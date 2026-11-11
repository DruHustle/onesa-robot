import time
from uuid import uuid4
from xml.etree import ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from robot_api.humanoid import (
    BY_NAME,
    JOINTS,
    ActuatorFault,
    Humanoid,
    MotionRequest,
    SimulatedActuators,
    urdf,
)
from robot_api.main import Settings, create_app
from robot_api.security import password_hash

PASSWORD = "humanoid-test-only-operator-password"
HASH = password_hash(PASSWORD)


@pytest.fixture
def api(tmp_path):
    config = Settings(operator_password_hash=HASH, database_path=tmp_path / "robot.sqlite3")
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        yield client


def sign_in(api):
    value = api.post("/api/v1/auth/login", json={"name": "operator", "password": PASSWORD})
    assert value.status_code == 200
    api.headers["X-CSRF-Token"] = value.json()["csrf"]
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 200


def request(**changes):
    return dict(
        command_id=str(uuid4()),
        issued_at=time.time(),
        duration_seconds=3.0,
        targets={
            "left_arm_elbow": 0.2,
            "right_arm_elbow": 0.2,
            "left_leg_knee": 0.1,
            "right_leg_knee": 0.1,
        },
        **changes,
    )


def test_reference_is_a_connected_full_body_33_actuator_tree():
    assert len(JOINTS) == len(BY_NAME) == 33
    links = {"pelvis"}
    for joint in JOINTS:
        assert joint.parent in links
        assert joint.lower <= 0 <= joint.upper
        assert sum(x * x for x in joint.axis) == 1
        links.add(joint.name)
    assert {j.group for j in JOINTS} == {
        "head",
        "torso",
        "left_arm",
        "right_arm",
        "left_leg",
        "right_leg",
    }
    tree = ET.fromstring(urdf())
    assert len(tree.findall("joint")) == 33
    assert len(tree.findall("link")) == 34
    assert {j.attrib["name"] for j in tree.findall("joint")} == set(BY_NAME)
    assert not tree.findall(".//inertial")  # No invented rigid-body dynamics.


def test_coordinated_actuators_reach_target_atomically_without_replay():
    robot = Humanoid()
    value = MotionRequest.model_validate(request())
    plan = robot.propose(value, "session", value.issued_at)
    robot.approve(value.command_id, "session", plan.plan_sha256, value.issued_at, 10)
    robot.tick(11.5)
    positions = robot.bank.read()
    assert positions["left_arm_elbow"] == pytest.approx(0.1)
    assert positions["right_arm_elbow"] == pytest.approx(0.1)
    assert positions["left_leg_knee"] == pytest.approx(0.05)
    assert all(abs(v) <= BY_NAME[k].velocity for k, v in robot.velocities.items())
    robot.tick(13)
    assert robot.bank.read()["left_arm_elbow"] == 0.2
    assert robot.history[-1].state == "completed"
    with pytest.raises(ValueError, match="replayed"):
        robot.propose(value, "session", value.issued_at)
    before = robot.bank.read()
    with pytest.raises(ValueError, match="frame"):
        robot.bank.write({**before, "head_yaw": 200})
    assert robot.bank.read() == before


def test_physical_bank_and_driver_failure_cannot_leave_command_running():
    bank = SimulatedActuators()
    bank.physical_actuation = True
    with pytest.raises(ValueError, match="commissioning"):
        Humanoid(bank)
    bank.physical_actuation = False
    robot = Humanoid(bank)
    value = MotionRequest.model_validate(request())
    plan = robot.propose(value, "session", value.issued_at)
    robot.approve(value.command_id, "session", plan.plan_sha256, value.issued_at, 10)

    def fail(_positions):
        raise OSError("private driver details")

    bank.write = fail
    with pytest.raises(ActuatorFault, match="stopped") as error:
        robot.tick(11)
    assert "private" not in str(error.value)
    assert robot.active is None
    assert robot.history[-1].reason == "actuator_failure"


def test_authenticated_api_approval_and_stop_cover_whole_body(api):
    assert api.get("/api/v1/humanoid/model").status_code == 401
    sign_in(api)
    assert api.get("/api/v1/humanoid/model").json()["joint_count"] == 33
    assert api.get("/api/v1/humanoid/model.urdf").status_code == 200
    created = api.post("/api/v1/humanoid/proposals", json=request())
    assert created.status_code == 201, created.text
    plan = created.json()
    path = f"/api/v1/humanoid/proposals/{plan['request']['command_id']}/approve"
    assert (
        api.post(path, json={"password": PASSWORD, "expected_plan_sha256": "0" * 64}).status_code
        == 409
    )
    approval = {"password": PASSWORD, "expected_plan_sha256": plan["plan_sha256"]}
    assert api.post(path, json=approval).status_code == 200
    assert api.post(path, json=approval).status_code == 409
    assert api.get("/api/v1/humanoid/state").json()["active_command"]
    assert api.post("/api/v1/stop", json={}).status_code == 200
    observed = api.get("/api/v1/humanoid/state").json()
    assert observed["active_command"] is None
    assert observed["physical_actuation"] is False
    assert observed["history"][-1]["state"] == "cancelled"


@pytest.mark.parametrize("target", [{"unknown": 0}, {"head_yaw": 2}, {"head_yaw": True}])
def test_invalid_joint_commands_never_change_positions(api, target):
    sign_in(api)
    body = request()
    body["targets"] = target
    assert api.post("/api/v1/humanoid/proposals", json=body).status_code == 422
    assert all(v == 0 for v in api.get("/api/v1/humanoid/state").json()["positions"].values())


@pytest.mark.parametrize("cause", ["heartbeat", "logout", "expired_session"])
def test_lost_authority_stops_all_actuators(api, cause):
    sign_in(api)
    plan = api.post("/api/v1/humanoid/proposals", json=request()).json()
    path = f"/api/v1/humanoid/proposals/{plan['request']['command_id']}/approve"
    assert (
        api.post(
            path, json={"password": PASSWORD, "expected_plan_sha256": plan["plan_sha256"]}
        ).status_code
        == 200
    )

    def expire():
        state = api.app.state.sim
        if cause == "heartbeat":
            state.humanoid.active.heartbeat -= 10
        else:
            state.db.execute("UPDATE sessions SET expires=0")
            state.db.commit()
        state.tick()

    if cause == "logout":
        assert api.post("/api/v1/auth/logout", json={}).status_code == 200
    else:
        api.portal.call(expire)
    assert api.portal.call(lambda: api.app.state.sim.humanoid.active) is None
    assert api.portal.call(lambda: api.app.state.sim.stopped)


def test_proposal_cannot_cross_sessions_or_survive_restart(api):
    sign_in(api)
    plan = api.post("/api/v1/humanoid/proposals", json=request()).json()
    second = api.post("/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}).json()
    api.headers["X-CSRF-Token"] = second["csrf"]
    path = f"/api/v1/humanoid/proposals/{plan['request']['command_id']}/approve"
    assert (
        api.post(
            path, json={"password": PASSWORD, "expected_plan_sha256": plan["plan_sha256"]}
        ).status_code
        == 409
    )
    fresh = Humanoid()
    assert fresh.pending is None and fresh.active is None
    assert all(v == 0 for v in fresh.bank.read().values())


def start_motion(api):
    plan = api.post("/api/v1/humanoid/proposals", json=request()).json()
    result = api.post(
        f"/api/v1/humanoid/proposals/{plan['request']['command_id']}/approve",
        json={"password": PASSWORD, "expected_plan_sha256": plan["plan_sha256"]},
    )
    assert result.status_code == 200, result.text
    return plan["request"]["command_id"]


@pytest.mark.parametrize("target", [0.5, 0.42])
def test_peak_velocity_and_acceleration_are_checked_before_any_motion(target):
    # At two seconds .5 exceeds peak velocity; .42 stays below that velocity
    # limit but exceeds peak acceleration. Both are legal reference positions.
    robot = Humanoid()
    body = request()
    body.update(targets={"head_yaw": target}, duration_seconds=2.0)
    with pytest.raises(ValueError, match="velocity or acceleration"):
        robot.propose(MotionRequest.model_validate(body), "session", body["issued_at"])
    assert robot.pending is None and robot.active is None
    assert all(value == 0 for value in robot.bank.read().values())


def test_complete_frame_moves_every_reference_axis_and_urdf_matches_limits():
    robot = Humanoid()
    body = request()
    body["targets"] = dict.fromkeys(BY_NAME, 0.1)
    value = MotionRequest.model_validate(body)
    plan = robot.propose(value, "session", value.issued_at)
    robot.approve(value.command_id, "session", plan.plan_sha256, value.issued_at, 10)
    robot.tick(11.5)
    assert all(value == pytest.approx(0.05) for value in robot.bank.read().values())
    robot.tick(13)
    assert all(value == pytest.approx(0.1) for value in robot.bank.read().values())
    assert all(value == 0 for value in robot.velocities.values())
    for element in ET.fromstring(urdf()).findall("joint"):
        joint = BY_NAME[element.attrib["name"]]
        assert element.find("parent").attrib["link"] == joint.parent
        limit = element.find("limit").attrib
        assert float(limit["lower"]) == joint.lower
        assert float(limit["upper"]) == joint.upper
        assert float(limit["velocity"]) == joint.velocity


def test_mutable_plan_contents_cannot_bypass_exact_review():
    robot = Humanoid()
    value = MotionRequest.model_validate(request())
    plan = robot.propose(value, "session", value.issued_at)
    plan.targets["head_yaw"] = 0.5
    with pytest.raises(ValueError, match="Review"):
        robot.approve(value.command_id, "session", plan.plan_sha256, value.issued_at, 10)
    assert robot.active is None
    assert all(value == 0 for value in robot.bank.read().values())


def test_replay_protection_outlives_bounded_detailed_history():
    robot = Humanoid()
    first = MotionRequest.model_validate(request())
    robot.propose(first, "session", first.issued_at)
    robot.stop("operator_stop")
    for _ in range(64):
        value = MotionRequest.model_validate(request())
        robot.propose(value, "session", value.issued_at)
        robot.stop("operator_stop")
    assert len(robot.history) == 64
    assert all(plan.request.command_id != first.command_id for plan in robot.history)
    with pytest.raises(ValueError, match="replayed"):
        robot.propose(first, "session", first.issued_at)


def test_general_console_heartbeat_cannot_renew_unobserved_humanoid_motion(api):
    sign_in(api)
    command = start_motion(api)

    def last_heartbeat():
        return api.app.state.sim.humanoid.active.heartbeat

    before = api.portal.call(last_heartbeat)
    assert api.post("/api/v1/heartbeat", json={}).status_code == 200
    assert api.portal.call(last_heartbeat) == before
    assert (
        api.post("/api/v1/humanoid/heartbeat", json={"command_id": str(uuid4())}).status_code == 409
    )
    assert api.portal.call(last_heartbeat) == before
    assert api.post("/api/v1/humanoid/heartbeat", json={"command_id": command}).status_code == 200
    assert api.portal.call(last_heartbeat) > before

    def expire():
        api.app.state.sim.humanoid.active.heartbeat -= 10

    api.portal.call(expire)
    # Even a queued, otherwise valid heartbeat cannot revive expired supervision.
    assert api.post("/api/v1/humanoid/heartbeat", json={"command_id": command}).status_code == 409
    observation = api.get("/api/v1/humanoid/state").json()
    assert observation["stopped"] and observation["active_command"] is None


def test_another_session_observes_motion_without_renewing_its_authority(api):
    sign_in(api)
    command = start_motion(api)
    second = api.post("/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}).json()
    api.headers["X-CSRF-Token"] = second["csrf"]
    observation = api.get("/api/v1/humanoid/state").json()
    assert observation["active_command"] == command and not observation["owns_active"]
    assert api.post("/api/v1/humanoid/heartbeat", json={"command_id": command}).status_code == 409
    assert api.post("/api/v1/stop", json={}).status_code == 200


def test_stop_adapter_failure_cannot_interrupt_gateway_revocation(api, monkeypatch):
    sign_in(api)
    start_motion(api)

    def fail():
        raise OSError("synthetic private adapter details")

    monkeypatch.setattr(api.app.state.sim.humanoid.bank, "stop", fail)
    result = api.post("/api/v1/stop", json={})
    assert result.status_code == 200 and "private" not in result.text
    observed = api.get("/api/v1/humanoid/state").json()
    assert observed["stopped"] and observed["active_command"] is None
    assert observed["connection"] == "simulator_fault"
    assert observed["history"][-1]["reason"] == "actuator_failure"
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 503
    assert api.post("/api/v1/humanoid/proposals", json=request()).status_code == 503


@pytest.mark.parametrize("interruption", ["stop_and_reset", "logout", "expired_session"])
def test_late_confirmation_cannot_restore_revoked_whole_body_authority(api, interruption):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    sign_in(api)
    plan = api.post("/api/v1/humanoid/proposals", json=request()).json()
    entered = threading.Event()
    release = asyncio.Event()
    original = api.app.state.passwords.verify

    async def held_confirmation(*args):
        valid = await original(*args)
        if not entered.is_set():
            entered.set()
            await release.wait()
        return valid

    api.app.state.passwords.verify = held_confirmation
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            api.post,
            f"/api/v1/humanoid/proposals/{plan['request']['command_id']}/approve",
            json={"password": PASSWORD, "expected_plan_sha256": plan["plan_sha256"]},
        )
        try:
            assert entered.wait(2)
            if interruption == "logout":
                assert api.post("/api/v1/auth/logout", json={}).status_code == 200
            elif interruption == "expired_session":

                def expire_session():
                    api.app.state.sim.db.execute("UPDATE sessions SET expires=0")
                    api.app.state.sim.db.commit()

                api.portal.call(expire_session)
            else:
                assert api.post("/api/v1/stop", json={}).status_code == 200
                assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 200
        finally:
            api.portal.call(release.set)
            api.app.state.passwords.verify = original
        assert future.result(timeout=3).status_code == (
            409 if interruption == "stop_and_reset" else 401
        )
    assert api.portal.call(lambda: api.app.state.sim.humanoid.active) is None
    assert all(value == 0 for value in api.app.state.sim.humanoid.bank.read().values())


@pytest.mark.parametrize("first", ["humanoid", "inspection", "joint"])
def test_motion_modes_exclude_each_others_pending_and_running_work(api, first):
    sign_in(api)
    mission = {
        "command_id": str(uuid4()),
        "robot_profile_id": "sim-inspector",
        "scenario": "normal",
        "seed": 101,
    }
    joint = {
        "command_id": str(uuid4()),
        "issued_at": time.time(),
        "target_radians": 0.1,
        "duration_seconds": 3.0,
    }
    if first == "humanoid":
        assert api.post("/api/v1/humanoid/proposals", json=request()).status_code == 201
        assert api.post("/api/v1/missions", json=mission).status_code == 409
        assert api.post("/api/v1/tasks", json=joint).status_code == 409
    else:
        path, body = (
            ("/api/v1/missions", mission) if first == "inspection" else ("/api/v1/tasks", joint)
        )
        assert api.post(path, json=body).status_code == 201
        assert api.post("/api/v1/humanoid/proposals", json=request()).status_code == 409
    assert api.post("/api/v1/stop", json={}).status_code == 200
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 200
    start_motion(api)
    assert api.post("/api/v1/missions", json=mission).status_code == 409
    assert api.post("/api/v1/tasks", json=joint).status_code == 409
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 409
