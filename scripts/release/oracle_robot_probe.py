"""Commissioned Robot hosted observations, never authorization or provider deployment.

Only exact canonical origins and the separately hash-confirmed existing operator
credential are accepted. Staging runs the reviewed full simulator acceptance and
browser suite. Production inspection remains read-only apart from its own login
and logout; it never resets, approves, stops or replays an operational command.
"""

import http.cookiejar
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_robot as r

LEGACY_CHECKS = (
    "backend_readiness",
    "frontend_availability",
    "authentication",
    "authorization",
    "simulation",
    "safety_denial",
    "cancellation",
    "connection_loss",
    "stale_command",
    "replay_rejection",
    "audit_history",
    "storage_isolation",
    "edge_telemetry",
    "administrator_operation_permission",
)

# Run only the reviewed suite already present in the pinned qualification image.
# Scoped credentials travel over Docker stdin, never argv or container metadata.
LEGACY_PROGRAM = """
import json, os, re, sys
sys.path.insert(0, '/opt/onesa-ai-robot/scripts/release')
from smoke import run
def require(value):
    if not value:
        raise ValueError('LEGACY_IMAGE_INPUT_REFUSED')
raw = sys.stdin.buffer.read(16385)
require(len(raw) <= 16384)
request = json.loads(raw)
require(set(request) in ({'origin', 'environment'}, {'origin', 'environment', 'identity'}))
require(re.fullmatch(r'https://onesa-ai-robot-git-staging-[a-z0-9-]+\\.vercel\\.app', request['origin']))
values = request['environment']
require(set(values) == {
    'ROBOT_TEST_OPERATOR', 'ROBOT_TEST_PASSWORD', 'ROBOT_TEST_SCOPE',
    'ROBOT_PUBLIC_ORIGIN', 'ROBOT_STORAGE_MODE', 'ROBOT_EDGE_DEVICE_ID',
    'ROBOT_EDGE_TOKEN', 'VERCEL_AUTOMATION_BYPASS_SECRET'
})
require(all(isinstance(v, str) for v in values.values()))
require(values['ROBOT_TEST_SCOPE'] == 'approved-synthetic-staging')
require(values['ROBOT_PUBLIC_ORIGIN'] == request['origin'])
require(values['ROBOT_STORAGE_MODE'] == 'turso')
require(values['ROBOT_EDGE_DEVICE_ID'].startswith('synthetic-'))
os.environ.update(values)
identity_login = None
if 'identity' in request:
    from oracle_robot_identity import acquire, attach
    require(request['identity']['origin'] == request['origin'])
    require(request['identity']['environment'] == 'staging')
    def identity_login(api):
        selected = {'identity': request['identity'], 'provider': {'bypass': values['VERCEL_AUTOMATION_BYPASS_SECRET']}}
        return attach(api, acquire(selected, inside_image=True))
print(json.dumps(run(request['origin'], 'Staging', oracle_staging_api='https://robot-api-staging.onesa.eu', identity_login=identity_login), allow_nan=False))
"""


class API:
    def __init__(
        self, origin, public_origin, credentials, bypass=None, identity_request=None
    ):
        self.origin = origin
        self.public = public_origin
        self.credentials = credentials
        self.bypass = bypass
        self.identity_request = identity_request
        self.csrf = ""
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            r.NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar)
        )

    def call(self, path, body=None, *, expected=200, csrf=True):
        r.require(
            path.startswith("/api/v1/") and "?" not in path and ".." not in path,
            "PROBE_API_PATH",
        )
        headers = {
            "Origin": self.public,
            "Content-Type": "application/json",
            "User-Agent": "OnesaOracleRobotOwner/1",
        }
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        if self.bypass:
            headers["x-vercel-protection-bypass"] = self.bypass
        request = urllib.request.Request(
            self.origin + path,
            data=None if body is None else r.canonical(body),
            headers=headers,
        )
        try:
            response = self.opener.open(request, timeout=30)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            r.require(
                response.status == expected,
                "HOSTED_HTTP_STATUS_" + str(response.status),
            )
            raw = response.read(1024 * 1024 + 1)
            r.require(len(raw) <= 1024 * 1024, "PROBE_API_RESPONSE_BOUND")
            return json.loads(raw) if raw else None

    def login(self):
        if self.identity_request is not None:
            import oracle_robot_identity as identity

            r.require(
                self.origin
                == self.public
                == self.identity_request["identity"]["origin"],
                "IDENTITY_COOKIE_ORIGIN_MUST_NOT_BE_REWRITTEN",
            )
            return identity.attach(self, identity.acquire(self.identity_request))
        value = self.call(
            "/api/v1/auth/login",
            {
                "name": self.credentials["operator"],
                "password": self.credentials["password"],
            },
        )
        self.csrf = value["csrf"]
        r.require(
            any(
                c.name == "robot_session"
                and c.secure
                and c.has_nonstandard_attr("HttpOnly")
                for c in self.jar
            ),
            "SECURE_HTTPONLY_SESSION_REQUIRED",
        )

    def logout(self):
        if self.csrf:
            value = self.call("/api/v1/auth/logout", {})
            if self.identity_request is not None:
                r.require(
                    value.get("identity_signed_out") is True,
                    "IDENTITY_SIGNOUT_MUST_BE_CONFIRMED",
                )
            self.csrf = ""
            self.call("/api/v1/auth/me", expected=401)


def validate_request(request):
    b = request["binding"]
    env = request["environment"]
    r.require(
        b["repository"] == "DruHustle/onesa-robot"
        and b["component"] in r.COMPONENTS
        and env in ["staging", "production"],
        "HOSTED_PROBE_COMPONENT_SCOPE",
    )
    r.require(
        request["physical_hardware_authorized"] is False
        and type(request["read_only"]) is bool
        and (env == "staging" or request["read_only"] is True),
        "HOSTED_PROBE_PROCESSING_SCOPE",
    )
    expected = (
        "https://robot-api" + ("-staging" if env == "staging" else "") + ".onesa.eu"
    )
    r.require(request["api_origin"] == expected, "HOSTED_API_TARGET_SCOPE")
    host = urllib.parse.urlsplit(request["origin"])
    r.require(
        host.scheme == "https"
        and host.path == ""
        and host.query == ""
        and host.fragment == ""
        and host.username is None
        and host.password is None
        and host.port is None,
        "CANONICAL_PROBE_ORIGIN",
    )
    r.require(
        request["origin"] == "https://robot.onesa.eu"
        if env == "production"
        else bool(
            re.fullmatch(
                r"onesa-ai-robot-git-staging-[a-z0-9-]+\.vercel\.app",
                host.hostname or "",
            )
        ),
        "HOSTED_FRONTEND_TARGET_SCOPE",
    )
    provider = request["provider"]
    r.require(
        provider["environment"] == env
        and provider["origin"] == request["origin"]
        and provider["provider_token_included"] is False
        and provider["binding"] == b
        and provider["provider"]["source"] == request["expected_frontend_source"],
        "ACTUAL_HOSTED_PROVIDER_REQUIRED",
    )
    r.require(
        re.fullmatch("sha256:[a-f0-9]{64}", request["probe_image"])
        and request["cpu_limit"] in ["1", "2"],
        "PINNED_BOUNDED_PROBE_IMAGE",
    )
    r.require(
        ("identity" in request) == (b["component"] in r.IDENTITY_COMPONENTS),
        "IDENTITY_PROBE_REQUIRES_DISTINCT_COMPONENT_SCOPE",
    )
    if "identity" in request:
        value = request["identity"]
        r.require(
            value["profile"] == "shared-identity-v1"
            and value["environment"] == env
            and value["origin"] == request["origin"]
            and request.get("identity_api_only", False)
            is (env == "production" and r.base_component(b) == "api"),
            "SOURCE_BOUND_IDENTITY_PROBE_SCOPE",
        )
    return env


def readonly_api(request):
    identity = request.get("identity")
    api = API(
        request["origin"] if identity else request["api_origin"],
        request["origin"],
        request["credentials"],
        bypass=request["provider"].get("bypass") if identity else None,
        identity_request=request if identity else None,
    )
    ready = api.call("/api/v1/ready")
    r.require(
        ready["status"] == "ready"
        and ready["mode"] == "simulation"
        and ready["physical_actuation"] is False
        and ready["storage_mode"] == "turso"
        and ready["deployment_environment"] == request["environment"],
        "ACTUAL_HOSTED_READINESS",
    )
    for path in [
        "/api/v1/status",
        "/api/v1/tasks",
        "/api/v1/missions",
        "/api/v1/missions/catalog",
        "/api/v1/admin/physical-operation",
    ]:
        api.call(path, expected=401)
    api.login()
    try:
        state = api.call("/api/v1/status")
        r.require(
            state["mode"] == "simulation"
            and state["physical_actuation"] is False
            and state["stopped"] is True
            and state["active_task"] is None,
            "PROBE_REQUIRES_EXISTING_STOPPED_IDLE_RUNTIME",
        )
        catalog = api.call("/api/v1/missions/catalog")
        r.require(
            catalog["physical_actuation"] is False
            and catalog["mode"] == "simulation"
            and len(catalog["skills"]) == 5
            and len(catalog["scenarios"]) == 7,
            "NEW_INSPECTION_CATALOG_REQUIRED",
        )
        missions = api.call("/api/v1/missions")
        tasks = api.call("/api/v1/tasks")
        permission = api.call("/api/v1/admin/physical-operation")
        r.require(
            permission["permission"] == "blocked"
            and permission["physical_actuation"] is False,
            "PHYSICAL_PERMISSION_MUST_REMAIN_BLOCKED",
        )
        identity_roles = None
        if identity:
            ordinary = API(
                request["origin"],
                request["origin"],
                {},
                bypass=request["provider"].get("bypass"),
                identity_request={**request, "identity_actor": "operator"},
            )
            ordinary.login()
            try:
                normal = ordinary.call("/api/v1/auth/me")
                r.require(
                    normal["role"] == "operator"
                    and normal["authentication_method"] == "onesa"
                    and normal["global_administrator"] is False,
                    "NORMAL_IDENTITY_ROLE_REQUIRED",
                )
                ordinary.call("/api/v1/admin/physical-operation", expected=403)
                for path in [
                    "/api/v1/status",
                    "/api/v1/missions/catalog",
                    "/api/v1/missions",
                    "/api/v1/tasks",
                ]:
                    ordinary.call(path)
                identity_roles = {
                    "administrator": "PASS",
                    "operator": "PASS",
                    "operator_admin_denial": 403,
                }
            finally:
                ordinary.logout()
        return {
            "readiness": "PASS",
            "authentication": "PASS",
            "anonymous_denials": 5,
            "catalog": "PASS",
            "mission_history_read": "PASS",
            "task_history_read": "PASS",
            "physical_permission": "BLOCKED",
            "production_operational_mutations": 0,
            "mission_count": len(missions.get("missions", [])),
            "task_count": len(tasks["tasks"]),
            **({"shared_identity_roles": identity_roles} if identity else {}),
        }
    finally:
        api.logout()


def verify_browser_authorization(value, request):
    expected = {
        (engine, phase)
        for engine in ["chromium", "firefox", "webkit"]
        for phase in ["before_sign_in", "after_logout"]
    }
    path = (
        "/api/v1/tasks"
        if request["environment"] == "production"
        and r.base_component(request["binding"]) == "api"
        else "/api/v1/missions"
    )
    rows = value.get("authorization_checks", [])
    r.require(
        isinstance(rows, list)
        and len(rows) == len(expected)
        and all(isinstance(row, dict) for row in rows)
        and {(row.get("engine"), row.get("phase")) for row in rows} == expected
        and all(
            row.get("path") == path and row.get("http_status") == 401 for row in rows
        ),
        "COMPONENT_BROWSER_AUTHORIZATION_BOUNDARY_REQUIRED",
    )


def browser(request):
    """The immutable probe image contains commissioned tooling, not candidate code."""
    output = Path(request["output"])
    private = output / "browser-request.json"
    r.write(
        private,
        {
            k: request[k]
            for k in [
                "binding",
                "environment",
                "origin",
                "credentials",
                "provider",
                "read_only",
            ]
        }
        | ({"identity": request["identity"]} if "identity" in request else {}),
    )
    command = [
        "/usr/bin/docker",
        "run",
        "--rm",
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
        "-v",
        str(private) + ":/private/request.json:ro",
        "-v",
        str(output) + ":/private/output:rw",
        "--entrypoint",
        "node",
        request["probe_image"],
        "/opt/onesa-ai-robot/scripts/release/oracle_robot_browser.mjs",
        "/private/request.json",
    ]
    try:
        with (
            (output / "browser.stdout").open("xb") as stdout,
            (output / "browser.stderr").open("xb") as stderr,
        ):
            os.chmod(stdout.name, 0o600)
            os.chmod(stderr.name, 0o600)
            result = subprocess.run(
                command,
                stdout=stdout,
                stderr=stderr,
                timeout=1500,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
    finally:
        # Also remove scoped credentials if the child fails or exceeds its bound.
        private.unlink(missing_ok=True)
    r.require(result.returncode == 0, "HOSTED_BROWSER_FAILED_INSPECT_PRIVATE_EVIDENCE")
    value = r.read(output / "browser-receipt.json")
    verify_browser_authorization(value, request)
    r.require(
        value["status"] == "PASS"
        and value["engines"] == ["chromium", "firefox", "webkit"]
        and value["binding"] == request["binding"]
        and value["environment"] == request["environment"]
        and value["replay_operational_requests"] == 0
        and value["javascript_errors"] == 0
        and value["accessibility_violations"] == 0,
        "GENUINE_HOSTED_BROWSER_RECEIPT",
    )
    if "identity" in request:
        r.require(
            value.get("authentication_method") == "onesa"
            and value.get("identity_entry")
            == (
                "api-pkce-retained-frontend"
                if request["environment"] == "production"
                and r.base_component(request["binding"]) == "api"
                else "product-ui-pkce"
            ),
            "GENUINE_IDENTITY_BROWSER_REQUIRED",
        )
    return value


def staging_legacy(request):
    # Keep the complete legacy suite in its commissioned image, not the host's
    # deliberately minimal installed Python module set.
    env = validate_request(request)
    r.require(env == "staging", "NO_LEGACY_MUTATIONS_IN_PRODUCTION")
    r.require(request["read_only"] is False, "NO_LEGACY_MUTATIONS_IN_READ_ONLY_PROBE")
    credential = request["credentials"]
    values = r.runtime_values(r.ENV["staging"])
    r.require(
        credential.get("edge_token")
        and values.get("ROBOT_EDGE_DEVICE_ID", "").startswith("synthetic-"),
        "EXISTING_STAGING_EDGE_CREDENTIAL_REQUIRED",
    )
    chosen = {
        "ROBOT_TEST_OPERATOR": credential["operator"],
        "ROBOT_TEST_PASSWORD": credential["password"],
        "ROBOT_TEST_SCOPE": "approved-synthetic-staging",
        "ROBOT_PUBLIC_ORIGIN": request["origin"],
        "ROBOT_STORAGE_MODE": "turso",
        "ROBOT_EDGE_DEVICE_ID": values["ROBOT_EDGE_DEVICE_ID"],
        "ROBOT_EDGE_TOKEN": credential["edge_token"],
        "VERCEL_AUTOMATION_BYPASS_SECRET": request["provider"]["bypass"],
    }
    payload = {"origin": request["origin"], "environment": chosen}
    if "identity" in request:
        payload["identity"] = request["identity"]
    raw = r.canonical(payload)
    r.require(len(raw) <= 16384, "LEGACY_PRIVATE_INPUT_BOUND")
    result = subprocess.run(
        [
            "/usr/bin/docker",
            "run",
            "--rm",
            "-i",
            "--pull",
            "never",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,mode=700,size=512m"
            if "identity" in request
            else "/tmp:rw,mode=700,size=64m",
            "--memory",
            "1400m" if "identity" in request else "256m",
            "--cpus",
            request["cpu_limit"],
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--entrypoint",
            "python3",
            request["probe_image"],
            "-c",
            LEGACY_PROGRAM,
        ],
        input=raw,
        capture_output=True,
        timeout=1200,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    r.require(result.returncode == 0, "LEGACY_IMAGE_EXECUTION_FAILED")
    r.require(len(result.stdout) <= 65536, "LEGACY_RESULT_BOUND")
    value = json.loads(result.stdout)
    r.write(Path(request["output"]) / "legacy-smoke.json", value)
    r.require(
        value["status"] == "PASS"
        and value["scope"] == "software-simulation-only"
        and value["physical_hardware_approved"] is False
        and set(value["checks"]) == set(LEGACY_CHECKS)
        and all(value["checks"][k] == "PASS" for k in LEGACY_CHECKS),
        "EXISTING_SIMULATOR_ACCEPTANCE_FAILED",
    )
    return value


def execute(request):
    env = validate_request(request)
    results = {"read_only_api": readonly_api(request)}
    if env == "staging" and not request["read_only"]:
        results["existing_simulator_and_edge"] = staging_legacy(request)
    results["browser"] = browser(request)
    return {
        "status": "PASS",
        "read_only": request["read_only"],
        "binding": request["binding"],
        "environment": env,
        "operation_id": request["operation_id"],
        "observed_at": r.now().isoformat(),
        "results": results,
        "physical_hardware_authorized": False,
        "production_operational_mutations": 0,
        "source_build_image_identity_observed": True,
    }
