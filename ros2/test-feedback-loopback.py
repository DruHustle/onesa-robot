"""Exercise the observer CLI with a synthetic JointState topic inside isolated ROS CI."""

import json
import math
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import rclpy
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState


def check(publisher, topic, path, invalid):
    process = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("check_joint_feedback.py")),
            "--urdf",
            str(path),
            "--topic",
            topic,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 20
        while process.poll() is None and time.monotonic() < deadline:
            stamp = time.time_ns()
            message = JointState(
                name=["bench_axis"], position=[math.nan if invalid else 0.0]
            )
            message.header.stamp.sec, message.header.stamp.nanosec = divmod(
                stamp, 1_000_000_000
            )
            publisher.publish(message)
            time.sleep(0.05)
        stdout, stderr = process.communicate(timeout=2)
        assert process.returncode == int(invalid), (stdout, stderr)
        report = json.loads(stdout)
        assert report["physical_readiness"] == "unverified"
        if invalid:
            assert "Position outside" in report["reason"] and report["samples"] == 0, (
                report
            )
        else:
            assert report["status"] == "observed" and report["expected_joints"] == 1, (
                report
            )
            assert report["samples"] > 1 and report["observation_ms"] >= 2000, report
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


if __name__ == "__main__":
    rclpy.init(args=[])
    node = rclpy.create_node("onesa_feedback_fixture")
    try:
        topic = "/onesa/test/bench_joint_states"
        publisher = node.create_publisher(JointState, topic, qos_profile_sensor_data)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.urdf"
            path.write_text(
                '<robot name="test"><link name="base"/><link name="moving"/>'
                '<joint name="bench_axis" type="revolute"><parent link="base"/>'
                '<child link="moving"/><limit lower="-0.1" upper="0.1" effort="1" velocity="1"/>'
                "</joint></robot>"
            )
            check(publisher, topic, path, invalid=False)
            check(publisher, topic, path, invalid=True)
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
