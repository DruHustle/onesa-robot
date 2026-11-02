"""Onesa identity protocol v1: confidential code exchange and live authorization.

The ID token is deliberately not used as an identity assertion. Identity and current
permissions come only from the configured issuer's authenticated /session endpoint,
bound to the opaque token and this confidential client. No discovery redirects,
browser bearer tokens, persistent role cache, or cross-product database access.
"""

import base64
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus, urlencode, urlsplit
from uuid import UUID

import httpx

PRODUCTS = frozenset({"ai", "drone", "robot"})
PRODUCT_ROLES = {
    "ai": {"user", "admin"},
    "drone": {"viewer", "operator", "admin"},
    "robot": {"viewer", "operator", "admin"},
}
MAX_RESPONSE_BYTES = 65536


class IdentityError(Exception):
    """Safe fixed error; never include tokens, credentials or upstream response text."""

    def __init__(self, code: str, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass(frozen=True)
class IdentityConfig:
    issuer: str
    client_id: str
    client_secret: str = field(repr=False)
    redirect_uri: str
    product: str
    allow_local_http: bool = False

    def __post_init__(self) -> None:
        for name, value in (("issuer", self.issuer), ("callback", self.redirect_uri)):
            parsed = urlsplit(value)
            local = self.allow_local_http and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            if (
                not value
                or len(value) > 1000
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or (parsed.scheme != "https" and not (local and parsed.scheme == "http"))
            ):
                raise ValueError(f"Onesa identity {name} requires an exact trusted HTTPS URL")
        if self.issuer.endswith("/"):
            raise ValueError("Onesa identity issuer must not end with a slash")
        if (
            not self.client_id
            or len(self.client_id) > 200
            or len(self.client_secret) < 32
            or self.product not in PRODUCTS
        ):
            raise ValueError("Onesa identity requires a registered confidential product client")


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    email: str
    name: str
    expires_at: int
    auth_time: int
    amr: tuple[str, ...]
    roles: tuple[str, ...]
    applications: tuple[str, ...]
    product_roles: dict[str, str]
    memberships: tuple[dict[str, str], ...]

    @property
    def global_admin(self) -> bool:
        return "global_admin" in self.roles and bool({"otp", "mfa"}.intersection(self.amr))

    def fresh(self, *, mfa: bool = False, max_age: int = 300) -> bool:
        instant = int(time.time())
        return (
            0 <= instant - self.auth_time <= max_age
            and self.expires_at > instant
            and (not mfa or bool({"otp", "mfa"}.intersection(self.amr)))
        )


def new_transaction() -> tuple[str, str, str]:
    """Return independent state, nonce and S256 verifier for server-side custody."""
    return secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(48)


def authorization_url(
    config: IdentityConfig, state: str, nonce: str, verifier: str, *, reauthenticate: bool = False
) -> str:
    if not all(re.fullmatch(r"[A-Za-z0-9_-]{40,128}", item) for item in (state, nonce, verifier)):
        raise ValueError("Invalid server-generated identity transaction")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    params = {
        "client_id": config.client_id,
        "redirect_uri": config.redirect_uri,
        "response_type": "code",
        "scope": "openid email profile onesa",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge.decode().rstrip("="),
        "code_challenge_method": "S256",
    }
    if reauthenticate:
        params.update(prompt="login", max_age="0", acr_values="urn:onesa:mfa")
    return config.issuer + "/auth?" + urlencode(params)


def _request(
    config: IdentityConfig,
    endpoint: str,
    *,
    data: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    own_client = client is None
    connection = client or httpx.Client(timeout=httpx.Timeout(5.0), follow_redirects=False)
    try:
        with connection.stream(
            "POST",
            config.issuer + endpoint,
            auth=httpx.BasicAuth(
                quote_plus(config.client_id, safe=""), quote_plus(config.client_secret, safe="")
            ),
            data=data,
            json=body,
            headers={"Accept": "application/json"},
            follow_redirects=False,
            timeout=5.0,
        ) as response:
            if response.status_code in {400, 401, 403}:
                raise IdentityError("identity_rejected", "Your Onesa session is no longer valid")
            if response.status_code != 200:
                raise IdentityError(
                    "identity_unavailable", "Onesa sign-in is temporarily unavailable", 503
                )
            result = bytearray()
            for block in response.iter_bytes():
                result.extend(block)
                if len(result) > MAX_RESPONSE_BYTES:
                    raise IdentityError(
                        "identity_unavailable", "Onesa sign-in response is invalid", 503
                    )
            value = json.loads(result)
            if not isinstance(value, dict):
                raise ValueError("Invalid response")
            return value
    except IdentityError:
        raise
    except (httpx.HTTPError, ValueError, TypeError, UnicodeError, RecursionError):
        raise IdentityError(
            "identity_unavailable", "Onesa sign-in is temporarily unavailable", 503
        ) from None
    finally:
        if own_client:
            connection.close()


def exchange_code(
    config: IdentityConfig, code: str, verifier: str, *, client: httpx.Client | None = None
) -> str:
    if not code or len(code) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", verifier):
        raise IdentityError("identity_callback_invalid", "Sign-in has expired. Please start again")
    result = _request(
        config,
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": config.redirect_uri,
        },
        client=client,
    )
    token = result.get("access_token")
    token_type = result.get("token_type")
    if (
        not isinstance(token, str)
        or not 20 <= len(token) <= 4096
        or not isinstance(token_type, str)
        or token_type.casefold() != "bearer"
    ):
        raise IdentityError("identity_unavailable", "Onesa sign-in response is invalid", 503)
    # An unvalidated id_token MUST NOT establish identity or permissions.
    return token


def _strings(value: Any, allowed: set[str] | frozenset[str] | None = None) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or len(value) > 10
        or any(not isinstance(item, str) or len(item) > 80 for item in value)
        or len(set(value)) != len(value)
        or (allowed is not None and not set(value) <= allowed)
    ):
        raise ValueError("Invalid assertion")
    return tuple(value)


def validate_session(
    config: IdentityConfig, token: str, *, client: httpx.Client | None = None
) -> Identity:
    if not isinstance(token, str) or not 20 <= len(token) <= 4096:
        raise IdentityError("identity_rejected", "Your Onesa session is no longer valid")
    raw = _request(config, "/session", body={"token": token}, client=client)
    if raw.get("active") is not True:
        raise IdentityError("identity_rejected", "Your Onesa session is no longer valid")
    try:
        instant = int(time.time())
        subject, email, name = raw["sub"], raw["email"], raw["name"]
        if (
            raw["iss"] != config.issuer
            or raw["aud"] != config.client_id
            or not isinstance(subject, str)
            or str(UUID(subject)) != subject
            or not isinstance(email, str)
            or not re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,189}", email)
            or len(email) > 254
            or raw["email_verified"] is not True
            or not isinstance(name, str)
            or not name.strip()
            or len(name) > 200
            or type(raw["exp"]) is not int
            or not instant < raw["exp"] <= instant + 86400
            or type(raw["auth_time"]) is not int
            or not 0 < raw["auth_time"] <= instant + 30
        ):
            raise ValueError("Invalid assertion")
        amr = _strings(raw["amr"])
        roles = _strings(raw["onesa_roles"], {"global_admin"})
        applications = _strings(raw["onesa_applications"], PRODUCTS)
        product_roles = raw["onesa_product_roles"]
        if not isinstance(product_roles, dict) or any(
            key not in PRODUCT_ROLES or value not in PRODUCT_ROLES[key]
            for key, value in product_roles.items()
        ):
            raise ValueError("Invalid assertion")
        memberships = raw["onesa_memberships"]
        if not isinstance(memberships, list) or len(memberships) > 100:
            raise ValueError("Invalid assertion")
        for membership in memberships:
            if (
                not isinstance(membership, dict)
                or set(membership) != {"product", "tenant_id", "role"}
                or membership["product"] != "drone"
                or membership["role"] not in PRODUCT_ROLES["drone"]
                or str(UUID(membership["tenant_id"])) != membership["tenant_id"]
            ):
                raise ValueError("Invalid assertion")
        if len({(item["product"], item["tenant_id"]) for item in memberships}) != len(memberships):
            raise ValueError("Duplicate tenant membership")
        result = Identity(
            config.issuer,
            subject,
            email.casefold(),
            name.strip(),
            raw["exp"],
            raw["auth_time"],
            amr,
            roles,
            applications,
            dict(product_roles),
            tuple(dict(item) for item in memberships),
        )
        if ("global_admin" in roles or product_roles.get(config.product) == "admin") and not {
            "otp",
            "mfa",
        }.intersection(amr):
            raise IdentityError(
                "identity_mfa_required", "Complete MFA for administrator access", 403
            )
        if not result.global_admin and config.product not in applications:
            raise IdentityError(
                "application_access_required",
                "Ask an administrator for access to this application",
                403,
            )
        if not result.global_admin and config.product not in product_roles:
            raise ValueError("Missing product role")
        return result
    except IdentityError:
        raise
    except (KeyError, ValueError, TypeError, AttributeError):
        raise IdentityError(
            "identity_unavailable", "Onesa sign-in response is invalid", 503
        ) from None


def revoke_session(
    config: IdentityConfig,
    token: str,
    *,
    all_sessions: bool = False,
    client: httpx.Client | None = None,
) -> bool:
    body: dict[str, Any] = {"token": token}
    if all_sessions:
        body["all_sessions"] = True
    result = _request(config, "/revoke", body=body, client=client)
    if result.get("ok") is not True:
        raise IdentityError("identity_unavailable", "Onesa sign-out is unavailable", 503)
    # An idempotent response to an expired token cannot prove global sign-out.
    return not all_sessions or result.get("all_sessions_revoked") is True
