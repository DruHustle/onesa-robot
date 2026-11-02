"""Protocol contract tests use an isolated HTTP transport, never a hosted issuer."""

import base64
import hashlib
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, unquote_plus, urlsplit
from uuid import uuid4

import httpx
import pytest

from robot_api.onesa_identity import (
    IdentityConfig,
    IdentityError,
    authorization_url,
    exchange_code,
    new_transaction,
    revoke_session,
    validate_session,
)

CONFIG = IdentityConfig(
    "https://account.example.test/api/v1/identity",
    "ai-staging",
    "synthetic-client-secret-" + "x" * 32,
    "https://ai.example.test/api/v1/auth/onesa/callback",
    "ai",
)
TOKEN = "synthetic-test-access-token-" + "x" * 32


def assertion(**changes):
    return {
        "active": True,
        "iss": CONFIG.issuer,
        "aud": CONFIG.client_id,
        "sub": str(uuid4()),
        "email": "person@example.test",
        "email_verified": True,
        "name": "Synthetic Person",
        "exp": int(time.time()) + 3600,
        "auth_time": int(time.time()),
        "amr": ["pwd"],
        "onesa_roles": [],
        "onesa_applications": ["ai"],
        "onesa_product_roles": {"ai": "user"},
        "onesa_memberships": [],
        **changes,
    }


def transport(payload, status=200):
    def handler(request):
        assert request.url == CONFIG.issuer + "/session"
        assert request.method == "POST"
        assert request.headers["authorization"].startswith("Basic ")
        assert json.loads(request.content) == {"token": TOKEN}
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_authorization_has_pkce_and_fresh_mfa_step_up():
    state, nonce, verifier = new_transaction()
    parsed = urlsplit(authorization_url(CONFIG, state, nonce, verifier, reauthenticate=True))
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https" and parsed.path.endswith("/identity/auth")
    assert query["state"] == [state] and query["nonce"] == [nonce]
    assert query["code_challenge_method"] == ["S256"]
    assert query["code_challenge"] == [
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    ]
    assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    assert query["acr_values"] == ["urn:onesa:mfa"]
    assert CONFIG.client_secret not in parsed.query and verifier not in parsed.query


def test_code_exchange_never_uses_unvalidated_id_token():
    _, _, verifier = new_transaction()

    def handler(request):
        assert request.url == CONFIG.issuer + "/token"
        params = parse_qs(request.content.decode())
        assert params["code_verifier"] == [verifier]
        assert params["redirect_uri"] == [CONFIG.redirect_uri]
        assert params["grant_type"] == ["authorization_code"]
        return httpx.Response(
            200,
            json={
                "access_token": TOKEN,
                "token_type": "Bearer",
                "id_token": "untrusted-admin-assertion",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert exchange_code(CONFIG, "one-use-code", verifier, client=client) == TOKEN


def test_oauth_client_basic_credentials_are_form_encoded_and_global_logout_is_explicit():
    config = replace(
        CONFIG, client_id="ai:+ %", client_secret="synthetic+:percent% space-" + "x" * 32
    )

    def handler(request):
        raw = base64.b64decode(request.headers["authorization"].removeprefix("Basic ")).decode()
        client_id, secret = raw.split(":", 1)
        assert unquote_plus(client_id) == config.client_id
        assert unquote_plus(secret) == config.client_secret
        assert json.loads(request.content) == {"token": TOKEN, "all_sessions": True}
        return httpx.Response(200, json={"ok": True, "all_sessions_revoked": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert revoke_session(config, TOKEN, all_sessions=True, client=client)


@pytest.mark.parametrize("proof", [None, False, "true", 1])
def test_idempotent_revoke_without_explicit_global_proof_is_partial_sign_out(proof):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"ok": True, "all_sessions_revoked": proof})
        )
    ) as client:
        assert not revoke_session(CONFIG, TOKEN, all_sessions=True, client=client)
        assert revoke_session(CONFIG, TOKEN, client=client)


def test_session_returns_immutable_subject_and_current_product_entitlements():
    payload = assertion()
    with transport(payload) as client:
        identity = validate_session(CONFIG, TOKEN, client=client)
    assert identity.subject == payload["sub"] and identity.email == payload["email"]
    assert identity.applications == ("ai",) and not identity.global_admin
    assert identity.fresh() and not identity.fresh(mfa=True)


def test_global_admin_requires_central_mfa_and_retains_freshness():
    with transport(
        assertion(onesa_roles=["global_admin"], amr=["pwd", "otp"], onesa_applications=[])
    ) as client:
        identity = validate_session(CONFIG, TOKEN, client=client)
    assert identity.global_admin and identity.fresh(mfa=True)
    assert not replace(identity, auth_time=int(time.time()) - 301).fresh(mfa=True)
    with transport(assertion(onesa_roles=["global_admin"])) as client:
        with pytest.raises(IdentityError, match="MFA"):
            validate_session(CONFIG, TOKEN, client=client)


@pytest.mark.parametrize("product", ["ai", "drone", "robot"])
def test_each_product_administrator_requires_mfa(product):
    config = replace(CONFIG, product=product)
    payload = assertion(onesa_applications=[product], onesa_product_roles={product: "admin"})
    with transport(payload) as client:
        with pytest.raises(IdentityError, match="MFA"):
            validate_session(config, TOKEN, client=client)
    payload["amr"] = ["pwd", "otp"]
    with transport(payload) as client:
        assert validate_session(config, TOKEN, client=client).product_roles[product] == "admin"


@pytest.mark.parametrize(
    "change",
    [
        {"active": False},
        {"active": "true"},
        {"iss": "https://other.example.test"},
        {"aud": "drone-staging"},
        {"sub": "email@example.test"},
        {"sub": None},
        {"email_verified": False},
        {"email_verified": "true"},
        {"email": "a\nb@example.test"},
        {"exp": 1},
        {"exp": True},
        {"auth_time": True},
        {"auth_time": 999999999999},
        {"onesa_roles": "global_admin"},
        {"onesa_roles": ["admin"]},
        {"onesa_applications": ["drone"]},
        {"onesa_applications": ["ai", "ai"]},
        {"onesa_product_roles": {"ai": "owner"}},
        {"onesa_product_roles": {}},
        {"onesa_memberships": [{"product": "drone", "tenant_id": "anything", "role": "admin"}]},
        {"name": ""},
        {"amr": [], "onesa_roles": ["global_admin"]},
    ],
)
def test_invalid_or_unentitled_assertions_fail_closed(change):
    with transport(assertion(**change)) as client:
        with pytest.raises(IdentityError):
            validate_session(CONFIG, TOKEN, client=client)


def test_revocation_and_role_removal_are_observed_on_next_request():
    current = assertion(onesa_roles=["global_admin"], amr=["pwd", "otp"])

    def handler(request):
        assert request.url == CONFIG.issuer + "/session"
        return httpx.Response(200, json=current)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert validate_session(CONFIG, TOKEN, client=client).global_admin
        current["onesa_roles"] = []
        assert not validate_session(CONFIG, TOKEN, client=client).global_admin
        current["active"] = False
        with pytest.raises(IdentityError):
            validate_session(CONFIG, TOKEN, client=client)


@pytest.mark.parametrize("status", [302, 307, 400, 401, 403, 429, 500, 503])
def test_redirect_and_error_never_forward_secrets_to_another_origin(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            json={"error": "private upstream detail"},
            headers={"location": "https://attacker.example.test/"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        with pytest.raises(IdentityError) as failed:
            validate_session(CONFIG, TOKEN, client=client)
    assert len(requests) == 1 and requests[0].url.host == "account.example.test"
    assert "private" not in str(failed.value) and TOKEN not in str(failed.value)


def test_oversize_response_is_rejected_and_revocation_is_confidential():
    def oversized(request):
        return httpx.Response(200, content=b" " * 65537)

    with httpx.Client(transport=httpx.MockTransport(oversized)) as client:
        with pytest.raises(IdentityError):
            validate_session(CONFIG, TOKEN, client=client)

    def revoke(request):
        assert request.url == CONFIG.issuer + "/revoke"
        assert json.loads(request.content) == {"token": TOKEN}
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(revoke)) as client:
        revoke_session(CONFIG, TOKEN, client=client)


def test_deep_nested_json_is_a_safe_error():
    def nested(request):
        return httpx.Response(200, content=b"[" * 1500 + b"0" + b"]" * 1500)

    with httpx.Client(transport=httpx.MockTransport(nested)) as client:
        with pytest.raises(IdentityError, match="temporarily unavailable"):
            validate_session(CONFIG, TOKEN, client=client)


@pytest.mark.parametrize(
    "issuer",
    [
        "http://account.example.test",
        "https://a:b@example.test",
        "https://example.test/",
        "https://example.test?x=1",
    ],
)
def test_invalid_config_fails_at_startup(issuer):
    with pytest.raises(ValueError):
        replace(CONFIG, issuer=issuer)
