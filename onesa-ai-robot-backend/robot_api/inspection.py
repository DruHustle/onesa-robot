"""Owned, deterministic pump-inspection plans and bounded review evidence.

This is a software scenario and a calculated synthetic joint trajectory. It is
not camera perception, validated rigid-body physics, a learned policy or hardware.
Records fit in the existing fenced task journal; no second execution authority
or schema is introduced.
"""

import hashlib
import json
import random
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

VERSION: Literal["1.0.0"] = "1.0.0"
RECORD_BYTES = 15000  # Leave room for the enclosing 16 KiB task proposal.
Metric = Literal["temperature", "pressure", "vibration"]
Scenario = Literal["normal", "threshold", "discrepancy", "interrupted", "missing", "stale", "units"]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=160)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-1e6, le=1e6)]
Instant = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=4133980800)]
Digest = Annotated[str, Field(strict=True, pattern=r"^[a-f0-9]{64}$")]


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Measure(Value):
    metric: Metric
    unit: Annotated[str, Field(strict=True, min_length=1, max_length=16)]
    minimum: Number
    maximum: Number
    discrepancy_tolerance: Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0)]
    stale_seconds: Annotated[int, Field(strict=True, ge=1, le=300)] = 30

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum >= self.maximum:
            raise ValueError("Inspection limits must be ordered")
        return self


class Observation(Value):
    metric: Metric
    value: Number | None
    unit: Annotated[str, Field(strict=True, min_length=1, max_length=16)]
    observed_at: Instant
    source_unit: Annotated[str, Field(strict=True, max_length=16)] = ""
    source: Literal["scenario_observation", "synthetic_iot", "reviewed_import"]
    source_ref: Text
    quality: Literal["good", "missing", "unknown"] = "good"
    declared_freshness: Annotated[str, Field(strict=True, max_length=24)] = "not_applicable"
    declared_connection: Annotated[str, Field(strict=True, max_length=24)] = "not_applicable"


class Asset(Value):
    id: Text
    name: Text
    operating_context: Text
    context_version: Literal["1.0.0"] = VERSION
    source: Text
    source_asset_id: Annotated[str, Field(strict=True, max_length=128)] = ""
    source_asset_name: Annotated[str, Field(strict=True, max_length=160)] = ""
    source_asset_revision: Annotated[int, Field(strict=True, ge=0, le=1000000000)] | None = None
    source_model_id: UUID | None = None
    source_sha256: Digest | None = None
    source_observed_at: Instant | None = None
    context_imported_at: Instant | None = None
    context_kind: Literal["synthetic", "reviewed_import"] = "synthetic"
    measures: tuple[Measure, ...] = Field(min_length=3, max_length=3)
    imported_telemetry: tuple[Observation, ...] = Field(default=(), max_length=3)


PUMP = Asset(
    id="pump-p-101",
    name="Pump P-101",
    operating_context="Illustrative cooling-water pump · steady-state inspection",
    source="onesa-robot.synthetic/pump-p-101/v1",
    measures=(
        Measure(
            metric="temperature", unit="°C", minimum=0.0, maximum=75.0, discrepancy_tolerance=2.0
        ),
        Measure(metric="pressure", unit="bar", minimum=2.0, maximum=6.0, discrepancy_tolerance=0.2),
        Measure(
            metric="vibration", unit="mm/s", minimum=0.0, maximum=4.5, discrepancy_tolerance=0.5
        ),
    ),
)


class RobotProfile(Value):
    id: Text
    version: Literal["1.0.0"] = VERSION
    name: Text
    adapter: Literal["synthetic_joint/v1", "display_only/v1"]
    capabilities: tuple[Text, ...] = Field(max_length=8)
    physical_connection: Literal["not_connected"] = "not_connected"


PROFILES = (
    RobotProfile(
        id="sim-inspector",
        name="Simulated inspection robot",
        adapter="synthetic_joint/v1",
        capabilities=("position_sensor", "observe_numeric", "compare_numeric", "report"),
    ),
    RobotProfile(
        id="display-only",
        name="Display-only profile · inspection unsupported",
        adapter="display_only/v1",
        capabilities=("report",),
    ),
)


class Skill(Value):
    id: Text
    version: Literal["1.0.0"] = VERSION
    name: Text
    required_capabilities: tuple[Text, ...] = Field(max_length=4)
    inputs: tuple[Text, ...] = Field(max_length=4)
    preconditions: tuple[Text, ...] = Field(max_length=4)
    operating_limits: Text
    timeout_seconds: Annotated[float, Field(strict=True, gt=0, le=10)]
    completion_condition: Text


SKILLS = (
    Skill(
        id="review_context",
        name="Verify inspection context",
        required_capabilities=(),
        inputs=("immutable asset snapshot", "plan digest"),
        preconditions=("current operator approval", "unchanged plan and profile"),
        operating_limits="Read-only context; no factory or device request",
        timeout_seconds=1.0,
        completion_condition="The approved context and capabilities match the current plan",
    ),
    Skill(
        id="position_sensor",
        name="Move to inspection position",
        required_capabilities=("position_sensor",),
        inputs=("synthetic joint target",),
        preconditions=("independent gate accepted", "current session heartbeat"),
        operating_limits="Synthetic joint: ±1 rad, 0.4 rad/s, 0.6 rad/s²",
        timeout_seconds=3.0,
        completion_condition="Calculated joint reaches the bounded inspection target",
    ),
    Skill(
        id="observe_numeric",
        name="Collect scenario observations",
        required_capabilities=("observe_numeric",),
        inputs=("versioned scenario and seed",),
        preconditions=("inspection position reached",),
        operating_limits="Three synthetic numeric observations; no camera, OCR or network",
        timeout_seconds=1.0,
        completion_condition="Available observations have source and time",
    ),
    Skill(
        id="compare_numeric",
        name="Compare readings and telemetry",
        required_capabilities=("compare_numeric",),
        inputs=("observations", "approved telemetry snapshot"),
        preconditions=("units and freshness assessed",),
        operating_limits="Versioned thresholds and absolute discrepancy tolerances",
        timeout_seconds=1.0,
        completion_condition="Each metric is assessed or explicitly unknown",
    ),
    Skill(
        id="report",
        name="Record inspection result",
        required_capabilities=("report",),
        inputs=("run evidence", "comparison findings"),
        preconditions=("owned run journal",),
        operating_limits="Bounded structured report and replay; no maintenance-system writes",
        timeout_seconds=1.0,
        completion_condition="Report links the outcome to the recorded run",
    ),
)


class Step(Value):
    skill_id: Text
    skill_version: Literal["1.0.0"] = VERSION
    completes_at_seconds: Annotated[float, Field(strict=True, ge=0, le=10)]


STEPS = (
    Step(skill_id="review_context", completes_at_seconds=0.5),
    Step(skill_id="position_sensor", completes_at_seconds=3.0),
    Step(skill_id="observe_numeric", completes_at_seconds=3.5),
    Step(skill_id="compare_numeric", completes_at_seconds=4.5),
    Step(skill_id="report", completes_at_seconds=6.0),
)


class Plan(Value):
    mission_id: UUID
    version: Literal["1.0.0"] = VERSION
    task: Literal["inspect_pump"] = "inspect_pump"
    title: Literal["Inspect Pump P-101 and report abnormal readings"]
    created_at: Instant
    scenario: Scenario
    scenario_version: Literal["1.0.0"] = VERSION
    seed: Annotated[int, Field(strict=True, ge=0, le=2147483647)]
    asset: Asset
    profile: RobotProfile
    skills_sha256: Digest
    start_radians: Number
    target_radians: Annotated[float, Field(strict=True, ge=0.1, le=0.1)] = 0.1
    motion_starts_at_seconds: Annotated[float, Field(strict=True, ge=1, le=1)] = 1.0
    motion_duration_seconds: Annotated[float, Field(strict=True, ge=2, le=2)] = 2.0
    duration_seconds: Annotated[float, Field(strict=True, ge=6, le=6)] = 6.0
    timeout_seconds: Annotated[float, Field(strict=True, ge=9, le=9)] = 9.0
    steps: tuple[Step, ...] = Field(min_length=5, max_length=5)
    observation_method: Literal["deterministic_scenario/v1"] = "deterministic_scenario/v1"
    physical_actuation: Literal[False] = False


class Event(Value):
    at: Instant
    elapsed_seconds: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=1e6)]
    kind: Literal[
        "planned",
        "approved",
        "execution_started",
        "step_completed",
        "completed",
        "interrupted",
        "failed",
    ]
    detail: Text
    actor: Annotated[str, Field(strict=True, max_length=64)] = "system"


class Finding(Value):
    metric: Metric
    kind: Literal["threshold", "discrepancy", "missing", "stale", "unit_mismatch"]
    rule_version: Literal["1.0.0"] = VERSION
    explanation: Annotated[str, Field(strict=True, min_length=1, max_length=400)]
    observation_index: Annotated[int, Field(strict=True, ge=0, le=5)]
    telemetry_index: Annotated[int, Field(strict=True, ge=0, le=5)] | None = None


class Record(Value):
    schema_version: Literal["onesa-robot-inspection/v1"] = "onesa-robot-inspection/v1"
    plan: Plan
    plan_sha256: Digest
    outcome: Literal[
        "awaiting_approval",
        "running",
        "completed_normal",
        "completed_findings",
        "interrupted",
        "failed",
    ] = "awaiting_approval"
    started_at: Instant | None = None
    elapsed_seconds: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0, le=1e6)] = 0.0
    events: tuple[Event, ...] = Field(default=(), max_length=16)
    observations: tuple[Observation, ...] = Field(default=(), max_length=3)
    telemetry: tuple[Observation, ...] = Field(default=(), max_length=3)
    findings: tuple[Finding, ...] = Field(default=(), max_length=9)


class MissionRequest(Value):
    command_id: UUID
    asset_id: Literal["pump-p-101"] = "pump-p-101"
    robot_profile_id: Annotated[str, Field(strict=True, min_length=1, max_length=64)]
    scenario: Scenario
    seed: Annotated[int, Field(strict=True, ge=0, le=2147483647)] = 101
    context_id: UUID | None = None


class ContextRequest(Value):
    # File bytes are hashed and parsed on the server. No URL fetch, credential,
    # executable model content or caller-declared authorization is accepted.
    document: Annotated[str, Field(strict=True, min_length=2, max_length=240000)]
    mapping: dict[Metric, Annotated[str, Field(strict=True, min_length=1, max_length=64)]]


def import_context(request: ContextRequest, clock: float) -> Asset:
    if (
        set(request.mapping) != {m.metric for m in PUMP.measures}
        or len(set(request.mapping.values())) != 3
    ):
        raise ValueError("Map three distinct exported tags to temperature, pressure and vibration")
    raw = request.document
    # Bound nesting before json.loads, including strings with escaped brackets.
    depth, quoted, escaped = 0, False, False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
            if depth > 16:
                raise ValueError("Context exceeds the supported nesting profile")
        elif char in "]}":
            depth -= 1

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate context key")
            result[key] = value
        return result

    try:
        data = json.loads(
            raw,
            object_pairs_hook=unique,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite context number")),
        )
        if type(data.get("contract_version")) is not int or data["contract_version"] != 1:
            raise ValueError("Expected the AI Platform robotics/v1 export")
        caps = data["capabilities"]
        if caps["read_only"] is not True or caps["actuation"] is not False:
            raise ValueError("Only a read-only robotics context is supported")
        asset_id = str(UUID(data["asset"]["id"]))
        state = data["operating_state"]
        if not isinstance(state, list) or len(state) > 100:
            raise ValueError("Export exceeds the supported binding profile")
        readings = []
        for measure in PUMP.measures:
            tag = request.mapping[measure.metric]
            matched = [s for s in state if s["binding"]["tag"] == tag]
            if len(matched) != 1:
                raise ValueError("Each mapped tag must identify exactly one exported source")
            source = matched[0]
            reading = source.get("reading")
            original_unit = source["binding"]["unit"]
            unit = (
                "°C"
                if measure.metric == "temperature" and original_unit == "Cel"
                else original_unit
            )
            if unit != measure.unit or (reading and reading["unit"] != original_unit):
                raise ValueError("Mapped context units differ from the P-101 reference profile")
            source_id = str(UUID(source["source_id"]))
            if reading and reading["mode"] != "live":
                raise ValueError("Do not relabel synthetic readings as live context")
            instant = datetime.fromisoformat(reading["observed_at"]) if reading else None
            if instant is not None and instant.tzinfo is None:
                raise ValueError("Context timestamp must include a timezone")
            observed_at = instant.timestamp() if instant else clock
            if observed_at > clock + 1:
                raise ValueError("Context timestamp is in the future")
            # Declared quality can only reduce confidence, never establish trust.
            good = (
                bool(reading)
                and source["freshness"] == "good"
                and source["connection"] == "connected"
            )
            readings.append(
                Observation(
                    metric=measure.metric,
                    value=reading["value"] if reading else None,
                    unit=unit,
                    source_unit=original_unit,
                    observed_at=observed_at,
                    source="reviewed_import",
                    source_ref=f"ai-robotics/v1/{source_id}/{tag}",
                    quality="good" if good else "unknown",
                    declared_freshness=source["freshness"],
                    declared_connection=source["connection"],
                )
            )
        snapshot = PUMP.model_copy(
            update={
                "source": "AI Platform robotics/v1 file; untrusted operator-reviewed export",
                "source_asset_id": asset_id,
                "source_asset_name": data["asset"]["name"],
                "source_asset_revision": data["asset"].get("revision"),
                "source_model_id": UUID(data["model_revision"]["id"])
                if data.get("model_revision")
                else None,
                "source_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "source_observed_at": max(
                    (r.observed_at for r in readings if r.value is not None), default=None
                ),
                "context_imported_at": clock,
                "context_kind": "reviewed_import",
                "imported_telemetry": tuple(readings),
            }
        )
        return Asset.model_validate(snapshot.model_dump())
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError):
        raise ValueError(
            "Invalid context or mapping; use bounded robotics/v1 data with P-101 units "
            "and timezone-qualified source timestamps"
        ) from None


def digest(value: BaseModel | list) -> str:
    raw = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return hashlib.sha256(
        json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def skill_digest() -> str:
    return digest([skill.model_dump(mode="json") for skill in SKILLS])


def make_record(request: MissionRequest, start: float, clock: float, asset: Asset = PUMP) -> Record:
    profile = next((p for p in PROFILES if p.id == request.robot_profile_id), None)
    required = {c for skill in SKILLS for c in skill.required_capabilities}
    if profile is None or not required <= set(profile.capabilities):
        raise ValueError("Robot profile does not support the required inspection capabilities")
    plan = Plan(
        mission_id=request.command_id,
        title="Inspect Pump P-101 and report abnormal readings",
        created_at=clock,
        scenario=request.scenario,
        seed=request.seed,
        asset=asset,
        profile=profile,
        skills_sha256=skill_digest(),
        start_radians=start,
        steps=STEPS,
    )
    return bounded(
        Record(
            plan=plan,
            plan_sha256=digest(plan),
            events=(
                Event(
                    at=clock, elapsed_seconds=0.0, kind="planned", detail="Awaiting operator review"
                ),
            ),
        )
    )


def validate(
    record: Record, identifier: UUID, start: float, target: float, duration: float
) -> None:
    plan = record.plan
    profile = next((p for p in PROFILES if p.id == plan.profile.id), None)
    if profile is None:
        raise ValueError("Robot profile is no longer supported")
    if (
        plan.mission_id != identifier
        or record.plan_sha256 != digest(plan)
        or profile != plan.profile
        or plan.skills_sha256 != skill_digest()
        or plan.steps != STEPS
        or plan.target_radians != target
        or plan.duration_seconds != duration
        or abs(plan.start_radians - start) > 1e-12
        or plan.asset.measures != PUMP.measures
        or (plan.asset.context_kind == "synthetic" and plan.asset != PUMP)
    ):
        raise ValueError("Plan, capabilities, asset context or execution conditions changed")
    if profile.adapter != "synthetic_joint/v1":
        raise ValueError("Only the configured software simulator can execute this inspection")
    bounded(record)


def bounded(record: Record) -> Record:
    if len(record.model_dump_json().encode()) > RECORD_BYTES:
        raise ValueError("Inspection evidence exceeds the bounded task journal profile")
    return record


def approve(
    record: Record,
    clock: float,
    actor: str,
    *,
    authentication_method: Literal["password", "onesa_mfa"] = "password",
) -> Record:
    if record.outcome != "awaiting_approval":
        raise ValueError("Inspection cannot be approved or replayed again")
    return bounded(
        record.model_copy(
            update={
                "outcome": "running",
                "events": (
                    *record.events,
                    Event(
                        at=clock,
                        elapsed_seconds=0.0,
                        kind="approved",
                        detail=(
                            "Onesa MFA and explicit exact-plan approval"
                            if authentication_method == "onesa_mfa"
                            else "Password-confirmed exact-plan approval"
                        ),
                        actor=actor,
                    ),
                ),
            }
        )
    )


def scenario_readings(
    plan: Plan, started_at: float | None = None
) -> tuple[tuple[Observation, ...], tuple[Observation, ...]]:
    source_clock = started_at if started_at is not None else plan.created_at
    noise = round(random.Random(plan.seed).uniform(-0.02, 0.02), 4)
    values = {"temperature": 46.0 + noise, "pressure": 3.2 + noise, "vibration": 2.1 + noise}
    if plan.scenario == "threshold":
        values["temperature"] = 86.0 + noise
    telemetry_values = dict(values)
    if plan.scenario == "discrepancy":
        values["pressure"], telemetry_values["pressure"] = 4.2 + noise, 2.7 + noise
    observations, telemetry = [], []
    for measure in plan.asset.measures:
        metric = measure.metric
        missing = plan.scenario == "missing" and metric == "vibration"
        observations.append(
            Observation(
                metric=metric,
                value=None if missing else values[metric],
                unit="rpm" if plan.scenario == "units" and metric == "pressure" else measure.unit,
                observed_at=source_clock + 3.5,
                source="scenario_observation",
                source_ref=f"scenario/{plan.scenario}/v1/{plan.seed}",
                quality="missing" if missing else "good",
            )
        )
        telemetry.append(
            Observation(
                metric=metric,
                value=telemetry_values[metric],
                unit=measure.unit,
                observed_at=source_clock - (120.0 if plan.scenario == "stale" else 0.0),
                source="synthetic_iot",
                source_ref=f"synthetic-iot/p-101/v1/{plan.seed}",
            )
        )
    return tuple(observations), plan.asset.imported_telemetry or tuple(telemetry)


def evaluate(record: Record, clock: float) -> tuple[Finding, ...]:
    findings = []
    for index, measure in enumerate(record.plan.asset.measures):
        observed, remote = record.observations[index], record.telemetry[index]
        common = {"metric": measure.metric, "observation_index": index}
        if observed.value is None or observed.quality != "good":
            findings.append(
                Finding(
                    **common,
                    kind="missing",
                    explanation="Observation is missing; this metric was not assessed",
                )
            )
            continue
        if observed.unit != measure.unit or remote.unit != measure.unit:
            findings.append(
                Finding(
                    **common,
                    kind="unit_mismatch",
                    telemetry_index=index,
                    explanation=f"Expected {measure.unit}; incompatible units prevent comparison",
                )
            )
            continue
        if clock - observed.observed_at > measure.stale_seconds:
            findings.append(
                Finding(
                    **common,
                    kind="stale",
                    explanation="Observation exceeds the configured freshness limit",
                )
            )
            continue
        if not measure.minimum <= observed.value <= measure.maximum:
            findings.append(
                Finding(
                    **common,
                    kind="threshold",
                    explanation=(
                        f"Observed {observed.value:g} {measure.unit}; reference inspection range "
                        f"{measure.minimum:g}–{measure.maximum:g} {measure.unit}"
                    ),
                )
            )
        if remote.value is None or (
            remote.quality != "good" and remote.declared_freshness != "stale"
        ):
            findings.append(
                Finding(
                    **common,
                    kind="missing",
                    telemetry_index=index,
                    explanation="Telemetry is unavailable; agreement could not be assessed",
                )
            )
        elif (
            remote.declared_freshness == "stale"
            or clock - remote.observed_at > measure.stale_seconds
        ):
            findings.append(
                Finding(
                    **common,
                    kind="stale",
                    telemetry_index=index,
                    explanation="Telemetry is stale; it cannot confirm the observation",
                )
            )
        elif abs(observed.value - remote.value) > measure.discrepancy_tolerance:
            findings.append(
                Finding(
                    **common,
                    kind="discrepancy",
                    telemetry_index=index,
                    explanation=(
                        f"Observation and telemetry differ by "
                        f"{abs(observed.value - remote.value):g} {measure.unit}; "
                        f"tolerance {measure.discrepancy_tolerance:g} {measure.unit}"
                    ),
                )
            )
    return tuple(findings)


def advance(record: Record, elapsed: float, clock: float) -> tuple[Record, str | None]:
    if record.outcome != "running":
        return record, None
    if elapsed > record.plan.timeout_seconds:
        return record, "mission_timeout"
    completed = {e.detail for e in record.events if e.kind == "step_completed"}
    events = list(record.events)
    observations, telemetry, findings = record.observations, record.telemetry, record.findings
    for step in record.plan.steps:
        cutoff = min(elapsed, 3.5) if record.plan.scenario == "interrupted" else elapsed
        if cutoff < step.completes_at_seconds or step.skill_id in completed:
            continue
        if step.skill_id == "observe_numeric":
            observations, telemetry = scenario_readings(record.plan, record.started_at)
        if step.skill_id == "compare_numeric":
            partial = record.model_copy(
                update={"observations": observations, "telemetry": telemetry}
            )
            findings = evaluate(partial, clock)
        events.append(
            Event(at=clock, elapsed_seconds=elapsed, kind="step_completed", detail=step.skill_id)
        )
    updated = record.model_copy(
        update={
            "events": tuple(events),
            "observations": observations,
            "telemetry": telemetry,
            "findings": findings,
            "elapsed_seconds": elapsed,
        }
    )
    if record.plan.scenario == "interrupted" and elapsed >= 3.5:
        return bounded(updated), "scenario_interrupted"
    if elapsed >= record.plan.duration_seconds:
        updated = updated.model_copy(
            update={
                "outcome": "completed_findings" if findings else "completed_normal",
                "events": (
                    *updated.events,
                    Event(
                        at=clock,
                        elapsed_seconds=elapsed,
                        kind="completed",
                        detail="Inspection completed with findings"
                        if findings
                        else "Inspection completed",
                    ),
                ),
            }
        )
    return bounded(updated), None


def interrupt(record: Record, reason: str, clock: float, elapsed: float = 0.0) -> Record:
    if record.outcome not in ("awaiting_approval", "running"):
        return record
    failure = reason in ("safety_gate", "mission_timeout", "inspection_integrity")
    return bounded(
        record.model_copy(
            update={
                "outcome": "failed" if failure else "interrupted",
                "elapsed_seconds": elapsed,
                "events": (
                    *record.events,
                    Event(
                        at=clock,
                        elapsed_seconds=elapsed,
                        kind="failed" if failure else "interrupted",
                        detail=reason,
                    ),
                ),
            }
        )
    )


def report(record: Record) -> dict:
    expected, _ = scenario_readings(record.plan)
    correct = sum(
        observed.value is not None
        and observed.unit == reference.unit
        and reference.value is not None
        and abs(observed.value - reference.value) < 1e-9
        for observed, reference in zip(record.observations, expected, strict=False)
    )
    terminal = record.outcome not in ("awaiting_approval", "running")
    return {
        "run_id": str(record.plan.mission_id),
        "plan_sha256": record.plan_sha256,
        "asset": record.plan.asset.model_dump(mode="json"),
        "outcome": record.outcome,
        "execution_complete": record.outcome in ("completed_normal", "completed_findings"),
        "findings": [f.model_dump(mode="json") for f in record.findings],
        "observations": [o.model_dump(mode="json") for o in record.observations],
        "telemetry": [o.model_dump(mode="json") for o in record.telemetry],
        "metrics": {
            "scope": "synthetic scenario evaluation",
            "elapsed_seconds": record.elapsed_seconds,
            "assessed_observations": sum(o.value is not None for o in record.observations),
            "expected_observations": 3,
            "correct_against_scenario": correct,
            "accuracy_against_scenario": correct / 3 if record.observations else None,
            "interventions": sum(e.kind in ("interrupted", "failed") for e in record.events),
            "recovery": "explicit_new_run_required"
            if terminal and not record.outcome.startswith("completed_")
            else "not_required"
            if terminal
            else "not_yet_assessed",
        },
        "limitations": [
            "Reference limits and scenario readings are synthetic; "
            "they are not factory acceptance limits",
            "Observations are scenario values, not camera recognition, OCR or model inference",
            "Calculated synthetic motion and visual animation are not validated physics simulation",
            "No physical controller, autonomous remediation or external maintenance write",
            *(
                [
                    "Imported telemetry is an untrusted exported snapshot, "
                    "not verified live factory evidence"
                ]
                if record.plan.asset.context_kind == "reviewed_import"
                else []
            ),
        ],
        "replay": {
            "read_only": True,
            "motion_model": "quintic_synthetic_joint/v1",
            "events": [event.model_dump(mode="json") for event in record.events],
        },
    }
