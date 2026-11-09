"""Real Robot routes with synthetic identity responses; never hosted traffic."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from robot_api import onesa_identity as identity
from robot_api.main import Settings, create_app
from robot_api.security import password_hash

PASSWORD = "robot-test-only-legacy-password"
HASH = password_hash(PASSWORD)
ISSUER = "https://account.example.test/api/v1/identity"


def person(*, subject=None, role="operator", mfa=True, admin=False, email="one@example.test"):
    return identity.Identity(
        issuer=ISSUER,
        subject=subject or str(uuid4()),
        email=email,
        name="Synthetic Operator",
        expires_at=int(time.time()) + 3600,
        auth_time=int(time.time()),
        amr=("pwd", "otp") if mfa else ("pwd",),
        roles=("global_admin",) if admin else (),
        applications=("robot",),
        product_roles={"robot": role},
        memberships=(),
    )


class Issuer:
    def __init__(self):
        self.codes, self.tokens = {}, {}
        self.exchanges, self.validations, self.revocations = [], [], []
        self.unavailable = False

    def code(self, principal):
        code = "synthetic-code-" + str(uuid4())
        self.codes[code] = principal
        return code

    def exchange(self, config, code, verifier):
        assert config.issuer == ISSUER and len(verifier) >= 40
        self.exchanges.append((code, verifier))
        if code not in self.codes:
            raise identity.IdentityError("invalid", "Sign in again")
        token = "synthetic-bearer-" + str(uuid4())
        self.tokens[token] = self.codes.pop(code)
        return token

    def validate(self, config, token):
        assert config.product == "robot"
        self.validations.append(token)
        if self.unavailable:
            raise identity.IdentityError("unavailable", "Identity is unavailable", 503)
        if token not in self.tokens:
            raise identity.IdentityError("invalid", "Sign in again")
        principal = self.tokens[token]
        if not principal.global_admin and "robot" not in principal.applications:
            raise identity.IdentityError("access", "Robot access is not enabled", 403)
        return principal

    def revoke(self, config, token, *, all_sessions=False):
        self.revocations.append((token, all_sessions))
        if self.unavailable:
            raise identity.IdentityError("unavailable", "Identity is unavailable", 503)
        principal = self.tokens.pop(token, None)
        if all_sessions and principal:
            self.tokens = {
                key: value
                for key, value in self.tokens.items()
                if value.subject != principal.subject
            }
        return not all_sessions or principal is not None


@pytest.fixture
def environment(tmp_path, monkeypatch):
    issuer = Issuer()
    monkeypatch.setattr(identity, "exchange_code", issuer.exchange)
    monkeypatch.setattr(identity, "validate_session", issuer.validate)
    monkeypatch.setattr(identity, "revoke_session", issuer.revoke)
    config = Settings(
        operator_password_hash=HASH,
        database_path=tmp_path / "synthetic.sqlite3",
        identity_enabled=True,
        identity_issuer=ISSUER,
        identity_client_id="robot-synthetic",
        identity_client_secret="test-only-synthetic-client-secret-" + "x" * 32,
        identity_callback_url="http://localhost:3431/api/v1/auth/identity/callback",
    )
    app = create_app(config)
    with TestClient(app, base_url=config.public_origin) as client:
        client.headers["Origin"] = config.public_origin
        yield app, client, issuer, config


def start(client, *, step_up=False):
    result = client.post("/api/v1/auth/identity/start", json={"step_up": step_up})
    assert result.status_code == 200, result.text
    authorization = result.json()["authorization_url"]
    assert "synthetic-client-secret" not in authorization
    params = parse_qs(urlsplit(authorization).query)
    assert params["code_challenge_method"] == ["S256"]
    return params


def finish(client, issuer, principal, parameters):
    return client.get(
        "/api/v1/auth/identity/callback",
        params={"code": issuer.code(principal), "state": parameters["state"][0], "iss": ISSUER},
        follow_redirects=False,
    )


def sign_in(client, issuer, principal):
    response = finish(client, issuer, principal, start(client))
    assert response.status_code == 303 and response.headers["location"] == "/console"
    assert "HttpOnly" in response.headers["set-cookie"]
    current = client.get("/api/v1/auth/me")
    assert current.status_code == 200, current.text
    client.headers["X-CSRF-Token"] = current.json()["csrf"]
    return current.json()


def proposal():
    return {
        "command_id": str(uuid4()),
        "issued_at": time.time(),
        "target_radians": 0.1,
        "duration_seconds": 2,
    }


def test_config_is_public_and_legacy_credentials_do_not_bypass_identity(environment):
    _, client, issuer, _ = environment
    assert client.get("/api/v1/auth/identity/config").json() == {
        "enabled": True,
        "account_url": "https://account.example.test/account",
    }
    assert (
        client.post(
            "/api/v1/auth/login",
            json={
                "name": "operator",
                "password": PASSWORD,
            },
        ).status_code
        == 403
    )
    result = sign_in(client, issuer, person())
    assert result["authentication_method"] == "onesa" and result["role"] == "operator"
    assert result["confirmation_expires_at"] > time.time()
    assert "bearer" not in str(result)


@pytest.mark.parametrize(
    "failure",
    [
        "missing_cookie",
        "wrong_cookie",
        "duplicate",
        "redirect",
        "wrong_issuer",
        "expired",
        "provider_error",
    ],
)
def test_callback_is_browser_bound_and_exact(environment, failure):
    app, client, issuer, _ = environment
    state = start(client)["state"][0]
    query = [("code", issuer.code(person())), ("state", state)]
    if failure in ("missing_cookie", "wrong_cookie"):
        client.cookies.clear()
        if failure == "wrong_cookie":
            client.cookies.set("robot_identity", "wrong", path="/api/v1/auth/identity")
    if failure == "duplicate":
        query.append(("state", state))
    if failure == "redirect":
        query.append(("return_to", "https://attacker.example"))
    if failure == "wrong_issuer":
        query.append(("iss", "https://attacker.example"))
    if failure == "expired":
        for key, value in app.state.sim.identity_flows.items():
            app.state.sim.identity_flows[key] = replace(value, expires=time.time() - 1)
    if failure == "provider_error":
        query.append(("error", "access_denied"))
    response = client.get("/api/v1/auth/identity/callback", params=query, follow_redirects=False)
    assert response.headers["location"] == "/console?identity=failed"
    assert not issuer.exchanges
    assert client.get("/api/v1/auth/me").status_code == 401


def test_consumed_callback_cannot_create_a_second_session(environment):
    app, client, issuer, _ = environment
    parameters = start(client)
    assert finish(client, issuer, person(), parameters).headers["location"] == "/console"
    assert (
        finish(client, issuer, person(), parameters).headers["location"]
        == "/console?identity=failed"
    )
    assert len(app.state.sim.identity_sessions) == 1


def test_confirmation_requires_current_mfa_and_explicit_review(environment):
    _, client, issuer, _ = environment
    principal = person(mfa=False)
    sign_in(client, issuer, principal)
    assert client.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 403
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 403
    parameters = start(client, step_up=True)
    assert parameters["prompt"] == ["login"] and parameters["max_age"] == ["0"]
    assert parameters["acr_values"] == ["urn:onesa:mfa"]
    response = finish(client, issuer, replace(principal, amr=("pwd", "otp")), parameters)
    assert response.headers["location"] == "/console"
    current = client.get("/api/v1/auth/me").json()
    client.headers["X-CSRF-Token"] = current["csrf"]
    assert client.post("/api/v1/reset", json={}).status_code == 403
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
    assert issuer.revocations[-1][1] is False


def test_step_up_cannot_switch_identity_or_refresh_old_authentication(environment):
    _, client, issuer, _ = environment
    principal = person()
    sign_in(client, issuer, principal)
    for substitute in (person(), replace(principal, auth_time=int(time.time()) - 301)):
        parameters = start(client, step_up=True)
        response = finish(client, issuer, substitute, parameters)
        assert response.headers["location"] == "/console?identity=failed"
        assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("authority", ["permission", "humanoid_pending", "running_task"])
def test_normal_account_switch_retires_start_bound_session_without_callback_cookie(
    environment, authority
):
    app, client, issuer, _ = environment
    old = sign_in(client, issuer, person(admin=True))
    old_cookie = client.cookies.get("robot_session")
    assert old_cookie
    if authority == "permission":
        revision = client.get("/api/v1/admin/physical-operation").json()["revision"]
        result = client.post(
            "/api/v1/admin/physical-operation/allow",
            json={"confirmed": True, "expected_revision": revision},
        )
        assert result.status_code == 200 and app.state.sim.physical_permission_owner
    else:
        assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
        if authority == "humanoid_pending":
            result = client.post(
                "/api/v1/humanoid/proposals",
                json={
                    "command_id": str(uuid4()),
                    "issued_at": time.time(),
                    "duration_seconds": 2,
                    "targets": {"head_yaw": 0.1},
                },
            )
            assert result.status_code == 201 and app.state.sim.humanoid.pending
        else:
            created = client.post("/api/v1/tasks", json=proposal()).json()
            result = client.post(f"/api/v1/tasks/{created['id']}/approve", json={"confirmed": True})
            assert result.status_code == 200 and app.state.sim.active
    parameters = start(client)
    # Preserve the Lax transaction cookie while simulating Strict-cookie omission.
    for cookie in list(client.cookies.jar):
        if cookie.name == "robot_session":
            client.cookies.delete(cookie.name, domain=cookie.domain, path=cookie.path)
    assert (
        finish(client, issuer, person(role="viewer"), parameters).headers["location"] == "/console"
    )
    current = client.get("/api/v1/auth/me")
    assert current.status_code == 200 and current.json()["role"] == "viewer"
    assert len(app.state.sim.identity_sessions) == 1
    assert app.state.sim.physical_permission_owner is None
    assert app.state.sim.humanoid.pending is None and app.state.sim.humanoid.active is None
    assert app.state.sim.active is None
    assert client.portal.call(lambda: app.state.sim.stopped)
    assert issuer.revocations[-1][1] is False
    captured = {"Cookie": f"robot_session={old_cookie}", "X-CSRF-Token": old["csrf"]}
    assert client.get("/api/v1/auth/me", headers=captured).status_code == 401
    assert (
        client.post("/api/v1/reset", headers=captured, json={"confirmed": True}).status_code == 401
    )


def test_subject_ownership_never_links_accounts_by_email(environment):
    app, client, issuer, _ = environment
    first = person(email="shared-contact@example.test")
    sign_in(client, issuer, first)
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
    created = client.post("/api/v1/tasks", json=proposal())
    assert created.status_code == 201
    identifier = created.json()["id"]
    owner = client.portal.call(
        lambda: app.state.sim.db.execute(
            "SELECT owner FROM tasks WHERE id=?",
            (identifier,),
        ).fetchone()[0]
    )
    assert owner.startswith("sso:") and first.email not in owner
    client.cookies.clear()
    sign_in(client, issuer, person(email=first.email))
    assert client.get("/api/v1/tasks").json() == {"tasks": []}
    response = client.post(f"/api/v1/tasks/{identifier}/approve", json={"confirmed": True})
    assert response.status_code == 404


def test_viewer_reads_but_cannot_operate_or_administer(environment):
    _, client, issuer, _ = environment
    current = sign_in(client, issuer, person(role="viewer"))
    assert current["role"] == "viewer"
    assert client.get("/api/v1/status").status_code == 200
    assert client.post("/api/v1/heartbeat").status_code == 200
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 403
    assert client.post("/api/v1/tasks", json=proposal()).status_code == 403
    assert client.get("/api/v1/admin/physical-operation").status_code == 403
    assert client.post("/api/v1/stop").status_code == 200


def test_humanoid_history_belongs_to_stable_subject_and_never_shared_email(environment):
    app, client, issuer, _ = environment
    first = person(email="shared-contact@example.test")
    sign_in(client, issuer, first)
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
    command_id = str(uuid4())
    created = client.post(
        "/api/v1/humanoid/proposals",
        json={
            "command_id": command_id,
            "issued_at": time.time(),
            "duration_seconds": 2,
            "targets": {"head_yaw": 0.1},
        },
    )
    assert created.status_code == 201, created.text
    assert client.post("/api/v1/stop").status_code == 200
    history = client.get("/api/v1/humanoid/state").json()["history"]
    assert [plan["request"]["command_id"] for plan in history] == [command_id]
    client.cookies.clear()
    sign_in(client, issuer, person(email=first.email))
    assert client.get("/api/v1/humanoid/state").json()["history"] == []
    client.cookies.clear()
    sign_in(client, issuer, replace(first, email="changed-contact@example.test"))
    history = client.get("/api/v1/humanoid/state").json()["history"]
    assert [plan["request"]["command_id"] for plan in history] == [command_id]
    # Detailed history has a fixed retention cap; ownership metadata follows it.
    client.portal.call(lambda: app.state.sim.humanoid.history.clear())
    assert client.get("/api/v1/humanoid/state").json()["history"] == []
    assert not app.state.sim.identity_humanoid_owners


def test_inspection_approval_records_actual_shared_mfa_and_stable_actor(environment):
    app, client, issuer, _ = environment
    principal = person()
    sign_in(client, issuer, principal)
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
    created = client.post(
        "/api/v1/missions",
        json={
            "command_id": str(uuid4()),
            "robot_profile_id": "sim-inspector",
            "scenario": "normal",
            "seed": 101,
        },
    )
    assert created.status_code == 201, created.text
    detail = created.json()
    approved = client.post(
        f"/api/v1/missions/{detail['id']}/approve",
        json={"confirmed": True, "expected_plan_sha256": detail["record"]["plan_sha256"]},
    )
    assert approved.status_code == 200, approved.text
    event = next(
        value for value in approved.json()["record"]["events"] if value["kind"] == "approved"
    )
    owner = client.portal.call(
        lambda: app.state.sim.db.execute(
            "SELECT owner FROM tasks WHERE id=?", (detail["id"],)
        ).fetchone()[0]
    )
    assert event["actor"] == owner.removeprefix("sso:")
    assert len(event["actor"]) == 64 and principal.email not in event["actor"]
    assert event["detail"] == "Onesa MFA and explicit exact-plan approval"


def test_global_admin_uses_existing_permission_guard_and_revocation(environment):
    app, client, issuer, _ = environment
    principal = person(admin=True)
    current = sign_in(client, issuer, principal)
    assert current["role"] == "administrator" and current["global_administrator"]
    before = client.get("/api/v1/admin/physical-operation").json()
    response = client.post(
        "/api/v1/admin/physical-operation/allow",
        json={
            "confirmed": True,
            "expected_revision": before["revision"],
        },
    )
    assert response.status_code == 200
    assert response.json()["physical_actuation"] is False
    assert response.json()["controller_connection"] == "not_connected"
    for token in issuer.tokens:
        issuer.tokens[token] = replace(principal, roles=(), product_roles={"robot": "operator"})
    assert client.get("/api/v1/admin/physical-operation").status_code == 403
    assert app.state.sim.physical_permission_owner is None


def test_entitlement_removal_revokes_local_session_and_stops_work(environment):
    app, client, issuer, _ = environment
    principal = person()
    sign_in(client, issuer, principal)
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
    created = client.post("/api/v1/tasks", json=proposal()).json()
    response = client.post(f"/api/v1/tasks/{created['id']}/approve", json={"confirmed": True})
    assert response.status_code == 200
    assert app.state.sim.active is not None
    for token in issuer.tokens:
        issuer.tokens[token] = replace(principal, applications=())
    assert client.get("/api/v1/status").status_code == 403
    assert app.state.sim.active is None and client.portal.call(lambda: app.state.sim.stopped)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_request_rechecks_local_revocation_after_identity_network_wait(environment, monkeypatch):
    app, client, issuer, _ = environment
    sign_in(client, issuer, person())
    entered, release = threading.Event(), threading.Event()

    def waiting(config, token):
        entered.set()
        assert release.wait(5)
        return issuer.validate(config, token)

    monkeypatch.setattr(identity, "validate_session", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.post, "/api/v1/reset", json={"confirmed": True})
        assert entered.wait(5)

        def revoke():
            app.state.sim.db.execute("DELETE FROM sessions")
            app.state.sim.db.commit()
            app.state.sim.identity_sessions.clear()

        client.portal.call(revoke)
        release.set()
        assert response.result(timeout=5).status_code == 401
    assert client.portal.call(lambda: app.state.sim.stopped)


@pytest.mark.parametrize("kind", ["joint", "inspection"])
def test_stop_then_reset_cannot_resume_an_approval_waiting_on_identity(
    environment, monkeypatch, kind
):
    app, client, issuer, _ = environment
    sign_in(client, issuer, person())
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
    if kind == "joint":
        created = client.post("/api/v1/tasks", json=proposal()).json()
        path = f"/api/v1/tasks/{created['id']}/approve"
        approval = {"confirmed": True}
    else:
        created = client.post(
            "/api/v1/missions",
            json={
                "command_id": str(uuid4()),
                "robot_profile_id": "sim-inspector",
                "scenario": "normal",
                "seed": 101,
            },
        ).json()
        path = f"/api/v1/missions/{created['id']}/approve"
        approval = {"confirmed": True, "expected_plan_sha256": created["record"]["plan_sha256"]}
    entered, release = threading.Event(), threading.Event()
    calls = 0

    def waiting(config, token):
        nonlocal calls
        calls += 1
        # The fourth live validation follows the durable running acknowledgement,
        # immediately before execution starts. Other requests remain independent.
        if calls == 4:
            entered.set()
            assert release.wait(5)
        return issuer.validate(config, token)

    monkeypatch.setattr(identity, "validate_session", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.post, path, json=approval)
        try:
            assert entered.wait(5)
            assert client.post("/api/v1/stop").status_code == 200
            # Reset is an explicit new action, never permission for the previous
            # pending approval to resume after its identity response arrives.
            assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
        finally:
            release.set()
        assert response.result(timeout=5).status_code == 409
    assert app.state.sim.active is None
    assert client.portal.call(lambda: app.state.sim.stopped)
    stored = client.portal.call(
        lambda: app.state.sim.db.execute(
            "SELECT state FROM tasks WHERE id=?", (created["id"],)
        ).fetchone()[0]
    )
    assert stored == "cancelled"


def test_logout_clears_local_authority_and_requests_global_signout(environment):
    app, client, issuer, _ = environment
    sign_in(client, issuer, person())
    response = client.post("/api/v1/auth/logout")
    assert response.json()["status"] == "signed_out"
    assert response.json()["identity_signed_out"] is True
    assert issuer.revocations[-1][1] is True
    assert not app.state.sim.identity_sessions
    assert client.get("/api/v1/auth/me").status_code == 401


def test_identity_outage_never_prevents_local_signout(environment):
    app, client, issuer, _ = environment
    sign_in(client, issuer, person())
    issuer.unavailable = True
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200 and response.json()["identity_signed_out"] is False
    assert not app.state.sim.identity_sessions
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_central_grant_does_not_claim_global_signout(environment):
    app, client, issuer, _ = environment
    sign_in(client, issuer, person())
    issuer.tokens.clear()
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 200 and response.json()["identity_signed_out"] is False
    assert not app.state.sim.identity_sessions
    assert client.get("/api/v1/auth/me").status_code == 401


def test_stop_takes_effect_before_waiting_for_central_identity(environment, monkeypatch):
    app, client, issuer, _ = environment
    sign_in(client, issuer, person())
    assert client.post("/api/v1/reset", json={"confirmed": True}).status_code == 200
    assert not client.portal.call(lambda: app.state.sim.stopped)
    entered, release = threading.Event(), threading.Event()

    def waiting(config, token):
        entered.set()
        assert release.wait(5)
        return issuer.validate(config, token)

    monkeypatch.setattr(identity, "validate_session", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(client.post, "/api/v1/stop")
        assert entered.wait(5)
        assert client.portal.call(lambda: app.state.sim.stopped)
        release.set()
        assert response.result(timeout=5).status_code == 200


def test_identity_mode_preserves_hardware_and_callback_configuration_boundaries(environment):
    _, _, _, config = environment
    values = config.model_dump()
    with pytest.raises(ValidationError):
        Settings(**{**values, "actuation_enabled": True})
    with pytest.raises(ValidationError):
        Settings(**{**values, "identity_callback_url": "https://attacker.example/callback"})
