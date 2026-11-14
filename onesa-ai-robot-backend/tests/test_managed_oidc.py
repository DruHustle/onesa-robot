"""Local Auth0-compatible issuance and Product-owned resource identity."""

import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from robot_api.managed_oidc import NS, exchange, validate
from robot_api.onesa_identity import (
    IdentityConfig,
    IdentityError,
    authorization_url,
    new_transaction,
)


@pytest.fixture
def signed():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    config = IdentityConfig(
        "https://issuer.example/",
        "robot-web",
        "test" * 10,
        "https://ai.example/callback",
        "robot",
        False,
        "https://api.robot.example",
        json.dumps({"keys": [{**public, "kid": "fixture"}]}),
        "https://legacy.example/api/v1/identity",
        "https://admin.example/account",
    )
    uid = str(uuid4())

    def pair(at=None, profile=None):
        now = int(time.time())
        shared = {"iss": config.issuer, "sub": "auth0|synthetic", "iat": now, "exp": now + 240}
        access = {
            **shared,
            "aud": config.audience,
            NS + "products": ["robot"],
            NS + "roles": ["viewer"],
            NS + "user_id": uid,
            **(at or {}),
        }
        user = {
            **shared,
            "aud": config.client_id,
            "email_verified": True,
            "email": "member@example.com",
            "name": "Member",
            "nonce": "registered-nonce",
            **(profile or {}),
        }
        return {
            k: jwt.encode(v, key, algorithm="RS256", headers={"kid": "fixture"})
            for k, v in [("access_token", access), ("id_token", user)]
        }

    return config, uid, pair


def test_provider_exchange_and_local_key_restart(signed):
    config, uid, pair = signed
    state, nonce, verifier = new_transaction()
    url = urlsplit(authorization_url(config, state, nonce, verifier, reauthenticate=True))
    q = parse_qs(url.query)
    assert (
        url.path == "/authorize"
        and q["audience"] == [config.audience]
        and q["code_challenge_method"] == ["S256"]
    )

    def response(req):
        assert req.url.path == "/oauth/token" and req.method == "POST"
        assert parse_qs(req.content.decode())["code_verifier"] == [verifier]
        return httpx.Response(200, json={**pair(), "token_type": "Bearer"})

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        packed = exchange(config, "controlled-code", verifier, "registered-nonce", client)
        with pytest.raises(IdentityError):
            exchange(config, "controlled-code", verifier, "wrong-nonce", client)
    recovered = validate(replace(config), packed)
    assert recovered.subject == uid and recovered.issuer == config.issuer
    assert recovered.email == "member@example.com" and not recovered.global_admin


@pytest.mark.parametrize(
    ("changes", "profile"),
    [
        ({"aud": "other"}, {}),
        ({NS + "products": ["ai"]}, {}),
        ({NS + "user_id": "member@example.com"}, {}),
        ({NS + "roles": ["admin"]}, {}),
        ({"exp": int(time.time()) + 600}, {}),
        ({"exp": 10}, {}),
        ({}, {"sub": "other"}),
        ({}, {"email_verified": False}),
        ({}, {"aud": "other"}),
    ],
)
def test_mismatch_or_privilege_denied(signed, changes, profile):
    config, _, pair = signed
    with pytest.raises(IdentityError):
        validate(config, json.dumps(pair(changes, profile)))


@pytest.mark.parametrize("mobile", [False, True])
def test_console_callback_retains_the_legacy_owner_during_outage(
    tmp_path, monkeypatch, signed, mobile
):
    import hashlib

    from fastapi.testclient import TestClient

    from robot_api import managed_oidc
    from robot_api.main import Settings, create_app
    from robot_api.security import password_hash

    provider, stable, issue = signed
    cfg = Settings(
        operator_password_hash=password_hash("synthetic-password-for-simulator"),
        database_path=tmp_path / "pilot.sqlite3",
        identity_enabled=True,
        identity_issuer=provider.issuer,
        identity_audience=provider.audience,
        identity_client_id=provider.client_id,
        identity_callback_url="http://localhost:3431/api/v1/auth/identity/callback",
        identity_client_secret=provider.client_secret,
        identity_public_jwks=provider.public_jwks,
        identity_mapping_issuer=provider.mapping_issuer,
        identity_account_url=provider.account_url,
    )
    app = create_app(cfg)
    with TestClient(app, base_url=cfg.public_origin) as client:
        client.headers["Origin"] = cfg.public_origin
        start = client.post("/api/v1/auth/identity/start", json={"mobile": mobile}).json()
        params = parse_qs(urlsplit(start["authorization_url"]).query)

        def respond(config, path, **kwargs):
            assert path == "oauth/token" and kwargs["data"]["code"] == "one-use"
            return {**issue(profile={"nonce": params["nonce"][0]}), "token_type": "Bearer"}

        monkeypatch.setattr(managed_oidc, "_request", respond)
        cb = client.get(
            "/api/v1/auth/identity/callback",
            params={"state": params["state"][0], "code": "one-use"},
            follow_redirects=False,
        )
        assert cb.status_code == 303
        if mobile:
            returned = urlsplit(cb.headers["location"])
            assert returned.scheme == "onesarobot" and returned.netloc == "account"
            assert returned.path == "/callback" and "set-cookie" not in cb.headers
            data = {key: values[0] for key, values in parse_qs(returned.query).items()}
            assert data == {"state": params["state"][0], "code": "one-use"}
            endpoint = "/api/v1/auth/identity/mobile/exchange"
            device_cookie = client.cookies.get("robot_identity")
            client.cookies.clear()
            assert client.post(endpoint, json=data).status_code == 401
            client.cookies.set("robot_identity", device_cookie)
            assert client.post(endpoint, json=data).status_code == 200
            assert client.post(endpoint, json=data).status_code == 401
        else:
            assert cb.headers["location"] == "/console"
        expected = (
            "sso:"
            + hashlib.sha256((provider.mapping_issuer + "\x00" + stable).encode()).hexdigest()
        )
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200 and me.json()["role"] == "viewer"
        owner = client.portal.call(
            lambda: app.state.sim.db.execute("SELECT name FROM sessions").fetchone()[0]
        )
        assert owner == expected

        def offline(*args, **kwargs):
            raise AssertionError("Unexpected online authority check")

        monkeypatch.setattr(managed_oidc, "_request", offline)
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.get("/api/v1/tasks").status_code == 200
