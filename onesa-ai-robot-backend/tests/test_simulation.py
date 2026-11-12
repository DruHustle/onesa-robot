import asyncio
import sqlite3
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from robot_api.main import Settings, create_app
from robot_api.safety import Proposal, SafetyGate, SafetyViolation
from robot_api.security import password_hash

PASSWORD = "robot-test-only-operator-password"
HASH = password_hash(PASSWORD)


@pytest.fixture
def config(tmp_path):
    return Settings(
        operator_password_hash=HASH, database_path=tmp_path / "robot.sqlite3", heartbeat_seconds=1
    )


@pytest.fixture
def api(config):
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        yield client


def login(client):
    response = client.post("/api/v1/auth/login", json={"name": "operator", "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return response


def reset(client):
    assert client.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 200


def proposal(**changes):
    return {
        "command_id": str(uuid4()),
        "issued_at": time.time(),
        "target_radians": 0.1,
        "duration_seconds": 2,
        **changes,
    }


def approve(client, value):
    created = client.post("/api/v1/tasks", json=value)
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    response = client.post(f"/api/v1/tasks/{task_id}/approve", json={"password": PASSWORD})
    assert response.status_code == 200, response.text
    return task_id


def test_auth_origin_csrf_and_control_boundaries(api):
    assert api.get("/api/v1/health").status_code == 200
    assert api.get("/api/v1/ready").json()["physical_actuation"] is False
    assert api.get("/api/v1/status").status_code == 401
    assert api.post("/api/v1/stop").status_code == 401
    response = login(api)
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=strict" in response.headers["set-cookie"]
    assert api.get("/api/v1/auth/me").json()["csrf"] == response.json()["csrf"]
    csrf = api.headers.pop("X-CSRF-Token")
    assert api.post("/api/v1/stop").status_code == 403
    api.headers["X-CSRF-Token"] = csrf
    api.headers["Origin"] = "https://attacker.example"
    assert api.post("/api/v1/stop").status_code == 403
    api.headers["Origin"] = "http://localhost:3431"
    assert api.post("/api/v1/reset", json={"password": "wrong"}).status_code == 403
    assert api.post("/api/v1/tasks", json=proposal()).status_code == 409
    reset(api)
    assert (
        api.post("/api/v1/tasks", json=proposal(target_radians=1, duration_seconds=1)).status_code
        == 422
    )
    assert api.post("/api/v1/tasks", json=proposal(issued_at=time.time() - 30)).status_code == 422
    assert api.post("/api/v1/tasks", json=proposal(issued_at=time.time() + 30)).status_code == 422
    assert api.post("/api/v1/tasks", json={**proposal(), "physical": True}).status_code == 422
    assert api.post("/api/v1/stop", content="x" * 16385).status_code == 413


@pytest.mark.parametrize("environment", ["local", "staging", "production"])
def test_status_context_is_authenticated_and_runtime_sourced(config, environment):
    config.deployment_environment = environment
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        assert client.get("/api/v1/status?deployment_environment=wrong").status_code == 401
        login(client)
        before = time.time()
        response = client.get("/api/v1/status?deployment_environment=wrong")
        after = time.time()
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["deployment_environment"] == environment
        assert before <= response.json()["sampled_at"] <= after
        assert response.json()["mode"] == "simulation"
        assert response.json()["physical_actuation"] is False


def test_completion_requires_heartbeat_no_replay_and_explicit_stop_reset(api):
    login(api)
    reset(api)
    value = proposal()
    task_id = approve(api, value)
    assert api.post("/api/v1/tasks", json=value).status_code == 409
    assert (
        api.post(f"/api/v1/tasks/{task_id}/approve", json={"password": PASSWORD}).status_code == 409
    )
    for _ in range(7):
        assert api.post("/api/v1/heartbeat").status_code == 200
        time.sleep(0.35)
    status = api.get("/api/v1/status").json()
    assert status["mode"] == "simulation"
    assert status["physical_actuation"] is False
    assert status["position_radians"] == pytest.approx(0.1)
    assert api.get("/api/v1/tasks").json()["tasks"][0]["state"] == "completed"
    approve(api, proposal(target_radians=0.2))
    assert api.post("/api/v1/stop").status_code == 200
    assert api.get("/api/v1/status").json()["stopped"]
    assert api.get("/api/v1/tasks").json()["tasks"][0]["state"] == "cancelled"
    assert api.post("/api/v1/tasks", json=proposal()).status_code == 409
    reset(api)
    assert not api.get("/api/v1/status").json()["stopped"]


def test_watchdog_stops_and_does_not_resume_after_reconnection(api):
    login(api)
    reset(api)
    approve(api, proposal(duration_seconds=5))
    time.sleep(1.2)
    assert api.get("/api/v1/status").json()["stopped"]
    assert api.get("/api/v1/tasks").json()["tasks"][0]["reason"] == "operator_connection_lost"
    api.post("/api/v1/heartbeat")
    assert api.get("/api/v1/status").json()["active_task"] is None


@pytest.mark.parametrize("method,path", [("GET", "/api/v1/status"), ("POST", "/api/v1/heartbeat")])
def test_requests_revoke_expired_authority_when_background_tick_is_delayed(
    api, monkeypatch, method, path
):
    login(api)
    reset(api)
    task_id = approve(api, proposal(duration_seconds=5))
    sim = api.app.state.sim
    original_tick = sim.tick

    def delayed_background_tick():
        if asyncio.current_task() is not api.app.state.watch:
            original_tick()

    monkeypatch.setattr(sim, "tick", delayed_background_tick)
    api.portal.call(lambda: sim.active.update(heartbeat=time.monotonic() - 2))
    position = sim.position
    assert api.request(method, path).status_code == 200
    assert api.portal.call(lambda: sim.stopped) and sim.active is None and sim.position == position
    row = api.portal.call(
        lambda: sim.db.execute("SELECT state,reason FROM tasks WHERE id=?", (task_id,)).fetchone()
    )
    assert tuple(row) == ("cancelled", "operator_connection_lost")
    assert api.post("/api/v1/heartbeat").status_code == 200
    assert api.get("/api/v1/status").json()["active_task"] is None


def test_session_revocation_cancel_restart_and_expiry(config):
    with TestClient(create_app(config)) as first:
        first.headers["Origin"] = config.public_origin
        login(first)
        reset(first)
        task_id = approve(first, proposal(duration_seconds=5))
        assert first.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 200
        reset(first)
        approve(first, proposal(duration_seconds=5))
        assert first.post("/api/v1/auth/logout").status_code == 200
        assert first.get("/api/v1/tasks").status_code == 401
        login(first)
        reset(first)
        approve(first, proposal(duration_seconds=5))

        def expire_sessions():
            first.app.state.sim.db.execute("UPDATE sessions SET expires=0")
            first.app.state.sim.db.commit()

        first.portal.call(expire_sessions)
        time.sleep(0.15)
        assert first.get("/api/v1/tasks").status_code == 401
        assert first.portal.call(lambda: first.app.state.sim.stopped)
        login(first)
        reset(first)
        approve(first, proposal(duration_seconds=5))
    with TestClient(create_app(config)) as second:
        second.headers["Origin"] = config.public_origin
        second.cookies.update(first.cookies)
        assert second.get("/api/v1/status").status_code == 401
        login(second)
        state = second.get("/api/v1/status").json()
        assert state["stopped"] and state["active_task"] is None
        rows = second.get("/api/v1/tasks").json()["tasks"]
        assert len(rows) == 4
        assert rows[0]["state"] == "cancelled"


def test_database_connection_is_owned_by_application_thread(api):
    with pytest.raises(sqlite3.ProgrammingError, match="same thread"):
        api.app.state.sim.db.execute("SELECT stopped FROM safety")
    assert (
        api.portal.call(
            lambda: api.app.state.sim.db.execute("SELECT stopped FROM safety").fetchone()[0]
        )
        == 1
    )
    assert api.get("/api/v1/ready").status_code == 200


def test_approval_rechecks_staleness_and_owner(api):
    login(api)
    reset(api)
    response = api.post("/api/v1/tasks", json=proposal())
    task_id = response.json()["id"]
    assert api.post("/api/v1/tasks/absent/approve", json={"password": PASSWORD}).status_code == 404
    sim = api.app.state.sim
    row = api.portal.call(
        lambda: sim.db.execute("SELECT proposal FROM tasks WHERE id=?", (task_id,)).fetchone()
    )
    expired = Proposal.model_validate_json(row[0]).model_copy(
        update={"issued_at": time.time() - 30}
    )

    def replace_proposal():
        sim.db.execute(
            "UPDATE tasks SET proposal=? WHERE id=?", (expired.model_dump_json(), task_id)
        )
        sim.db.commit()

    api.portal.call(replace_proposal)
    assert (
        api.post(f"/api/v1/tasks/{task_id}/approve", json={"password": PASSWORD}).status_code == 422
    )

    def change_owner():
        sim.db.execute("UPDATE tasks SET owner='different-operator' WHERE id=?", (task_id,))
        sim.db.commit()

    api.portal.call(change_owner)
    assert api.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 404
    assert api.get("/api/v1/tasks").json()["tasks"] == []


def test_login_limit_and_secure_cookie(config):
    config.public_origin = "https://localhost"
    with TestClient(create_app(config), base_url=config.public_origin) as client:
        client.headers["Origin"] = config.public_origin
        assert "Secure" in login(client).headers["set-cookie"]
        for _ in range(5):
            assert (
                client.post(
                    "/api/v1/auth/login", json={"name": "operator", "password": "wrong"}
                ).status_code
                == 401
            )
        assert (
            client.post(
                "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
            ).status_code
            == 429
        )


def test_hosted_console_rejects_product_password_when_portal_is_unconfigured(config):
    config.public_origin = "https://robot.example.invalid"
    with TestClient(create_app(config), base_url=config.public_origin) as client:
        client.headers["Origin"] = config.public_origin
        result = client.post("/api/v1/auth/login", json={"name": "operator", "password": PASSWORD})
        assert result.status_code == 403
        assert "set-cookie" not in result.headers


def test_gate_limits_and_actuation_disabled(config):
    gate = SafetyGate()
    for values in [(float("nan"), 0, 0), (0, 0.41, 0), (0, 0, 0.61), (1.01, 0, 0)]:
        with pytest.raises(SafetyViolation):
            gate.check(*values)
    for n in range(101):
        gate.sample(0, 0.1, 2, n / 50)
    with pytest.raises(ValidationError):
        Settings(operator_password_hash=HASH, actuation_enabled=True)
    with pytest.raises(ValidationError):
        Settings(operator_password_hash=HASH, public_origin="http://robot.example.invalid")


def test_isolated_online_backup_restores_history_without_execution_authority(config, tmp_path):
    backup = tmp_path / "backup.sqlite3"
    restored = tmp_path / "restored.sqlite3"
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        login(client)
        reset(client)
        approve(client, proposal(duration_seconds=5))

        def snapshot():
            destination = sqlite3.connect(backup)
            try:
                client.app.state.sim.db.backup(destination)
            finally:
                destination.close()

        client.portal.call(snapshot)
    source, destination = sqlite3.connect(backup), sqlite3.connect(restored)
    try:
        source.backup(destination)
    finally:
        source.close()
        destination.close()
    config.database_path = restored
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        assert client.get("/api/v1/status").status_code == 401
        login(client)
        status = client.get("/api/v1/status").json()
        assert status["stopped"] and status["active_task"] is None
        task = client.get("/api/v1/tasks").json()["tasks"][0]
        assert task["state"] == "cancelled" and task["reason"] == "process_restart"


def test_failed_watchdog_cannot_reset_or_authorize_tasks(api):
    login(api)
    reset(api)
    created = api.post("/api/v1/tasks", json=proposal()).json()["id"]
    api.portal.call(lambda: api.app.state.watch.cancel())
    time.sleep(0.1)
    assert api.get("/api/v1/ready").status_code == 503
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 503
    assert api.post("/api/v1/tasks", json=proposal()).status_code == 503
    assert (
        api.post(f"/api/v1/tasks/{created}/approve", json={"password": PASSWORD}).status_code == 503
    )
    # Stopping remains available even when the watchdog has failed.
    assert api.post("/api/v1/stop").status_code == 200


def test_confirmation_limits_and_config_errors_hide_hash(api):
    login(api)
    for _ in range(5):
        assert api.post("/api/v1/reset", json={"password": "wrong"}).status_code == 403
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 429
    assert api.post("/api/v1/stop").status_code == 200
    disclosed = "scrypt$invalid-private-test-only-material"
    with pytest.raises(ValidationError) as caught:
        Settings(operator_password_hash=disclosed)
    assert disclosed not in str(caught.value)


def test_password_work_cannot_starve_watchdog_or_queue_unbounded_hashes(api, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from robot_api import security

    login(api)
    reset(api)
    approve(api, proposal(duration_seconds=5))
    entered, release = threading.Event(), threading.Event()
    lock = threading.Lock()
    started = 0

    def slow_verify(*_args):
        nonlocal started
        with lock:
            started += 1
            if started == 2:
                entered.set()
        assert release.wait(5)
        return False

    monkeypatch.setattr(security, "verify", slow_verify)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [
            pool.submit(
                api.post, "/api/v1/auth/login", json={"name": "operator", "password": "wrong"}
            )
            for _ in range(2)
        ]
        try:
            assert entered.wait(2)
            assert (
                api.post(
                    "/api/v1/auth/login", json={"name": "operator", "password": "wrong"}
                ).status_code
                == 429
            )
            deadline = time.monotonic() + 2
            while (
                not api.portal.call(lambda: api.app.state.sim.stopped)
                and time.monotonic() < deadline
            ):
                time.sleep(0.02)
            assert api.portal.call(lambda: api.app.state.sim.stopped)
            assert (
                api.get("/api/v1/tasks").json()["tasks"][0]["reason"] == "operator_connection_lost"
            )
            assert api.post("/api/v1/stop").status_code == 200
        finally:
            release.set()
        assert all(job.result(2).status_code == 401 for job in pending)


def test_confirmation_rechecks_session_after_concurrent_logout(api, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from robot_api import security

    login(api)
    reset(api)
    identifier = api.post("/api/v1/tasks", json=proposal()).json()["id"]
    entered, release = threading.Event(), threading.Event()

    def slow_verify(*_args):
        entered.set()
        assert release.wait(5)
        return True

    monkeypatch.setattr(security, "verify", slow_verify)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            api.post, f"/api/v1/tasks/{identifier}/approve", json={"password": PASSWORD}
        )
        try:
            assert entered.wait(2)
            assert api.post("/api/v1/auth/logout").status_code == 200
        finally:
            release.set()
        assert pending.result(2).status_code == 401
    assert api.app.state.sim.active is None


def test_cancelled_hash_caller_retains_capacity_until_worker_finishes(monkeypatch):
    import asyncio
    import threading

    from robot_api import security

    release = threading.Event()
    entered = [threading.Event(), threading.Event()]
    lock = threading.Lock()
    calls = 0

    def slow_verify(*_args):
        nonlocal calls
        with lock:
            index = calls
            calls += 1
        if index < 2:
            entered[index].set()
            assert release.wait(5)
        return True

    monkeypatch.setattr(security, "verify", slow_verify)

    async def exercise():
        verifier = security.PasswordVerifier()
        jobs = [asyncio.create_task(verifier.verify(PASSWORD, HASH)) for _ in range(2)]
        try:
            for event in entered:
                assert await asyncio.to_thread(event.wait, 2)
            jobs[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await jobs[0]
            with pytest.raises(security.VerificationBusy):
                await verifier.verify(PASSWORD, HASH)
        finally:
            release.set()
        assert await jobs[1]
        # Both worker completions release capacity even after the first caller disconnects.
        await asyncio.sleep(0.05)
        assert await verifier.verify(PASSWORD, HASH)

    asyncio.run(exercise())
