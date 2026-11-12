"""Complete feedback is required before accepting a simulated actuator frame."""

import pytest

from robot_api.humanoid import BY_NAME, ActuatorFault, Humanoid, MotionRequest
from tests.test_humanoid import PASSWORD, request, sign_in, start_motion
from tests.test_humanoid import api as api


@pytest.mark.parametrize("bad", ["missing", "extra", "nonfinite", "limit", "boolean"])
def test_malformed_feedback_never_creates_a_plan_or_observation(api, monkeypatch, bad):
    sign_in(api)
    sim = api.app.state.sim
    bank = sim.humanoid.bank
    malformed = bank.read()
    if bad == "missing":
        del malformed["head_yaw"]
    elif bad == "extra":
        malformed["unconfigured_joint"] = 0.0
    else:
        malformed["head_yaw"] = {"nonfinite": float("nan"), "limit": 1.0, "boolean": True}[bad]
    monkeypatch.setattr(bank, "read", lambda: malformed)
    proposal = api.post("/api/v1/humanoid/proposals", json=request())
    assert proposal.status_code == 503
    assert sim.humanoid.pending is None
    assert api.get("/api/v1/humanoid/state").status_code == 503
    assert api.get("/api/v1/status").json()["stopped"] is True


def test_lost_readback_during_approval_revokes_review_and_redacts_fault(api, monkeypatch):
    sign_in(api)
    plan = api.post("/api/v1/humanoid/proposals", json=request()).json()
    bank = api.app.state.sim.humanoid.bank

    def offline():
        raise OSError("private device transport details")

    with monkeypatch.context() as patch:
        patch.setattr(bank, "read", offline)
        response = api.post(
            f"/api/v1/humanoid/proposals/{plan['request']['command_id']}/approve",
            json={"password": PASSWORD, "expected_plan_sha256": plan["plan_sha256"]},
        )
        assert response.status_code == 503 and "private" not in response.text
        assert api.get("/api/v1/humanoid/state").status_code == 503

    observation = api.get("/api/v1/humanoid/state").json()
    assert observation["stopped"] and observation["connection"] == "simulator_fault"
    assert observation["pending"] is None and observation["active_command"] is None
    assert observation["history"][-1]["reason"] == "actuator_failure"
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 503
    assert api.get("/api/v1/ready").status_code == 200


def test_missed_final_feedback_cannot_report_target_reached(monkeypatch):
    robot = Humanoid()
    command = MotionRequest.model_validate(request())
    plan = robot.propose(command, "session", command.issued_at)
    robot.approve(command.command_id, "session", plan.plan_sha256, command.issued_at, 10)
    # An adapter accepted the write but one axis stayed at its previous position.
    write = robot.bank.write

    def missed_axis(positions):
        positions["left_arm_elbow"] = 0.0
        write(positions)

    monkeypatch.setattr(robot.bank, "write", missed_axis)
    with pytest.raises(ActuatorFault, match="stopped"):
        robot.tick(13)
    assert robot.active is None and robot.faulted
    assert [(p.state, p.reason) for p in robot.history] == [("cancelled", "actuator_failure")]
    assert robot.velocities == dict.fromkeys(BY_NAME, 0.0)


def test_lost_feedback_while_running_latches_global_stop(api, monkeypatch):
    sign_in(api)
    start_motion(api)
    sim = api.app.state.sim
    with monkeypatch.context() as patch:
        patch.setattr(sim.humanoid.bank, "read", lambda: {})
        assert api.get("/api/v1/humanoid/state").status_code == 503
    observation = api.get("/api/v1/humanoid/state").json()
    assert observation["active_command"] is None and observation["stopped"]
    assert observation["history"][-1]["reason"] == "actuator_failure"
    assert api.post("/api/v1/humanoid/proposals", json=request()).status_code == 503
