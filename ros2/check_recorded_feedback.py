"""Check the JointState samples in a local ROS 2 bag without starting a ROS node."""

import argparse
import json
from pathlib import Path
from xml.etree.ElementTree import ParseError

from check_joint_feedback import FeedbackWindow, read_model


def inspect_bag(path, topic, window):
    import rosbag2_py
    from rclpy.serialization import deserialize_message
    from sensor_msgs.msg import JointState

    if not path.is_dir() or not (path / "metadata.yaml").is_file():
        raise ValueError(
            "Expected a local rosbag2 recording directory with metadata.yaml"
        )
    reader = rosbag2_py.SequentialReader()
    reader.open(
        rosbag2_py.StorageOptions(uri=str(path), storage_id=""),
        rosbag2_py.ConverterOptions("", ""),
    )
    try:
        matches = [
            item
            for item in reader.get_metadata().topics_with_message_count
            if item.topic_metadata.name == topic
        ]
        if (
            len(matches) != 1
            or matches[0].topic_metadata.type != "sensor_msgs/msg/JointState"
        ):
            raise ValueError(
                "The selected topic must be recorded as sensor_msgs/msg/JointState"
            )
        if matches[0].topic_metadata.serialization_format != "cdr":
            raise ValueError("Expected CDR-serialized JointState samples")
        reader.set_filter(rosbag2_py.StorageFilter(topics=[topic]))
        while reader.has_next():
            name, data, recorded_ns = reader.read_next()
            if name != topic:
                raise ValueError("The bag reader returned a different topic")
            message = deserialize_message(data, JointState)
            # A bag gives us the recorder's receive time, but no DDS writer identity.
            window.receive(message, None, recorded_ns, recorded_ns)
        if window.samples != matches[0].message_count:
            raise ValueError("Recorded sample count differs from the bag metadata")
        if not window.complete:
            raise ValueError("Recording has less than two seconds of valid feedback")
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bag", type=Path, required=True, help="Local rosbag2 recording directory"
    )
    parser.add_argument(
        "--urdf", type=Path, required=True, help="URDF used for this recording"
    )
    parser.add_argument(
        "--topic", required=True, help="Absolute JointState topic in the recording"
    )
    args = parser.parse_args()
    report = {
        "check": "recorded_joint_feedback",
        "topic": args.topic,
        "publisher_identity": "unavailable_in_recording",
        "physical_readiness": "unverified",
    }
    window = None
    try:
        if not args.topic.startswith("/"):
            raise ValueError("An absolute ROS topic is required")
        limits, digest = read_model(args.urdf)
        report.update(urdf_sha256=digest, expected_joints=len(limits))
        window = FeedbackWindow(limits)
        inspect_bag(args.bag, args.topic, window)
        report.update(
            status="recording_checked",
            recording_span_ms=(window.last_receipt - window.first_receipt) / 1_000_000,
            max_recorded_gap_ms=window.max_gap / 1_000_000,
            max_age_at_recording_ms=window.max_age / 1_000_000,
        )
    except ImportError:
        report.update(
            status="failed", reason="Source ROS 2 Jazzy with rosbag2_py and sensor_msgs"
        )
    except (ValueError, RuntimeError, OSError, ParseError) as exc:
        report.update(status="failed", reason=str(exc))
    except KeyboardInterrupt:
        report.update(status="interrupted")
    report["samples"] = window.samples if window else 0
    print(json.dumps(report, allow_nan=False))
    return {"recording_checked": 0, "failed": 1, "interrupted": 130}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
