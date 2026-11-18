"""Native controller against real routes and a local, cryptographic synthetic issuer.

This verifies software state/API integration, not installed-device/browser acceptance.
"""

import base64
import hashlib
import json
import os
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import jwt
import uvicorn
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException, Request

from robot_api import managed_oidc
from robot_api.main import Settings, create_app
from robot_api.security import password_hash


def test_native_operator_real_routes_and_watchdog(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    control = str(uuid4())
    stable = str(uuid4())
    codes = {}
    provider = {"role": "operator"}
    config = Settings(
        operator_password_hash=password_hash("synthetic-native-operator-only"),
        database_path=tmp_path / "native.sqlite3",
        public_origin=origin,
        heartbeat_seconds=1,
        identity_enabled=True,
        identity_issuer="https://auth.onesa.eu/",
        identity_audience="https://api.robot.example.test",
        identity_client_id="synthetic-native-operator",
        identity_client_secret="synthetic-" + "x" * 40,
        identity_callback_url=origin + "/api/v1/auth/identity/callback",
        identity_public_jwks=json.dumps({"keys": [{**public, "kid": "synthetic"}]}),
        identity_mapping_issuer="https://legacy.example.test/identity",
        identity_account_url="https://account.example.test/",
    )
    app = create_app(config)

    @app.post("/_synthetic_native")
    async def fixture(request: Request):
        if request.headers.get("x-synthetic-control") != control:
            raise HTTPException(403)
        value = await request.json()
        if value["action"] == "mint":
            params = value["parameters"]
            assert params["client_id"] == config.identity_client_id
            assert params["redirect_uri"] == config.identity_callback_url
            assert params["code_challenge_method"] == "S256"
            code = str(uuid4())
            codes[code] = params
            return {"code": code}
        if value["action"] == "expire":
            app.state.sim.db.execute("UPDATE sessions SET expires=0")
            app.state.sim.db.commit()
            app.state.sim.tick()
            return {"expired": True}
        if value["action"] == "viewer":
            provider["role"] = "viewer"
            return {"role": "viewer"}
        raise HTTPException(400)

    def issue(issuer, path, **kwargs):
        assert issuer.issuer == config.identity_issuer and path == "oauth/token"
        data = kwargs["data"]
        params = codes.pop(data["code"])
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert challenge == params["code_challenge"]
        assert data["redirect_uri"] == config.identity_callback_url
        now = int(time.time())
        shared = {
            "iss": config.identity_issuer,
            "sub": "synthetic-provider-subject",
            "iat": now,
            "exp": now + 240,
        }
        access = {
            **shared,
            "aud": config.identity_audience,
            managed_oidc.NS + "products": ["robot"],
            managed_oidc.NS + "roles": [provider["role"]],
            managed_oidc.NS + "user_id": stable,
        }
        if params.get("acr_values") == managed_oidc.MFA_ACR:
            access[managed_oidc.NS + "mfa_time"] = now
        profile = {
            **shared,
            "aud": config.identity_client_id,
            "nonce": params["nonce"],
            "email_verified": True,
            "email": "operator@example.test",
            "name": "Synthetic Operator",
            "auth_time": now,
        }
        return {
            "token_type": "Bearer",
            **{
                name: jwt.encode(value, key, algorithm="RS256", headers={"kid": "synthetic"})
                for name, value in [("access_token", access), ("id_token", profile)]
            },
        }

    monkeypatch.setattr(managed_oidc, "_request", issue)
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        script = (
            Path(__file__).parents[2] / "onesa-ai-robot-frontend/mobile/acceptance/real-routes.mjs"
        )
        result = subprocess.run(
            ["node", str(script), origin],
            env={**os.environ, "ONESA_SYNTHETIC_NATIVE_CONTROL": control},
            text=True,
            capture_output=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "PASS native controller real-route software acceptance"
        assert not codes
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()
