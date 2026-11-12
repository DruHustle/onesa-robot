"""Owner permission is real software authorization, never simulated hardware readiness."""

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from robot_api.main import Settings, create_app
from robot_api.security import password_hash

PASSWORD = "synthetic-administrator-test-password-only"
BASE = "/api/v1/admin/physical-operation"


def settings(tmp_path, administrator="operator"):
    return Settings(
        operator_password_hash=password_hash(PASSWORD),
        database_path=tmp_path / "isolated-synthetic.sqlite3",
        administrator_name=administrator,
        storage_mode="persistent",
        turso_database_url="",
        turso_auth_token="",
        actuation_enabled=False,
        deployment_environment="local",
    )


def sign_in(client):
    response = client.post("/api/v1/auth/login", json={"name": "operator", "password": PASSWORD})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return response.json()


@pytest.fixture
def administrator(tmp_path):
    config = settings(tmp_path)
    app = create_app(config)
    with TestClient(app) as client:
        client.headers["Origin"] = config.public_origin
        assert sign_in(client)["role"] == "administrator"
        yield client, app


def allow(client, revision=None):
    if revision is None:
        revision = client.get(BASE).json()["revision"]
    return client.post(BASE + "/allow", json={"password": PASSWORD, "expected_revision": revision})


def test_anonymous_and_non_administrator_cannot_read_or_change_permission(tmp_path):
    config = settings(tmp_path, administrator="")
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        assert client.get(BASE).status_code == 401
        assert client.post(BASE + "/block", json={}).status_code == 401
        assert sign_in(client)["role"] == "operator"
        for method, path, body in [
            ("GET", BASE, None),
            (
                "POST",
                BASE + "/allow",
                {
                    "password": PASSWORD,
                    "expected_revision": 0,
                },
            ),
            ("POST", BASE + "/block", {}),
        ]:
            assert client.request(method, path, json=body).status_code == 403


def test_only_explicit_existing_operator_can_be_designated_administrator(tmp_path):
    with pytest.raises(ValidationError):
        settings(tmp_path, administrator="unrelated-user")


def test_allow_and_block_are_separate_from_actual_hardware_state(administrator):
    client, _ = administrator
    before = client.get("/api/v1/tasks").json()
    initial = client.get(BASE).json()
    assert initial["permission"] == "blocked"
    response = allow(client, initial["revision"])
    assert response.status_code == 200
    permission = response.json()
    assert permission["permission"] == "allowed"
    assert permission["expires_at"] > time.time()
    assert permission["controller_connection"] == "not_connected"
    assert permission["controller_readiness"] == "not_ready"
    assert permission["physical_operation"] == "inactive"
    assert permission["physical_actuation"] is False
    assert client.get("/api/v1/tasks").json() == before
    assert allow(client, initial["revision"]).status_code == 409
    blocked = client.post(BASE + "/block", json={})
    assert blocked.status_code == 200
    assert blocked.json()["permission"] == "blocked"
    assert blocked.json()["expires_at"] is None
    assert client.get(BASE).headers["cache-control"] == "no-store"


def test_feedback_loss_revokes_permission_and_blocks_new_allow(administrator, monkeypatch):
    client, app = administrator
    assert allow(client).status_code == 200
    with monkeypatch.context() as patch:
        patch.setattr(app.state.sim.humanoid.bank, "read", lambda: {})
        assert client.get("/api/v1/humanoid/state").status_code == 503
    permission = client.get(BASE).json()
    assert permission["permission"] == "blocked" and permission["physical_actuation"] is False
    assert allow(client, permission["revision"]).status_code == 503
    assert client.post(BASE + "/block", json={}).status_code == 200


def test_allow_checks_feedback_before_granting_permission(administrator, monkeypatch):
    client, app = administrator
    monkeypatch.setattr(app.state.sim.humanoid.bank, "read", lambda: {})
    assert allow(client).status_code == 503
    assert client.get(BASE).json()["permission"] == "blocked"


def test_origin_csrf_password_and_request_shape_still_apply(administrator):
    client, _ = administrator
    revision = client.get(BASE).json()["revision"]
    csrf = client.headers.pop("X-CSRF-Token")
    assert allow(client, revision).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    origin = client.headers["Origin"]
    client.headers["Origin"] = "https://untrusted.invalid"
    assert client.post(BASE + "/block", json={}).status_code == 403
    client.headers["Origin"] = origin
    assert (
        client.post(
            BASE + "/allow",
            json={
                "password": "incorrect-password",
                "expected_revision": revision,
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            BASE + "/allow",
            json={
                "password": PASSWORD,
                "expected_revision": True,
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            BASE + "/allow",
            json={
                "password": PASSWORD,
                "expected_revision": revision,
                "role": "administrator",
            },
        ).status_code
        == 422
    )
    assert client.get(BASE).json()["permission"] == "blocked"


def test_logout_expiry_and_restart_revoke_permission(administrator):
    client, app = administrator
    assert allow(client).status_code == 200
    assert client.post("/api/v1/auth/logout", json={}).status_code == 200
    assert client.get(BASE).status_code == 401
    sign_in(client)
    assert client.get(BASE).json()["permission"] == "blocked"
    assert allow(client).status_code == 200
    # Expire the software authorization, without changing any user or credential.
    app.state.sim.physical_permission_expires = time.time() - 1
    assert client.get(BASE).json()["permission"] == "blocked"


def test_restart_does_not_restore_authority(tmp_path):
    config = settings(tmp_path)
    for first in [True, False]:
        with TestClient(create_app(config)) as client:
            client.headers["Origin"] = config.public_origin
            sign_in(client)
            assert client.get(BASE).json()["permission"] == "blocked"
            if first:
                assert allow(client).json()["permission"] == "allowed"


def test_block_or_stop_during_password_confirmation_prevents_late_allow(administrator):
    client, app = administrator
    for path in [BASE + "/block", "/api/v1/stop"]:
        entered, release = threading.Event(), threading.Event()
        original = app.state.passwords.verify

        async def delayed(password, stored, original=original, entered=entered, release=release):
            valid = await original(password, stored)
            entered.set()
            while not release.is_set():
                await asyncio.sleep(0.005)
            return valid

        app.state.passwords.verify = delayed
        revision = client.get(BASE).json()["revision"]
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(allow, client, revision)
                assert entered.wait(5)
                assert client.post(path, json={}).status_code == 200
                release.set()
                assert pending.result(timeout=5).status_code == 409
        finally:
            release.set()
            app.state.passwords.verify = original
        assert client.get(BASE).json()["permission"] == "blocked"
