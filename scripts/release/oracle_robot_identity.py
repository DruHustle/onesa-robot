"""Explicit source-bound identity release profile and private session transport.

Existing owner approval, provider custody and simulation gates remain in the Robot
runner. This module cannot provision users, grant access or authorize a release.
"""

import argparse
import datetime as dt
import getpass
import hashlib
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_robot as r

PROFILE = "shared-identity-v1"
KEYS = {
    "ONESA_IDENTITY_ENABLED",
    "ONESA_IDENTITY_ISSUER",
    "ONESA_IDENTITY_CLIENT_ID",
    "ONESA_IDENTITY_CLIENT_SECRET",
    "ONESA_IDENTITY_CALLBACK_URL",
}
CUSTODY = Path("/run/onesa-release/robot-identity.json")
BROWSER = "/opt/onesa-ai-robot/scripts/release/oracle_robot_identity_browser.mjs"


def profile(plan, binding, tool):
    value = plan.get("identity")
    if value is None:
        r.require(
            binding["component"] not in r.IDENTITY_COMPONENTS,
            "EXPLICIT_IDENTITY_PROFILE_REQUIRED",
        )
        return None
    r.require(
        binding["component"] in r.IDENTITY_COMPONENTS
        and set(plan["bindings"]) == r.IDENTITY_COMPONENTS
        and "independent_api" not in plan,
        "IDENTITY_REQUIRES_DISTINCT_COMPONENT_SCOPES",
    )
    for component in r.IDENTITY_COMPONENTS:
        selected = plan["bindings"][component]
        r.require(
            selected["component"] == component
            and (
                not r.is_canonical(binding)
                or r.source_identity(selected) == r.source_identity(binding)
            )
            and all(
                selected[key] == binding[key]
                for key in [
                    "repository",
                    "candidate_commit",
                    "source_tree",
                    "source_sha256",
                    "target_environment",
                ]
            ),
            "IDENTITY_COMPONENT_PAIR_EXACT_SOURCE_REQUIRED",
        )
    r.require(
        set(value)
        == {
            "profile",
            "source_commit",
            "source_tree",
            "source_sha256",
            "environments",
            "native_bridge",
        }
        and value["profile"] == PROFILE
        and value["source_commit"]
        == binding["candidate_commit"]
        == tool["source_commit"]
        and value["source_tree"] == binding["source_tree"] == tool["source_tree"]
        and value["source_sha256"] == binding["source_sha256"] == tool["source_sha256"]
        and set(value["environments"]) == set(r.ENV),
        "IDENTITY_PROFILE_EXACT_INSTALLED_SOURCE_REQUIRED",
    )
    for environment, entry in value["environments"].items():
        r.require(
            set(entry)
            == {
                "issuer",
                "client_id",
                "client_secret_sha256",
                "callback_url",
                "portal_bypass_sha256",
                "accounts",
                "baseline_frontend",
            },
            "IDENTITY_ENVIRONMENT_PROFILE_SHAPE",
        )
        issuer = urllib.parse.urlsplit(entry["issuer"])
        r.require(
            issuer.scheme == "https"
            and issuer.path == "/api/v1/identity"
            and not issuer.query
            and not issuer.fragment
            and not issuer.username
            and not issuer.password
            and not issuer.port
            and (
                issuer.hostname == "portal.onesa.eu"
                if environment == "production"
                else re.fullmatch(
                    r"onesa-company-portal-[a-z0-9-]+\.vercel\.app",
                    issuer.hostname or "",
                )
            )
            and re.fullmatch(r"[A-Za-z0-9_-]{16,128}", entry["client_id"])
            and re.fullmatch(r"[a-f0-9]{64}", entry["client_secret_sha256"])
            and entry["callback_url"]
            == plan["origins"][environment] + "/api/v1/auth/identity/callback"
            and (
                entry["portal_bypass_sha256"] is None
                or environment == "staging"
                and re.fullmatch(r"[a-f0-9]{64}", entry["portal_bypass_sha256"])
            )
            and set(entry["accounts"]) == {"administrator", "operator"},
            "EXACT_CONFIDENTIAL_IDENTITY_ENVIRONMENT_REQUIRED",
        )
        baseline = entry["baseline_frontend"]
        r.require(
            set(baseline) == {"deployment_id", "source"}
            and re.fullmatch(r"dpl_[A-Za-z0-9]{8,128}", baseline["deployment_id"])
            and baseline["source"] == binding["candidate_commit"],
            "EXACT_IDENTITY_BASELINE_FRONTEND_REQUIRED",
        )
        for account in entry["accounts"].values():
            r.require(
                set(account) == {"subject", "email"}
                and str(UUID(account["subject"])) == account["subject"]
                and re.fullmatch(r"[^\s@]{1,64}@[^\s@]{1,189}", account["email"])
                and account["email"] == account["email"].casefold(),
                "EXACT_EXISTING_IDENTITY_SUBJECT_REQUIRED",
            )
    stage, production = (
        value["environments"][key] for key in ["staging", "production"]
    )
    r.require(
        stage["issuer"] != production["issuer"]
        and stage["client_id"] != production["client_id"]
        and stage["client_secret_sha256"] != production["client_secret_sha256"]
        and len(
            {
                account["subject"]
                for entry in [stage, production]
                for account in entry["accounts"].values()
            }
        )
        == 4,
        "IDENTITY_ENVIRONMENTS_AND_TEST_PRINCIPALS_MUST_BE_SEPARATE",
    )
    return value


def native_bridge(plan, binding):
    """Read the accepted identity-disabled bridge; do not transfer its authority."""
    bridge = plan["identity"]["native_bridge"]
    r.require(set(bridge) == {"api", "frontend"}, "EXACT_NATIVE_BRIDGE_PAIR_REQUIRED")
    records = {}
    for component, reference in bridge.items():
        r.require(
            set(reference) == {"binding", "operation_id"}
            and str(UUID(reference["operation_id"])) == reference["operation_id"],
            "EXACT_NATIVE_BRIDGE_REFERENCE_REQUIRED",
        )
        native = reference["binding"]
        r.require(
            native["component"] == component
            and (
                not r.is_canonical(binding)
                or r.source_identity(native) == r.source_identity(binding)
            )
            and all(
                native[key] == binding[key]
                for key in [
                    "repository",
                    "candidate_commit",
                    "source_tree",
                    "source_sha256",
                    "target_environment",
                ]
            ),
            "NATIVE_BRIDGE_MUST_BE_SAME_SOURCE_DISTINCT_SCOPE",
        )
        chain = r.verify_invocation(
            native,
            {"operation_id": reference["operation_id"], "read_only": True},
            "production",
            plan,
            dependency=True,
        )
        accepted = [
            row
            for row in chain
            if row["kind"] == "production-verification"
            and row["parent"] == reference["operation_id"]
            and row["binding"] == native
        ]
        r.require(len(accepted) == 1, "ACCEPTED_NATIVE_BRIDGE_REQUIRED")
        records[component] = accepted[0]["details"]["observations"]
    native_api = bridge["api"]["binding"]
    identity_api = r.plan_binding(plan, "api")
    r.require(
        native_api["artifact_digest"] == identity_api["artifact_digest"],
        "IDENTITY_API_MUST_USE_QUALIFIED_BRIDGE_IMAGE",
    )
    for environment in r.ENV:
        path = r.candidate_environment(native_api, environment)
        r.require(
            r.sha(r.private(path)) == native_api["configuration_sha256"][environment]
            and r.runtime_values(path).get("ONESA_IDENTITY_ENABLED", "false") == "false"
            and native_api["configuration_sha256"][environment]
            != identity_api["configuration_sha256"][environment],
            "NATIVE_BRIDGE_IDENTITY_DISABLED_CONFIGURATION_REQUIRED",
        )
    baseline = plan["identity"]["environments"]["production"]["baseline_frontend"]
    frontend = records["frontend"]
    r.require(
        frontend["deployment_id"] == baseline["deployment_id"]
        and frontend["frontend"]["source_commit"] == baseline["source"]
        and frontend["backend_runtime"] == records["api"]["backend_runtime"],
        "ACCEPTED_NATIVE_BRIDGE_FRONTEND_AND_API_REQUIRED",
    )
    return records


def configuration(value, environment, prepared):
    if value is None:
        r.require(
            prepared.get("ONESA_IDENTITY_ENABLED", "false") == "false",
            "EXPLICIT_IDENTITY_PROFILE_REQUIRED",
        )
        return set()
    entry = value["environments"][environment]
    secret = prepared.get("ONESA_IDENTITY_CLIENT_SECRET", "")
    r.require(
        prepared.get("ONESA_IDENTITY_ENABLED") == "true"
        and prepared.get("ONESA_IDENTITY_ISSUER") == entry["issuer"]
        and prepared.get("ONESA_IDENTITY_CLIENT_ID") == entry["client_id"]
        and prepared.get("ONESA_IDENTITY_CALLBACK_URL") == entry["callback_url"]
        and 48 <= len(secret) <= 256
        and all(33 <= ord(char) <= 126 for char in secret)
        and hashlib.sha256(secret.encode()).hexdigest()
        == entry["client_secret_sha256"],
        "IDENTITY_CONFIGURATION_DIFFERS_FROM_BOUND_PROFILE",
    )
    return KEYS


def credentials(plan, binding):
    value = r.read(CUSTODY)
    return validate_credentials(value, plan, binding)


def validate_credentials(value, plan, binding):
    r.require(
        set(value)
        == {
            "schema_version",
            "candidate_commit",
            "created_at",
            "expires_at",
            "mechanism",
            "release_authority",
            "environments",
        }
        and value["schema_version"] == 1
        and value["candidate_commit"] == binding["candidate_commit"]
        and value["mechanism"] == "owner-tty-existing-identity-credentials"
        and value["release_authority"] is False
        and set(value["environments"]) == set(r.ENV),
        "IDENTITY_CREDENTIAL_CUSTODY_SOURCE_SCOPE",
    )
    created, expires = (
        dt.datetime.fromisoformat(value[key]) for key in ["created_at", "expires_at"]
    )
    r.require(
        dt.timedelta(0) < expires - created <= dt.timedelta(hours=1)
        and created <= r.now() < expires,
        "IDENTITY_CREDENTIAL_CUSTODY_EXPIRED",
    )
    for environment, entry in value["environments"].items():
        planned = plan["identity"]["environments"][environment]
        r.require(
            set(entry) == {"accounts", "portal_bypass"}
            and set(entry["accounts"]) == {"administrator", "operator"},
            "IDENTITY_CREDENTIAL_SHAPE",
        )
        for role, account in entry["accounts"].items():
            r.require(
                set(account) == {"subject", "email", "password", "totp_secret"}
                and all(
                    account[key] == planned["accounts"][role][key]
                    for key in ["subject", "email"]
                )
                and isinstance(account["password"], str)
                and 1 <= len(account["password"]) <= 256
                and re.fullmatch(r"[A-Z2-7]{16,128}", account["totp_secret"]),
                "EXISTING_IDENTITY_CREDENTIAL_BINDING_REQUIRED",
            )
        bypass = entry["portal_bypass"]
        r.require(
            (bypass is None and planned["portal_bypass_sha256"] is None)
            or isinstance(bypass, str)
            and 1 <= len(bypass) <= 8192
            and all(33 <= ord(char) <= 126 for char in bypass)
            and hashlib.sha256(bypass.encode()).hexdigest()
            == planned["portal_bypass_sha256"],
            "EXACT_PORTAL_PREVIEW_PROTECTION_CUSTODY_REQUIRED",
        )
    return value["environments"]


def request(plan, binding, environment, *, origin=None):
    entry = plan["identity"]["environments"][environment]
    held = credentials(plan, binding)[environment]
    return {
        "profile": PROFILE,
        "environment": environment,
        "origin": origin or plan["origins"][environment],
        "issuer": entry["issuer"],
        "client_id": entry["client_id"],
        "accounts": held["accounts"],
        "portal_bypass": held["portal_bypass"],
    }


def acquire(request, *, inside_image=False):
    """Return an actual private browser session. No supplied acceptance is consumed."""
    payload = {
        "identity": request["identity"],
        "actor": request.get("identity_actor", "administrator"),
        "robot_bypass": request["provider"].get("bypass"),
        "api_only": request.get("identity_api_only", False),
    }
    r.require(type(payload["api_only"]) is bool, "EXPLICIT_IDENTITY_ENTRY_SCOPE")
    raw = r.canonical(payload)
    r.require(len(raw) <= 65536, "PRIVATE_IDENTITY_INPUT_BOUND")
    if inside_image:
        command = ["node", BROWSER]
    else:
        r.require(
            re.fullmatch(r"sha256:[a-f0-9]{64}", request["probe_image"])
            and request["cpu_limit"] in {"1", "2"},
            "PINNED_IDENTITY_PROBE_REQUIRED",
        )
        command = [
            "/usr/bin/docker",
            "run",
            "--rm",
            "-i",
            "--pull",
            "never",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,size=512m",
            "--tmpfs",
            "/root:rw,mode=700,size=64m",
            "--memory",
            "1400m",
            "--cpus",
            request["cpu_limit"],
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--entrypoint",
            "node",
            request["probe_image"],
            BROWSER,
        ]
    child = subprocess.run(
        command,
        input=raw,
        capture_output=True,
        timeout=180,
        check=False,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin" if inside_image else "/usr/bin:/bin",
            "LANG": "C.UTF-8",
        },
    )
    r.require(
        child.returncode == 0 and len(child.stdout) <= 65536,
        "IDENTITY_SESSION_ACQUISITION_FAILED",
    )
    result = json.loads(child.stdout)
    validate_session(result, payload)
    return result


def validate_session(value, payload):
    identity = payload["identity"]
    actor = payload["actor"]
    account = identity["accounts"][actor]
    cookie = value["cookie"]
    instant = r.now().timestamp()
    r.require(
        set(value)
        == {
            "schema_version",
            "origin",
            "issuer",
            "subject",
            "role",
            "csrf",
            "confirmation_expires_at",
            "cookie",
        }
        and value["schema_version"] == 1
        and value["origin"] == identity["origin"]
        and value["issuer"] == identity["issuer"]
        and value["subject"] == account["subject"]
        and value["role"] == actor
        and isinstance(value["csrf"], str)
        and re.fullmatch(r"[A-Za-z0-9_-]{20,128}", value["csrf"])
        and type(value["confirmation_expires_at"]) in {int, float}
        and instant < value["confirmation_expires_at"] <= instant + 330
        and set(cookie)
        == {
            "name",
            "value",
            "domain",
            "path",
            "expires",
            "httpOnly",
            "secure",
            "sameSite",
        }
        and cookie["name"] == "robot_session"
        and re.fullmatch(r"[A-Za-z0-9_-]{20,128}", cookie["value"])
        and cookie["domain"] == urllib.parse.urlsplit(identity["origin"]).hostname
        and cookie["path"] == "/api/v1"
        and cookie["secure"] is True
        and cookie["httpOnly"] is True
        and cookie["sameSite"] == "Strict"
        and type(cookie["expires"]) in {int, float}
        and instant < cookie["expires"] <= instant + 86400,
        "EXACT_FRESH_MFA_IDENTITY_SESSION_REQUIRED",
    )


def attach(api, value):
    """Keep the original host-only browser cookie; never rewrite its domain."""
    origin = getattr(api, "url", getattr(api, "origin", ""))
    r.require(origin == value["origin"], "IDENTITY_COOKIE_ORIGIN_MUST_NOT_BE_REWRITTEN")
    cookie = value["cookie"]
    api.jar.clear()
    api.jar.set_cookie(
        http.cookiejar.Cookie(
            version=0,
            name=cookie["name"],
            value=cookie["value"],
            port=None,
            port_specified=False,
            domain=cookie["domain"],
            domain_specified=False,
            domain_initial_dot=False,
            path=cookie["path"],
            path_specified=True,
            secure=True,
            expires=int(cookie["expires"]),
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None, "SameSite": "Strict"},
            rfc2109=False,
        )
    )
    api.csrf = value["csrf"]
    observed = api.call("/api/v1/auth/me")
    r.require(
        observed["authentication_method"] == "onesa"
        and observed["role"] == value["role"]
        and observed["csrf"] == value["csrf"]
        and observed["confirmation_expires_at"] == value["confirmation_expires_at"],
        "LIVE_IDENTITY_SESSION_REVALIDATION_REQUIRED",
    )
    return observed


def authenticated_state(plan, binding, environment):
    """Preserve preflight's actual stopped-idle proof under shared sign-in."""
    import oracle_robot_frontend as frontend
    import oracle_robot_probe as probe

    values = r.runtime_values(r.ENV[environment])
    configuration(plan["identity"], environment, values)
    origin = values["ROBOT_PUBLIC_ORIGIN"]
    r.require(
        origin == plan["origins"][environment], "EXACT_IDENTITY_RUNTIME_ORIGIN_REQUIRED"
    )
    if environment == "staging" and plan.get("preview_receipt_path"):
        provider = frontend.observe_provider(
            binding, environment, plan, "identity-state"
        )
    else:
        project = frontend.protection(plan)
        baseline = plan["identity"]["environments"][environment]["baseline_frontend"]
        alias = frontend.provider("/v4/aliases/" + origin.removeprefix("https://"))
        deployment = frontend.provider("/v13/deployments/" + baseline["deployment_id"])
        source = deployment.get("meta", {}).get("githubCommitSha") or deployment.get(
            "gitSource", {}
        ).get("sha")
        r.require(
            alias["deployment"]["id"] == deployment["id"] == baseline["deployment_id"]
            and alias["projectId"] == deployment["projectId"] == plan["project_id"]
            and source == baseline["source"]
            and deployment["readyState"] == "READY"
            and deployment.get("target")
            == ("production" if environment == "production" else None),
            "IDENTITY_BASELINE_FRONTEND_CHANGED",
        )
        bypass = None
        if environment == "staging":
            keys = [
                key
                for key, value in project.get("protectionBypass", {}).items()
                if value.get("scope") == "automation-bypass"
            ]
            r.require(len(keys) == 1, "EXISTING_PREVIEW_PROTECTION_CUSTODY")
            bypass = keys[0]
        provider = {"bypass": bypass}
    selected = {
        "identity": request(plan, binding, environment),
        "provider": provider,
        "probe_image": plan["probe_image"],
        "cpu_limit": r.probe_cpu_limit(),
        "identity_api_only": True,
    }
    api = probe.API(
        origin, origin, {}, bypass=provider.get("bypass"), identity_request=selected
    )
    ready = api.call("/api/v1/ready")
    r.require(
        ready["mode"] == "simulation"
        and ready["physical_actuation"] is False
        and ready["deployment_environment"] == environment
        and ready["storage_mode"] == "turso",
        "SIMULATION_READINESS_REQUIRED",
    )
    api.login()
    try:
        value = api.call("/api/v1/status")
        r.require(
            value["stopped"] is True
            and value["active_task"] is None
            and value["physical_actuation"] is False
            and value["mode"] == "simulation",
            "EXISTING_RUNTIME_NOT_STOPPED_IDLE_PRESERVE_WORK",
        )
        return value
    finally:
        api.logout()


def collect(plan, binding, prompt=getpass.getpass):
    r.require(
        not CUSTODY.exists() and not CUSTODY.is_symlink(),
        "EXISTING_IDENTITY_CUSTODY_PRESERVED",
    )
    values = {}
    for environment, entry in plan["identity"]["environments"].items():
        accounts = {}
        for role, account in entry["accounts"].items():
            accounts[role] = {
                **account,
                "password": prompt(
                    f"Existing {environment} {role} Onesa password (hidden): "
                ),
                "totp_secret": prompt(
                    f"Existing {environment} {role} authorized test authenticator seed (hidden): "
                ),
            }
        values[environment] = {
            "accounts": accounts,
            "portal_bypass": prompt("Existing Portal staging Preview bypass (hidden): ")
            if entry["portal_bypass_sha256"]
            else None,
        }
    CUSTODY.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    r.private(CUSTODY.parent, directory=True)
    created = r.now()
    value = {
        "schema_version": 1,
        "candidate_commit": binding["candidate_commit"],
        "created_at": created.isoformat(),
        "expires_at": (created + dt.timedelta(hours=1)).isoformat(),
        "mechanism": "owner-tty-existing-identity-credentials",
        "release_authority": False,
        "environments": values,
    }
    validate_credentials(value, plan, binding)
    r.write(CUSTODY, value)
    return {
        "state": "IDENTITY_CREDENTIAL_CUSTODY_NOT_ACCEPTANCE",
        "release_authority": False,
        "candidate_commit": binding["candidate_commit"],
        "expires_at": (created + dt.timedelta(hours=1)).isoformat(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--canonical-source",
        action="store_true",
        help="Use the fixed separately reviewed canonical Robot plan",
    )
    parser.add_argument("--clear-expired", action="store_true")
    args = parser.parse_args()
    r.require(
        os.geteuid() == 0
        and os.getenv("SUDO_USER") == "ubuntu"
        and not os.getenv("GITHUB_ACTIONS"),
        "OWNER_HIDDEN_TTY_REQUIRED",
    )
    r.require(
        re.fullmatch(r"[a-f0-9]{40}", args.candidate),
        "EXACT_IDENTITY_CANDIDATE_REQUIRED",
    )
    if args.clear_expired:
        value = r.read(CUSTODY)
        r.require(
            value["candidate_commit"] == args.candidate
            and value["mechanism"] == "owner-tty-existing-identity-credentials"
            and value["release_authority"] is False
            and dt.datetime.fromisoformat(value["expires_at"]) <= r.now(),
            "ONLY_THIS_EXPIRED_IDENTITY_CUSTODY_CAN_BE_CLEARED",
        )
        CUSTODY.unlink()
        r.sync_directory(CUSTODY.parent)
        print(
            json.dumps(
                {
                    "state": "EXPIRED_IDENTITY_CUSTODY_CLEARED",
                    "release_authority": False,
                }
            )
        )
        return
    r.require(sys.stdin.isatty(), "OWNER_HIDDEN_TTY_REQUIRED_NO_SECRET_STDIN")
    plan = r.read(
        r.PLAN.with_name("oracle-robot-canonical-release.json")
        if args.canonical_source
        else r.PLAN
    )
    binding = plan["bindings"][
        plan.get(
            "release_component",
            "identity-api" if plan.get("identity") is not None else "api",
        )
    ]
    r.require(
        binding["candidate_commit"] == args.candidate,
        "EXACT_IDENTITY_CANDIDATE_REQUIRED",
    )
    tool = r.verify_toolchain(plan, binding)
    r.require(
        profile(plan, binding, tool) is not None, "EXPLICIT_IDENTITY_PROFILE_REQUIRED"
    )
    print(json.dumps(collect(plan, binding)))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- private identity credentials and provider errors.
        raise SystemExit(
            "ROBOT_IDENTITY_CUSTODY_REFUSED_INSPECT_PRIVATE_STATE"
        ) from None
