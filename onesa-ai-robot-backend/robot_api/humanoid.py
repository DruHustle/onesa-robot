"""Full-body reference kinematics and a bounded simulated actuator interface.

Reference limits are synthetic, not motor ratings or a balanced walking policy.
No serial, CAN, DDS or physical actuator transport is opened by this module.
"""

import hashlib
import json
import secrets
from collections import deque
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from uuid import UUID
from xml.etree import ElementTree as ET

from pydantic import BaseModel, ConfigDict, Field

MODEL_ID: Literal["onesa-humanoid-33-v1"] = "onesa-humanoid-33-v1"
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Joint(Strict):
    name: str
    parent: str
    group: str
    axis: tuple[float, float, float]
    origin: tuple[float, float, float]
    lower: float
    upper: float
    velocity: float = 0.4
    acceleration: float = 0.6
    effort: float = 1.0


def reference_joints() -> tuple[Joint, ...]:
    result: list[Joint] = []

    def chain(group, parent, names, origins):
        for name, origin in zip(names, origins, strict=True):
            axis = (1.0, 0.0, 0.0) if "roll" in name else (0.0, 0.0, 1.0)
            if "pitch" in name or name in ("elbow", "knee", "grip"):
                axis = (0.0, 1.0, 0.0)
            joint = Joint(
                name=f"{group}_{name}",
                parent=parent,
                group=group,
                axis=axis,
                origin=origin,
                lower=0 if name in ("elbow", "knee", "grip") else -0.8,
                upper=1.2 if name in ("elbow", "knee", "grip") else 0.8,
            )
            result.append(joint)
            parent = joint.name

    zero = (0.0, 0.0, 0.0)
    chain("torso", "pelvis", ("yaw", "roll", "pitch"), [(0, 0, 0.15), zero, zero])
    chain("head", "torso_pitch", ("yaw", "pitch"), [(0, 0, 0.4), zero])
    for side, sign in (("left", 1), ("right", -1)):
        chain(
            f"{side}_arm",
            "torso_pitch",
            (
                "shoulder_pitch",
                "shoulder_roll",
                "shoulder_yaw",
                "elbow",
                "wrist_yaw",
                "wrist_pitch",
                "wrist_roll",
                "grip",
            ),
            [
                (0, sign * 0.22, 0.3),
                zero,
                zero,
                (0, 0, -0.28),
                (0, 0, -0.25),
                zero,
                zero,
                (0, 0, -0.08),
            ],
        )
        chain(
            f"{side}_leg",
            "pelvis",
            (
                "hip_yaw",
                "hip_roll",
                "hip_pitch",
                "knee",
                "ankle_pitch",
                "ankle_roll",
            ),
            [(0, sign * 0.1, 0), zero, zero, (0, 0, -0.38), (0, 0, -0.36), zero],
        )
    return tuple(result)


JOINTS = reference_joints()
BY_NAME = {joint.name: joint for joint in JOINTS}


class Model(Strict):
    model_id: Literal["onesa-humanoid-33-v1"] = MODEL_ID
    mode: Literal["simulation"] = "simulation"
    physical_actuation: Literal[False] = False
    joint_count: int = 33
    units: Literal["radians, seconds, metres; synthetic effort"] = (
        "radians, seconds, metres; synthetic effort"
    )
    joints: tuple[Joint, ...] = JOINTS
    limitations: tuple[str, ...] = (
        "Kinematics only: balance, contact dynamics and collision avoidance are not validated.",
        "Each hand has one coupled grip axis, not independently actuated fingers.",
        "Physical drives, motor ratings and emergency-stop hardware are not commissioned.",
    )


class MotionRequest(Strict):
    command_id: UUID
    issued_at: Number
    duration_seconds: Annotated[Number, Field(ge=2, le=30)]
    targets: dict[str, Number] = Field(min_length=1, max_length=33)


class Heartbeat(Strict):
    command_id: UUID


class MotionPlan(Strict):
    request: MotionRequest
    model_id: str = MODEL_ID
    revision: int
    start: dict[str, float]
    targets: dict[str, float]
    plan_sha256: str
    state: Literal["proposed", "running", "completed", "cancelled"] = "proposed"
    reason: str = "review_required"


class Observation(Strict):
    model_id: str = MODEL_ID
    mode: Literal["simulation"] = "simulation"
    physical_actuation: Literal[False] = False
    connection: Literal["simulated_actuators", "simulator_fault"] = "simulated_actuators"
    stopped: bool
    revision: int
    positions: dict[str, float]
    velocities: dict[str, float]
    active_command: str | None
    owns_active: bool
    pending: MotionPlan | None
    history: list[MotionPlan]
    sampled_at: float
    retention: Literal["bounded-process-memory; no restart replay"] = (
        "bounded-process-memory; no restart replay"
    )


class ActuatorBank(Protocol):
    """Synchronous simulation interface. A completed write is readable immediately."""

    physical_actuation: bool

    def read(self) -> dict[str, float]: ...
    def write(self, positions: dict[str, float]) -> None: ...
    def stop(self) -> None: ...


class ActuatorFault(RuntimeError):
    """The simulator cannot establish a trustworthy whole-body actuator state."""


def validate_frame(positions: dict[str, float]) -> None:
    if positions.keys() != BY_NAME.keys() or any(
        type(value) not in (int, float) or not BY_NAME[name].lower <= value <= BY_NAME[name].upper
        for name, value in positions.items()
    ):
        raise ValueError("Invalid actuator frame")


class SimulatedActuators:
    physical_actuation = False

    def __init__(self):
        self.positions = dict.fromkeys(BY_NAME, 0.0)

    def read(self) -> dict[str, float]:
        return self.positions.copy()

    def write(self, positions: dict[str, float]) -> None:
        # Validate the entire frame before changing any joint.
        validate_frame(positions)
        self.positions = positions.copy()

    def stop(self) -> None:
        # Holding a synthetic position is not a claim of hardware safe torque off.
        pass


@dataclass
class Running:
    plan: MotionPlan
    owner: str
    started: float
    heartbeat: float


class Humanoid:
    def __init__(self, bank: ActuatorBank | None = None):
        self.bank = bank or SimulatedActuators()
        if self.bank.physical_actuation:
            raise ValueError("Physical actuator commissioning is required")
        self.revision = 0
        self.pending: tuple[str, MotionPlan] | None = None
        self.active: Running | None = None
        self.history: deque[MotionPlan] = deque(maxlen=64)
        # Compact replay protection outlives the bounded detailed history.
        # Refuse new work at capacity instead of forgetting recent authority.
        self.commands: set[UUID] = set()
        self.faulted = False
        self.velocities = dict.fromkeys(BY_NAME, 0.0)

    def require_healthy(self) -> None:
        if self.faulted:
            raise ActuatorFault("Simulated actuator fault requires process recovery")

    def verified_positions(self) -> dict[str, float]:
        self.require_healthy()
        return self._actuator_frame()

    def _actuator_frame(self, commanded: dict[str, float] | None = None) -> dict[str, float]:
        try:
            if commanded is not None:
                validate_frame(commanded)
                self.bank.write(commanded.copy())
            feedback = self.bank.read().copy()
            validate_frame(feedback)
            if commanded is not None and feedback != commanded:
                raise ValueError("Incomplete simulated frame application")
            return feedback
        except Exception:
            if not self.faulted:
                self.faulted = True
                self.stop("actuator_failure")
            raise ActuatorFault("Simulated actuator feedback failed; command stopped") from None

    @staticmethod
    def fresh(request: MotionRequest, now: float) -> None:
        if not 0 <= now - request.issued_at <= 15:
            raise ValueError("Humanoid proposal expired or has a future timestamp")

    def propose(self, request: MotionRequest, owner: str, now: float) -> MotionPlan:
        self.fresh(request, now)
        if self.active or self.pending:
            raise ValueError("Cancel or complete the existing humanoid proposal first")
        if request.command_id in self.commands:
            raise ValueError("Humanoid commands cannot be replayed")
        if len(self.commands) >= 10000:
            raise ValueError("Humanoid command capacity reached; process recovery required")
        start = self.verified_positions()
        target = start.copy()
        for key, value in request.targets.items():
            joint = BY_NAME.get(key)
            if joint is None or not joint.lower <= value <= joint.upper:
                raise ValueError("Unknown joint or reference position limit exceeded")
            delta = abs(value - start[key])
            if (
                1.875 * delta / request.duration_seconds > joint.velocity
                or 5.774 * delta / request.duration_seconds**2 > joint.acceleration
            ):
                raise ValueError("Reference velocity or acceleration limit exceeded")
            target[key] = value
        payload = dict(
            request=request.model_dump(mode="json"),
            model_id=MODEL_ID,
            revision=self.revision,
            start=start,
            targets=target,
        )
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        plan = MotionPlan(**payload, plan_sha256=digest)
        self.commands.add(request.command_id)
        self.pending = owner, plan
        return plan

    def approve(self, identifier: UUID, owner: str, digest: str, now: float, monotonic: float):
        if self.pending is None or self.pending[0] != owner:
            raise ValueError("Humanoid proposal is unavailable to this session")
        plan = self.pending[1]
        self.fresh(plan.request, now)
        payload = plan.model_dump(mode="json", exclude={"plan_sha256", "state", "reason"})
        verified_digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        if (
            plan.request.command_id != identifier
            or plan.revision != self.revision
            or self.verified_positions() != plan.start
            or not secrets.compare_digest(plan.plan_sha256, digest)
            or not secrets.compare_digest(plan.plan_sha256, verified_digest)
        ):
            raise ValueError("Review the current humanoid plan before approval")
        plan = plan.model_copy(update={"state": "running", "reason": "operator_approved"})
        self.active = Running(plan, owner, monotonic, monotonic)
        self.pending = None
        self.revision += 1
        return plan

    def stop(self, reason: str):
        plan = self.active.plan if self.active else self.pending[1] if self.pending else None
        self.active = None
        self.pending = None
        self.velocities = dict.fromkeys(BY_NAME, 0.0)
        self.revision += 1
        try:
            self.bank.stop()
        except Exception:
            # Revocation and the gateway's Stop latch must survive a failed
            # adapter stop. A fault never becomes approval for another command.
            self.faulted = True
            reason = "actuator_failure"
        if plan:
            self.history.append(plan.model_copy(update={"state": "cancelled", "reason": reason}))

    def tick(self, monotonic: float):
        active = self.active
        if active is None:
            return
        plan = active.plan
        duration = plan.request.duration_seconds
        t = min(1.0, max(0.0, (monotonic - active.started) / duration))
        blend, rate = (
            10 * t**3 - 15 * t**4 + 6 * t**5,
            (30 * t**2 - 60 * t**3 + 30 * t**4) / duration,
        )
        positions = (
            plan.targets
            if t >= 1
            else {k: start + (plan.targets[k] - start) * blend for k, start in plan.start.items()}
        )
        self._actuator_frame(positions)
        self.velocities = {k: (plan.targets[k] - start) * rate for k, start in plan.start.items()}
        if t >= 1:
            self.history.append(
                plan.model_copy(update={"state": "completed", "reason": "target_reached"})
            )
            self.active = None

    def observe(self, owner: str, now: float, stopped: bool = True) -> Observation:
        return Observation(
            connection="simulator_fault" if self.faulted else "simulated_actuators",
            stopped=stopped,
            revision=self.revision,
            positions=self._actuator_frame(),
            velocities=self.velocities.copy(),
            active_command=str(self.active.plan.request.command_id) if self.active else None,
            owns_active=self.active is not None and self.active.owner == owner,
            pending=self.pending[1] if self.pending and self.pending[0] == owner else None,
            history=list(self.history),
            sampled_at=now,
        )


def urdf() -> str:
    """Export a connected reference tree; deliberately no fabricated inertial model."""
    robot = ET.Element("robot", name=MODEL_ID)
    ET.SubElement(robot, "link", name="pelvis")
    for spec in JOINTS:
        ET.SubElement(robot, "link", name=spec.name)
        joint = ET.SubElement(robot, "joint", name=spec.name, type="revolute")
        ET.SubElement(joint, "parent", link=spec.parent)
        ET.SubElement(joint, "child", link=spec.name)
        ET.SubElement(joint, "origin", xyz=" ".join(map(str, spec.origin)), rpy="0 0 0")
        ET.SubElement(joint, "axis", xyz=" ".join(map(str, spec.axis)))
        ET.SubElement(
            joint,
            "limit",
            lower=str(spec.lower),
            upper=str(spec.upper),
            velocity=str(spec.velocity),
            effort=str(spec.effort),
        )
    return ET.tostring(robot, encoding="unicode")
