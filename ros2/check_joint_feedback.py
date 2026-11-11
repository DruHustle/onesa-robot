"""Observe a local JointState stream against an explicitly supplied bench URDF."""

import argparse
import hashlib
import json
import math
import os
import time
from pathlib import Path
from xml.etree import ElementTree

NANOSECONDS = 1_000_000_000
WINDOW_NS = 2 * NANOSECONDS
MAX_GAP_NS = NANOSECONDS // 2
MAX_AGE_NS = NANOSECONDS // 2
TIMEOUT_NS = 10 * NANOSECONDS
MAX_URDF_BYTES = 1024 * 1024


def read_model(path):
    with Path(path).open("rb") as source:
        contents = source.read(MAX_URDF_BYTES + 1)
    if len(contents) > MAX_URDF_BYTES:
        raise ValueError("URDF exceeds the 1 MiB limit")
    root = ElementTree.fromstring(contents)
    if root.tag != "robot":
        raise ValueError("Expected a URDF robot")
    limits = {}
    seen = set()
    for joint in root.findall("joint"):
        name = joint.get("name")
        if not name or name in seen:
            raise ValueError("URDF joint names must be nonempty and unique")
        seen.add(name)
        if joint.get("type") == "fixed":
            continue
        if joint.get("type") not in {"revolute", "prismatic"}:
            raise ValueError(
                f"Unsupported joint type for {name}: bounded joints required"
            )
        limit = joint.find("limit")
        if limit is None or "lower" not in limit.attrib or "upper" not in limit.attrib:
            raise ValueError(f"Missing position limits for {name}")
        lower, upper = (float(limit.attrib[key]) for key in ("lower", "upper"))
        if not (math.isfinite(lower) and math.isfinite(upper) and lower < upper):
            raise ValueError(f"Invalid position limits for {name}")
        limits[name] = lower, upper
    if not limits:
        raise ValueError("URDF contains no bounded movable joints")
    return limits, hashlib.sha256(contents).hexdigest()


class FeedbackWindow:
    def __init__(self, limits):
        self.limits = limits
        self.samples = 0
        self.first_receipt = self.last_receipt = None
        self.first_stamp = self.last_stamp = None
        self.publisher = None
        self.max_gap = 0
        self.max_age = 0

    def check_gap(self, now):
        if self.last_receipt is None:
            return
        if now < self.last_receipt:
            raise ValueError("Feedback receipt clock regressed")
        if now - self.last_receipt > MAX_GAP_NS:
            raise ValueError("Feedback delivery gap exceeded 500 ms")

    def receive(self, message, publisher, received, wall):
        self.check_gap(received)
        stamp = message.header.stamp
        if stamp.sec < 0 or not 0 <= stamp.nanosec < NANOSECONDS:
            raise ValueError("Invalid JointState header timestamp")
        timestamp = stamp.sec * NANOSECONDS + stamp.nanosec
        age = wall - timestamp
        if timestamp == 0 or not 0 <= age <= MAX_AGE_NS:
            raise ValueError(
                "Feedback timestamp is stale, missing or ahead of this host"
            )
        if self.last_stamp is not None:
            if publisher != self.publisher:
                raise ValueError("Feedback publisher changed during observation")
            source_delta = timestamp - self.last_stamp
            if not 0 < source_delta <= MAX_GAP_NS:
                raise ValueError(
                    "Feedback source timestamp repeated, regressed or skipped"
                )
        names = message.name
        if len(names) != len(self.limits) or set(names) != self.limits.keys():
            raise ValueError(
                "JointState names must match all movable URDF joints exactly once"
            )
        if len(message.position) != len(names):
            raise ValueError("JointState requires one measured position per joint")
        for name, position in zip(names, message.position, strict=True):
            lower, upper = self.limits[name]
            if not math.isfinite(position) or not lower <= position <= upper:
                raise ValueError(f"Position outside supplied URDF limits for {name}")
        for values in (message.velocity, message.effort):
            if len(values) and (
                len(values) != len(names) or not all(map(math.isfinite, values))
            ):
                raise ValueError(
                    "Optional JointState velocity/effort must be complete and finite"
                )

        if self.first_receipt is None:
            self.first_receipt, self.first_stamp = received, timestamp
            self.publisher = publisher
        else:
            self.max_gap = max(self.max_gap, received - self.last_receipt)
        self.last_stamp, self.last_receipt = timestamp, received
        self.max_age = max(self.max_age, age)
        self.samples += 1

    @property
    def complete(self):
        return self.samples > 1 and (
            self.last_receipt - self.first_receipt >= WINDOW_NS
            and self.last_stamp - self.first_stamp >= WINDOW_NS
        )


def observe(window, topic):
    # Run alongside the device driver. Do not target a remote DDS graph.
    os.environ["ROS_LOCALHOST_ONLY"] = "1"
    try:
        import rclpy
        from rclpy.exceptions import NameValidationException
        from rclpy.executors import ExternalShutdownException
        from rclpy.qos import qos_profile_sensor_data
        from sensor_msgs.msg import JointState
    except ImportError as exc:
        raise RuntimeError(
            "Source ROS 2 Jazzy with rclpy and sensor_msgs installed"
        ) from exc

    rclpy.init(args=[])
    node = None
    try:
        node = rclpy.create_node(
            "onesa_joint_feedback_check",
            enable_rosout=False,
            start_parameter_services=False,
            use_global_arguments=False,
        )

        def receive(message):
            publishers = node.get_publishers_info_by_topic(topic)
            if len(publishers) != 1:
                raise ValueError(
                    "Expected exactly one discoverable JointState publisher"
                )
            window.receive(
                message,
                bytes(publishers[0].endpoint_gid),
                time.monotonic_ns(),
                time.time_ns(),
            )

        node.create_subscription(JointState, topic, receive, qos_profile_sensor_data)
        deadline = time.monotonic_ns() + TIMEOUT_NS
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0.05)
            now = time.monotonic_ns()
            if now >= deadline:
                raise ValueError(
                    "Complete feedback window was not observed within 10 seconds"
                )
            window.check_gap(now)
            if window.complete:
                return
        raise KeyboardInterrupt
    except NameValidationException as exc:
        raise ValueError("Invalid ROS feedback topic name") from exc
    except ExternalShutdownException as exc:
        raise KeyboardInterrupt from exc
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.try_shutdown()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--urdf", type=Path, required=True, help="Installed rig or test fixture URDF"
    )
    parser.add_argument(
        "--topic", required=True, help="Absolute JointState feedback topic"
    )
    args = parser.parse_args()
    report = {
        "check": "joint_feedback",
        "topic": args.topic,
        "publisher_check": "discovery_graph_only",
        "physical_readiness": "unverified",
    }
    window = None
    try:
        if not args.topic.startswith("/"):
            raise ValueError("An absolute ROS topic is required")
        limits, digest = read_model(args.urdf)
        report.update(urdf_sha256=digest, expected_joints=len(limits))
        window = FeedbackWindow(limits)
        observe(window, args.topic)
        report.update(
            status="observed",
            observation_ms=(window.last_receipt - window.first_receipt) / 1_000_000,
            max_receipt_gap_ms=window.max_gap / 1_000_000,
            max_source_age_ms=window.max_age / 1_000_000,
        )
    except (ValueError, OSError, RuntimeError, ElementTree.ParseError) as exc:
        report.update(status="failed", reason=str(exc))
    except KeyboardInterrupt:
        report.update(status="interrupted")
    report["samples"] = window.samples if window else 0
    print(json.dumps(report, allow_nan=False))
    return {"observed": 0, "failed": 1, "interrupted": 130}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
