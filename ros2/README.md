# ROS 2 feedback and simulation interfaces

## Observe joint feedback on a bench

On the bench computer with the feedback driver already running, use ROS 2 Jazzy,
`rclpy`, and `sensor_msgs`. Supply the expanded URDF for the assembled rig and the
driver's absolute `sensor_msgs/msg/JointState` feedback topic. For example:

```sh
. /opt/ros/jazzy/setup.sh
python3 ros2/check_joint_feedback.py --urdf /path/to/installed-rig.urdf --topic /joint_states
```

The probe requests localhost-only discovery and subscribes using sensor-data QoS.
The driver and probe must share a ROS domain and synchronized wall clocks.
It sends no actuator commands and does not connect to the operator API.
An exported simulator URDF is suitable for testing the harness. Use the
installed machine's engineering limits for a bench observation.

The probe follows every bounded revolute or prismatic joint in the supplied
model and ignores fixed joints. Each frame must contain exactly one finite
position per movable joint, in radians or metres as applicable, within the
supplied bounds. Joint order may vary. Optional velocity and effort arrays
must be empty or complete and finite. Other movable joint types fail the check.

Exit status 0 and `status: observed` require at least two seconds of continuous
valid feedback on both the host receipt clock and source timestamps. Source stamps
must increase, with zero to 500 ms of age and at most 500 ms between samples on
each clock. An invalid frame or a delivery gap ends the check. Once the subscription starts, discovery and
observation have a combined ten-second budget. The JSON output includes the
URDF SHA-256, joint and frame counts, and timing measurements. It does not
contain joint positions. Failures exit nonzero and give a reason.

One stable discoverable publisher must appear on the topic throughout sampling.
ROS 2 Jazzy graph metadata describes the endpoint visible during each callback.
It does not authenticate the source of each sample or distinguish encoder data
from a simulator or bag replay. Verify the driver, wiring, hardware limits,
calibration and independent stop separately. The report always marks physical
readiness as unverified.

## Check an existing recording

On a ROS 2 Jazzy workstation with `rosbag2_py`, run the offline check against a
local MCAP or SQLite3 rosbag2 directory containing `metadata.yaml`.

```sh
. /opt/ros/jazzy/setup.sh
python3 ros2/check_recorded_feedback.py --bag /path/to/bench-recording --urdf /path/to/installed-rig.urdf --topic /joint_states
```

The checker reads the selected topic directly from disk. It checks every recorded
frame, including frames after the first two valid seconds, and compares the number
read with the topic count in the bag metadata. Source age is measured against the
recorder's receive timestamp. The source and recorder must have used the same
clock domain. `status: recording_checked` covers the interval from the first to the
last recorded sample on that topic. Publisher identity, periods outside that
interval and the current state of the rig cannot be inferred from this report.
This command runs without a ROS node or DDS playback.

## Simulation message definitions

The `onesa_sim_interfaces` package defines synthetic telemetry and proposals.
Build it with `colcon build --packages-select onesa_sim_interfaces`, source the
workspace, and use `ros2 interface show onesa_sim_interfaces/msg/SimulationTelemetry`.
The manifest's contact/licensing entries need owner approval before distribution.

`SimulationProposal` is untrusted input. A future authenticated adapter must
pass the versioned HTTP proposal, independent safety gate, operator approval,
freshness, session and watchdog checks. ROS messages carry no action authority.
Keep ROS graph access restricted with SROS 2 permissions where implemented.

The isolated Jazzy CI entry point compiles the interfaces, exercises local
synthetic messaging and runs the passive observer against a synthetic
`JointState` publisher. It exercises both valid and invalid feedback.
