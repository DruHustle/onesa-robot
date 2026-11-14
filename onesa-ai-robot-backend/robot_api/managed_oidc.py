"""Managed OIDC Code/PKCE. Public keys are provisioned independently of the issuer."""

import hmac
import json
import re
import time
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
import jwt

from .onesa_identity import PRODUCT_ROLES, Identity, IdentityConfig, IdentityError, _request

NS = "https://onesa.eu/claims/"
MFA_ACR = "http://schemas.openid.net/pape/policies/2007/06/multi-factor"


def rejected() -> IdentityError:
    return IdentityError("identity_rejected", "Your Onesa session is no longer valid")


def authorize(config: IdentityConfig, params: dict[str, str], reauthenticate: bool) -> str:
    params = {**params, "scope": "openid email profile", "audience": config.audience}
    if reauthenticate:
        params["acr_values"] = MFA_ACR
    return config.issuer + "authorize?" + urlencode(params)


def public_keys(raw: str) -> dict[str, Any]:
    keys = json.loads(raw)["keys"]
    if not isinstance(keys, list) or not 1 <= len(keys) <= 8:
        raise ValueError("Provision a bounded current/next RSA signing key set")
    by_id = {}
    for key in keys:
        if (
            not isinstance(key, dict)
            or key.get("kty") != "RSA"
            or key.get("alg", "RS256") != "RS256"
            or key.get("use", "sig") != "sig"
            or "verify" not in key.get("key_ops", ["verify"])
            or set(key).intersection({"d", "p", "q", "dp", "dq", "qi", "oth", "k"})
            or not isinstance(key.get("kid"), str)
            or not 0 < len(key["kid"]) <= 200
            or key["kid"] in by_id
        ):
            raise ValueError("Invalid or duplicate public signing key")
        public = jwt.PyJWK.from_dict(key, algorithm="RS256").key
        if getattr(public, "key_size", 0) < 2048:
            raise ValueError("Signing key must have at least 2048 bits")
        by_id[key["kid"]] = public
    return by_id


def claims(config: IdentityConfig, token: str, audience: str) -> dict[str, Any]:
    if not isinstance(token, str) or not 20 <= len(token) <= 12000:
        raise rejected()
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256" or header.get("crit"):
        raise rejected()
    key = public_keys(config.public_jwks).get(header.get("kid"))
    if key is None:
        raise rejected()
    return jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=config.issuer,
        audience=audience,
        options={"require": ["sub", "iss", "aud", "iat", "exp"]},
    )


def validate(config: IdentityConfig, packed: str, nonce: str | None = None) -> Identity:
    try:
        pair = json.loads(packed)
        access = claims(config, pair["access_token"], config.audience)
        profile = claims(config, pair["id_token"], config.client_id)
        if nonce is not None and (
            not isinstance(profile.get("nonce"), str)
            or not hmac.compare_digest(profile["nonce"], nonce)
        ):
            raise rejected()
        if access["sub"] != profile["sub"] or profile.get("email_verified") is not True:
            raise rejected()
        if "azp" in access and access["azp"] != config.client_id:
            raise rejected()
        if (
            "azp" in profile
            and profile["azp"] != config.client_id
            or isinstance(profile["aud"], list)
            and len(profile["aud"]) > 1
            and profile.get("azp") != config.client_id
        ):
            raise rejected()
        if (
            type(access["exp"]) is not int
            or type(access["iat"]) is not int
            or not (0 < access["exp"] - access["iat"] <= 300)
        ):
            raise rejected()
        stable_id = access[NS + "user_id"]
        if not isinstance(stable_id, str) or str(UUID(stable_id)) != stable_id:
            raise rejected()
        roles = access.get(NS + "roles")
        if (
            access.get(NS + "products") != [config.product]
            or not isinstance(roles, list)
            or len(roles) != 1
            or roles[0] not in PRODUCT_ROLES[config.product]
        ):
            raise IdentityError("application_access_required", "Onesa product access required", 403)
        email, name = profile["email"], profile.get("name") or profile["email"]
        if (
            not isinstance(email, str)
            or not re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,189}", email)
            or len(email) > 254
            or not isinstance(name, str)
            or not 0 < len(name.strip()) <= 200
        ):
            raise rejected()
        factor = access.get(NS + "mfa_time", 0)
        authenticated = profile.get("auth_time", 0)
        now = int(time.time())
        if any(type(t) is not int or not 0 <= t <= now for t in [factor, authenticated]):
            raise rejected()
        # Existing APIs require MFA evidence at the administrator boundary.
        if roles[0] == "admin" and (not factor or now - factor > 300):
            raise IdentityError(
                "identity_mfa_required", "Complete MFA for administrator access", 403
            )
        return Identity(
            config.issuer,
            stable_id,
            email.casefold(),
            name.strip(),
            min(access["exp"], profile["exp"]),
            factor or authenticated,
            ("mfa",) if factor else (),
            (),
            (config.product,),
            {config.product: roles[0]},
            (),
        )
    except IdentityError:
        raise
    except (ValueError, KeyError, TypeError, jwt.PyJWTError, AttributeError, RecursionError):
        raise rejected() from None


def exchange(
    config: IdentityConfig, code: str, verifier: str, nonce: str, client: httpx.Client | None = None
) -> str:
    if not code or len(code) > 4096 or not re.fullmatch(r"[A-Za-z0-9_-]{40,128}", verifier):
        raise rejected()
    data = _request(
        config,
        "oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": config.redirect_uri,
        },
        client=client,
    )
    if str(data.get("token_type", "")).lower() != "bearer":
        raise rejected()
    packed = json.dumps(
        {k: data.get(k) for k in ("access_token", "id_token")}, separators=(",", ":")
    )
    # Preserve the existing encrypted-token column bound; fail before writing any session.
    if len(packed.encode()) > 5900:
        raise rejected()
    validate(config, packed, nonce)
    return packed
