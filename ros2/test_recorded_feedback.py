"""Exercise the offline CLI with real rosbag2 storage and synthetic CDR samples."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import rosbag2_py
from rclpy.serialization import serialize_message
from sensor_msgs.msg import JointState

TOPIC = "/rig/joint_states"


class RecordedFeedbackTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.urdf = self.root / "rig.urdf"
        self.urdf.write_text(
            '<robot name="rig"><joint type="revolute" name="axis">'
            '<limit lower="-1" upper="1"/></joint></robot>'
        )

    def fixture(self, storage, positions):
        bag = self.root / storage
        writer = rosbag2_py.SequentialWriter()
        writer.open(
            rosbag2_py.StorageOptions(uri=str(bag), storage_id=storage),
            rosbag2_py.ConverterOptions("", ""),
        )
        try:
            for i, topic in enumerate((TOPIC, "/other")):
                writer.create_topic(
                    rosbag2_py.TopicMetadata(
                        id=i + 1,
                        name=topic,
                        type="sensor_msgs/msg/JointState",
                        serialization_format="cdr",
                    )
                )
            for i, position in enumerate(positions):
                stamp = 10_000_000_000 + i * 250_000_000
                message = JointState(name=["axis"], position=[position])
                message.header.stamp.sec, message.header.stamp.nanosec = divmod(
                    stamp, 1_000_000_000
                )
                writer.write(TOPIC, serialize_message(message), stamp + 20_000_000)
                message.position[0] = 999.0
                writer.write("/other", serialize_message(message), stamp + 30_000_000)
        finally:
            writer.close()
        return bag

    def run_check(self, bag, topic=TOPIC):
        process = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).with_name("check_recorded_feedback.py")),
                "--bag",
                str(bag),
                "--topic",
                topic,
                "--urdf",
                str(self.urdf),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertTrue(process.stdout, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["physical_readiness"], "unverified")
        self.assertEqual(report["publisher_identity"], "unavailable_in_recording")
        return process.returncode, report

    def test_checks_all_selected_samples_for_both_storage_formats(self):
        for storage in ("mcap", "sqlite3"):
            with self.subTest(storage=storage):
                code, report = self.run_check(self.fixture(storage, [0.2] * 11))
                self.assertEqual(code, 0, report)
                self.assertEqual(report["status"], "recording_checked")
                self.assertEqual(report["samples"], 11)
                self.assertEqual(report["recording_span_ms"], 2500)
                self.assertEqual(report["max_age_at_recording_ms"], 20)

    def test_late_bad_frame_fails_even_after_valid_window(self):
        code, report = self.run_check(self.fixture("mcap", [0.2] * 10 + [1.1]))
        self.assertEqual(code, 1)
        self.assertEqual(report["samples"], 10)
        self.assertIn("Position outside", report["reason"])

    def test_missing_final_record_refuses_an_otherwise_complete_window(self):
        import sqlite3

        bag = self.fixture("sqlite3", [0.2] * 11)
        with sqlite3.connect(next(bag.glob("*.db3"))) as connection:
            connection.execute(
                "DELETE FROM messages WHERE id = (SELECT MAX(m.id) FROM messages m "
                "JOIN topics t ON m.topic_id = t.id WHERE t.name = ?)",
                (TOPIC,),
            )
        code, report = self.run_check(bag)
        self.assertEqual(code, 1)
        self.assertEqual(report["samples"], 10)
        self.assertIn("count differs", report["reason"])

    def test_short_recording_and_missing_topic(self):
        bag = self.fixture("sqlite3", [0.2] * 8)
        code, report = self.run_check(bag)
        self.assertEqual(code, 1)
        self.assertIn("less than two seconds", report["reason"])
        code, report = self.run_check(bag, "/not_recorded")
        self.assertEqual(code, 1)
        self.assertEqual(report["samples"], 0)
        self.assertIn("selected topic", report["reason"])


if __name__ == "__main__":
    unittest.main()
