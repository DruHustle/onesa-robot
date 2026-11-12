import time
from hashlib import sha256
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from robot_api.main import Settings, create_app
from robot_api.security import password_hash

TOKEN = "synthetic-test-device-token-" + "a" * 64


@pytest.fixture
def settings(tmp_path):
    return Settings(
        operator_password_hash=password_hash("synthetic-test-password"),
        database_path=tmp_path / "robot.sqlite3",
        edge_device_id="synthetic-ci",
        edge_token_sha256=sha256(TOKEN.encode()).hexdigest(),
    )


@pytest.fixture
def device(settings):
    with TestClient(create_app(settings)) as api:
        api.headers["Authorization"] = "Bearer " + TOKEN
        yield api


def telemetry(api):
    challenge = api.get("/api/v1/edge/challenge").json()
    return {
        "version": 1,
        "device_id": "synthetic-ci",
        "boot_challenge": challenge["boot_challenge"],
        "sequence": challenge["next_sequence"],
        "observed_at": time.time(),
        "mode": "simulation",
        "physical_actuation": False,
        "stopped": True,
        "position_radians": 0.0,
    }


def test_device_identity_is_distinct_from_operator_authority(device):
    assert (
        device.get(
            "/api/v1/edge/challenge", headers={"Authorization": "Bearer invalid"}
        ).status_code
        == 401
    )
    assert device.get("/api/v1/status").status_code == 401
    assert device.post("/api/v1/reset", json={"password": TOKEN}).status_code == 403
    value = telemetry(device)
    assert device.post("/api/v1/edge/telemetry", json=value).status_code == 200
    assert device.portal.call(lambda: device.app.state.sim.stopped)
    assert device.app.state.sim.active is None
    assert device.app.state.edge.status() == "simulation_connected"
    assert device.app.state.edge.audit[-1]["event"] == "simulation_telemetry"


def test_replay_stale_future_wrong_identity_and_commands_denied(device):
    value = telemetry(device)
    assert device.post("/api/v1/edge/telemetry", json=value).status_code == 200
    assert device.post("/api/v1/edge/telemetry", json=value).status_code == 409
    for changes, code in [
        ({"sequence": 1, "observed_at": time.time() - 10}, 409),
        ({"sequence": 1, "observed_at": time.time() + 10}, 409),
        ({"device_id": "synthetic-other"}, 403),
        ({"physical_actuation": True}, 422),
        ({"command": "move"}, 422),
        ({"stopped": False}, 422),
    ]:
        assert device.post("/api/v1/edge/telemetry", json={**value, **changes}).status_code == code


def test_disconnected_telemetry_cannot_appear_online(device):
    value = telemetry(device)
    assert device.post("/api/v1/edge/telemetry", json=value).status_code == 200
    with patch("robot_api.edge.time.monotonic", return_value=device.app.state.edge.observed + 4):
        assert device.app.state.edge.status() == "not_connected"


@pytest.mark.parametrize("challenge", ["é" * 43, "🤖" * 43, "=" * 43, "\n" * 43])
def test_malformed_boot_challenge_is_rejected_without_changing_edge_state(device, challenge):
    value = telemetry(device)
    assert (
        device.post(
            "/api/v1/edge/telemetry", json={**value, "boot_challenge": challenge}
        ).status_code
        == 422
    )
    assert device.app.state.edge.sequence == -1
    assert device.app.state.edge.status() == "not_connected"
    assert not device.app.state.edge.audit
    assert device.portal.call(lambda: device.app.state.sim.stopped)
    assert device.post("/api/v1/edge/telemetry", json=value).status_code == 200


def test_restart_rejects_old_boot_and_never_resumes(settings):
    with TestClient(create_app(settings)) as first:
        first.headers["Authorization"] = "Bearer " + TOKEN
        value = telemetry(first)
    with TestClient(create_app(settings)) as second:
        second.headers["Authorization"] = "Bearer " + TOKEN
        assert second.post("/api/v1/edge/telemetry", json=value).status_code == 403
        assert (
            second.portal.call(lambda: second.app.state.sim.stopped)
            and second.app.state.sim.active is None
        )


def test_rate_limit_and_unconfigured_gateway(device, tmp_path):
    for _ in range(10):
        assert device.get("/api/v1/edge/challenge").status_code == 200
    assert device.get("/api/v1/edge/challenge").status_code == 429
    with TestClient(
        create_app(
            Settings(
                operator_password_hash=password_hash("test-only-password"),
                database_path=tmp_path / "other.sqlite3",
            )
        )
    ) as api:
        assert api.get("/api/v1/edge/challenge").status_code == 503


def test_production_ephemeral_and_real_device_startup_blocked(settings):
    for changes in [
        {"deployment_environment": "production", "storage_mode": "ephemeral-simulation"},
        {"edge_device_id": "real-actuator"},
        {"actuation_enabled": True},
        {"edge_token_sha256": ""},
    ]:
        with pytest.raises(ValidationError):
            Settings(**{**settings.model_dump(), **changes})


@pytest.mark.parametrize("shared_identity", [False, True])
def test_hosted_acceptance_executes_real_api_and_safety_flows(
    settings, monkeypatch, shared_identity
):
    """Hosted transport is adapted locally; simulator/auth/safety handlers are real."""
    import http.cookiejar
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/release"))
    import smoke

    settings.public_origin = "https://synthetic.vercel.app"
    settings.administrator_name = settings.operator_name
    settings.deployment_environment = "staging"
    settings.storage_mode = "ephemeral-simulation"
    issuer = None
    if shared_identity:
        from test_shared_identity import ISSUER, Issuer, person

        from robot_api import onesa_identity as identity

        issuer = Issuer()
        principal = person(role="admin", admin=True)
        monkeypatch.setattr(identity, "exchange_code", issuer.exchange)
        monkeypatch.setattr(identity, "validate_session", issuer.validate)
        monkeypatch.setattr(identity, "revoke_session", issuer.revoke)
        settings = Settings(
            **{
                **settings.model_dump(),
                "identity_enabled": True,
                "identity_issuer": ISSUER,
                "identity_client_id": "robot-synthetic",
                "identity_client_secret": "test-only-synthetic-client-secret-" + "x" * 32,
                "identity_callback_url": settings.public_origin + "/api/v1/auth/identity/callback",
            }
        )
    config = {
        "ROBOT_TEST_PASSWORD": "synthetic-test-password",
        "ROBOT_TEST_OPERATOR": "operator",
        "ROBOT_TEST_SCOPE": "approved-synthetic-staging",
        "ROBOT_EDGE_TOKEN": TOKEN,
        "ROBOT_EDGE_DEVICE_ID": "synthetic-ci",
        "ROBOT_API_URL": "https://synthetic.onrender.com",
    }
    for key, value in config.items():
        monkeypatch.setenv(key, value)
    with TestClient(create_app(settings), base_url=settings.public_origin) as api:
        api.headers["Origin"] = settings.public_origin

        class LocalSimulator:
            def __init__(self, url):
                self.url = url
                self.csrf = ""
                self.jar: http.cookiejar.CookieJar = api.cookies.jar

            def call(self, route, body=None, expected=200, csrf=True, timeout=45):
                if route == "/":
                    return (
                        Path(__file__).resolve().parents[2]
                        / "onesa-ai-robot-frontend/public/index.html"
                    ).read_bytes()
                headers = {"Origin": self.url}
                if self.csrf and csrf:
                    headers["X-CSRF-Token"] = self.csrf
                response = api.request(
                    "GET" if body is None else "POST", route, headers=headers, json=body
                )
                smoke.require(response.status_code == expected, "Local simulator HTTP failure")
                return response.json()

        class EdgeResponse:
            code = 200

            def __init__(self, response):
                self.response = response

            def read(self, limit):
                return self.response.content[:limit]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        class LocalEdge:
            def open(self, request, timeout):
                from urllib.parse import urlsplit

                response = api.request(
                    request.get_method(),
                    urlsplit(request.full_url).path,
                    headers=dict(request.header_items()),
                    json=json.loads(request.data) if request.data else None,
                )
                assert response.status_code == 200
                return EdgeResponse(response)

        monkeypatch.setattr(smoke, "Simulator", LocalSimulator)
        monkeypatch.setattr(smoke.urllib.request, "build_opener", lambda *args: LocalEdge())
        identity_login = None
        if shared_identity:
            from test_shared_identity import finish, start

            def identity_login(local):
                response = finish(api, issuer, principal, start(api))
                assert response.status_code == 303
                observed = api.get("/api/v1/auth/me")
                assert observed.status_code == 200
                return observed.json()

        result = smoke.run(
            settings.public_origin,
            "Staging",
            **(
                {
                    "identity_login": identity_login,
                    "oracle_staging_api": "https://robot-api-staging.onesa.eu",
                }
                if shared_identity
                else {}
            ),
        )
        if not shared_identity:
            assert result["status"] == "FAIL", result
            assert result["failure"]["phase"] == "authentication"
            assert not api.cookies.get("robot_session")
            return
        assert result["status"] == "PASS", result
        assert all(value == "PASS" for value in result["checks"].values())
        assert result["physical_hardware_approved"] is False
        if shared_identity:
            assert len(issuer.exchanges) == 2  # Initial login and real reconnect.
            assert len(issuer.revocations) == 2
            assert all(all_sessions is True for _token, all_sessions in issuer.revocations)
