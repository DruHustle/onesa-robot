"""Authenticated synthetic simulator acceptance. No hardware or real user data."""

import argparse
import http.cookiejar
import json
import os
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from uuid import uuid4

from cloud import NoRedirect, origin
from evidence import Rejected, require

CHECKS = [
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
]


class ReadinessUnavailable(Rejected):
    """Temporary rollout readiness; no authenticated operation has occurred."""


class SimulatorHTTPFailure(Rejected):
    def __init__(self, status):
        self.status = status if type(status) is int and 100 <= status <= 599 else 0
        super().__init__("Unexpected simulator HTTP status")


def failure_details(error):
    """Fixed categories only; never retain response text, URLs or credentials."""
    if isinstance(error, SimulatorHTTPFailure):
        return {"category": "http_status", "http_status": error.status}
    if isinstance(error, json.JSONDecodeError):
        return {
            "category": "response_json",
            "body_characters": min(len(error.doc), 1024 * 1024),
        }
    if isinstance(error, TimeoutError):
        return {"category": "transport_timeout"}
    if isinstance(error, urllib.error.URLError):
        reason = error.reason
        if isinstance(reason, TimeoutError):
            return {"category": "transport_timeout"}
        if isinstance(reason, ssl.SSLError):
            return {"category": "transport_tls"}
        if isinstance(reason, socket.gaierror):
            return {"category": "transport_dns"}
        if isinstance(reason, OSError):
            number = reason.errno
            return {
                "category": "transport_socket",
                "errno": number
                if type(number) is int and -1000 <= number <= 1000
                else None,
            }
        return {"category": "transport_unclassified"}
    return {"category": "acceptance"}


class Simulator:
    def __init__(self, url):
        self.url = origin(url)
        self.csrf = ""
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar)
        )

    def call(self, route, body=None, expected=200, csrf=True, timeout=45):
        headers = {
            "Origin": self.url,
            "Content-Type": "application/json",
            "User-Agent": "OnesaRobotQualification/1",
        }
        if self.csrf and csrf:
            headers["X-CSRF-Token"] = self.csrf
        bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
        if bypass and urllib.parse.urlsplit(self.url).hostname.endswith(".vercel.app"):
            headers["x-vercel-protection-bypass"] = bypass
        request = urllib.request.Request(
            self.url + route,
            headers=headers,
            data=json.dumps(body).encode() if body is not None else None,
        )
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            if (
                route == "/api/v1/ready"
                and body is None
                and response.code in {502, 503}
            ):
                raise ReadinessUnavailable("Simulator journal is not ready")
            if response.code != expected:
                raise SimulatorHTTPFailure(response.code)
            raw = response.read(1024 * 1024)
            if response.headers.get_content_type() == "application/json":
                return json.loads(raw)
            return raw


def wait_readiness(api):
    deadline = time.monotonic() + 90
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, "Simulator readiness deadline exceeded")
        try:
            return api.call("/api/v1/ready", timeout=min(10, remaining))
        except (ReadinessUnavailable, urllib.error.URLError, TimeoutError):
            remaining = deadline - time.monotonic()
            require(remaining > 0, "Simulator readiness deadline exceeded")
            time.sleep(min(2, remaining))


def edge_backend(environment, oracle_staging_api=None):
    """Oracle is an explicit staging-only adapter; existing Render scope remains."""
    if oracle_staging_api is not None:
        require(
            environment == "Staging"
            and oracle_staging_api == "https://robot-api-staging.onesa.eu",
            "Oracle edge qualification requires the exact staging API",
        )
        return oracle_staging_api
    return origin(os.environ["ROBOT_API_URL"], ".onrender.com")


def run(url, environment, *, oracle_staging_api=None, identity_login=None):
    record = {
        "scope": "software-simulation-only",
        "status": "BLOCKED",
        "checks": {name: "BLOCKED" for name in CHECKS},
        "physical_hardware_approved": False,
    }
    # Refuse an Oracle production/foreign target before any login or simulator
    # operation. This opt-in is target selection, never release authorization.
    oracle_backend = (
        edge_backend(environment, oracle_staging_api)
        if oracle_staging_api is not None
        else None
    )
    require(
        identity_login is None
        or callable(identity_login)
        and environment == "Staging"
        and oracle_backend == "https://robot-api-staging.onesa.eu",
        "Identity simulator acceptance is exact Oracle staging only",
    )
    password = os.environ.get("ROBOT_TEST_PASSWORD", "")
    name = os.environ.get("ROBOT_TEST_OPERATOR", "")
    if (
        not password
        or not name
        or os.environ.get("ROBOT_TEST_SCOPE")
        != "approved-synthetic-" + environment.lower()
    ):
        return record
    if environment == "Staging":
        origin(url, ".vercel.app")
    else:
        require(
            origin(url) == origin(os.environ.get("ROBOT_PUBLIC_ORIGIN", "")),
            "Unapproved production operator origin",
        )
    api = Simulator(url)
    confirmation = (
        {"confirmed": True} if identity_login is not None else {"password": password}
    )

    def sign_in():
        login = (
            identity_login(api)
            if identity_login is not None
            else api.call("/api/v1/auth/login", {"name": name, "password": password})
        )
        if identity_login is not None:
            require(
                login.get("authentication_method") == "onesa"
                and login.get("role") == "administrator"
                and (login.get("confirmation_expires_at") or 0) > time.time(),
                "Fresh shared administrator identity required",
            )
        api.csrf = login["csrf"]
        return login

    def sign_out():
        result = api.call("/api/v1/auth/logout", {})
        if identity_login is not None:
            require(
                result.get("identity_signed_out") is True,
                "Shared identity revocation must be confirmed",
            )

    logged_in = False
    checks = record["checks"]
    phase = "frontend_availability"
    try:
        require(b"Software simulation" in api.call("/"), "Unexpected frontend")
        checks["frontend_availability"] = "PASS"
        phase = "backend_readiness"
        ready = wait_readiness(api)
        require(
            ready.get("mode") == "simulation"
            and ready.get("physical_actuation") is False
            and ready.get("deployment_environment") == environment.lower(),
            "Unsafe runtime",
        )
        checks["backend_readiness"] = "PASS"
        phase = "storage_isolation"
        require(
            ready.get("storage_mode")
            == os.environ.get(
                "ROBOT_STORAGE_MODE",
                "ephemeral-simulation" if environment == "Staging" else "persistent",
            ),
            "Storage scope mismatch",
        )
        checks["storage_isolation"] = "PASS"
        phase = "authentication"
        api.call("/api/v1/status", expected=401)
        api.call("/api/v1/admin/physical-operation", expected=401)
        api.call(
            "/api/v1/auth/login",
            {"name": name, "password": "invalid-synthetic-login"},
            expected=403 if identity_login is not None else 401,
        )
        login = sign_in()
        logged_in = True
        require(
            any(cookie.secure and cookie.name == "robot_session" for cookie in api.jar),
            "Secure cookie required",
        )
        checks["authentication"] = "PASS"
        phase = "authorization"
        api.call("/api/v1/stop", {}, expected=403, csrf=False)
        checks["authorization"] = "PASS"
        phase = "administrator_operation_permission"
        require(
            login.get("role") == "administrator", "Designated administrator required"
        )
        before_permission_history = api.call("/api/v1/tasks")
        permission = api.call("/api/v1/admin/physical-operation")
        require(
            permission["permission"] == "blocked",
            "Initial administrator permission must be blocked",
        )
        allowed = api.call(
            "/api/v1/admin/physical-operation/allow",
            {
                **confirmation,
                "expected_revision": permission["revision"],
            },
        )
        require(
            allowed["permission"] == "allowed"
            and allowed["controller_connection"] == "not_connected"
            and allowed["controller_readiness"] == "not_ready"
            and allowed["physical_operation"] == "inactive"
            and allowed["physical_actuation"] is False,
            "Administrator permission must not fabricate connected hardware",
        )
        blocked = api.call("/api/v1/admin/physical-operation/block", {})
        require(
            blocked["permission"] == "blocked"
            and blocked["physical_actuation"] is False
            and api.call("/api/v1/tasks") == before_permission_history,
            "Administrator control must preserve task history and hardware state",
        )
        checks["administrator_operation_permission"] = "PASS"
        phase = "edge_telemetry"
        # Independent outbound device identity is telemetry-only; it cannot approve tasks.
        edge_token = os.environ.get("ROBOT_EDGE_TOKEN", "")
        edge_device = os.environ.get("ROBOT_EDGE_DEVICE_ID", "")
        require(
            len(edge_token) >= 64 and edge_device.startswith("synthetic-"),
            "Synthetic edge identity required",
        )
        backend = oracle_backend or edge_backend(environment)
        opener = urllib.request.build_opener(NoRedirect())

        def edge_call(route, body=None):
            request = urllib.request.Request(
                backend + route,
                headers={
                    "Authorization": "Bearer " + edge_token,
                    "Content-Type": "application/json",
                    "User-Agent": "OnesaRobotQualification/1",
                },
                data=json.dumps(body).encode() if body is not None else None,
            )
            try:
                with opener.open(request, timeout=10) as response:
                    if response.code != 200:
                        raise SimulatorHTTPFailure(response.code)
                    return json.loads(response.read(16384))
            except urllib.error.HTTPError as error:
                raise SimulatorHTTPFailure(error.code) from None

        challenge = edge_call("/api/v1/edge/challenge")
        edge_call(
            "/api/v1/edge/telemetry",
            {
                "version": 1,
                "device_id": edge_device,
                "boot_challenge": challenge["boot_challenge"],
                "sequence": challenge["next_sequence"],
                "observed_at": time.time(),
                "mode": "simulation",
                "physical_actuation": False,
                "stopped": True,
                "position_radians": 0.0,
            },
        )
        require(
            api.call("/api/v1/status")["capabilities"]["edge_telemetry"]
            == "simulation_connected",
            "Missing edge telemetry",
        )
        time.sleep(4)
        require(
            api.call("/api/v1/status")["capabilities"]["edge_telemetry"]
            == "not_connected",
            "Stale edge appears connected",
        )
        checks["edge_telemetry"] = "PASS"
        phase = "stale_command"
        api.call("/api/v1/stop", {})

        def reset():
            api.call("/api/v1/reset", confirmation)

        def proposal(**changes):
            return {
                "command_id": str(uuid4()),
                "issued_at": time.time(),
                "target_radians": 0.1,
                "duration_seconds": 2,
                **changes,
            }

        def create(value):
            api.call("/api/v1/tasks", value, expected=201)
            return value["command_id"]

        def approve(identifier):
            api.call("/api/v1/tasks/" + identifier + "/approve", confirmation)

        reset()
        api.call("/api/v1/tasks", proposal(issued_at=time.time() - 60), expected=422)
        checks["stale_command"] = "PASS"
        phase = "safety_denial"
        api.call(
            "/api/v1/tasks",
            proposal(target_radians=0.9, duration_seconds=1),
            expected=422,
        )
        checks["safety_denial"] = "PASS"
        phase = "simulation"
        value = proposal()
        identifier = create(value)
        api.call("/api/v1/tasks", value, expected=409)
        approve(identifier)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            api.call("/api/v1/heartbeat", {})
            history = api.call("/api/v1/tasks")["tasks"]
            if any(
                task["id"] == identifier and task["state"] == "completed"
                for task in history
            ):
                break
            time.sleep(0.25)
        else:
            raise Rejected("Simulation did not complete")
        checks["simulation"] = "PASS"
        phase = "replay_rejection"
        api.call(
            "/api/v1/tasks/" + identifier + "/approve",
            confirmation,
            expected=409,
        )
        checks["replay_rejection"] = "PASS"
        phase = "cancellation"
        cancelled = create(proposal(target_radians=0.15, duration_seconds=10))
        approve(cancelled)
        api.call("/api/v1/tasks/" + cancelled + "/cancel", {})
        require(api.call("/api/v1/status")["stopped"], "Cancellation must latch stop")
        checks["cancellation"] = "PASS"
        phase = "connection_loss"
        reset()
        # Revoke the approving session: execution cannot survive reconnect/login.
        disconnected = create(proposal(target_radians=0.2, duration_seconds=10))
        approve(disconnected)
        sign_out()
        logged_in = False
        sign_in()
        logged_in = True
        require(api.call("/api/v1/status")["stopped"], "Lost authority did not stop")
        history = api.call("/api/v1/tasks")["tasks"]
        require(
            any(
                task["id"] == disconnected
                and task["state"] == "cancelled"
                and task["reason"] == "session_revoked"
                for task in history
            ),
            "Missing cancellation audit",
        )
        checks["connection_loss"] = "PASS"
        phase = "audit_history"
        require(
            any(
                task["id"] == identifier and task["state"] == "completed"
                for task in history
            )
            and any(
                task["id"] == cancelled and task["state"] == "cancelled"
                for task in history
            ),
            "Missing task audit",
        )
        checks["audit_history"] = "PASS"
        record["status"] = "PASS"
    except (
        Rejected,
        ValueError,
        KeyError,
        urllib.error.URLError,
        TimeoutError,
    ) as error:
        checks[phase] = "FAIL"
        record["status"] = "FAIL"
        record["failure"] = {"phase": phase, **failure_details(error)}
        reasons = {
            "Synthetic edge identity required": "EDGE_FIXTURE",
            "Missing edge telemetry": "EDGE_OBSERVATION",
            "Stale edge appears connected": "EDGE_EXPIRY",
        }
        if isinstance(error, Rejected) and str(error) in reasons:
            record["failure"]["reason"] = reasons[str(error)]
    finally:
        if logged_in:
            try:
                api.call("/api/v1/stop", {})
                sign_out()
            except (Rejected, ValueError, urllib.error.URLError, TimeoutError):
                record["status"] = "FAIL"
    return record


def save_smoke(record, directory):
    """Keep intermediate acceptance separate from the strict final record writer."""
    directory = Path(directory)
    require(not directory.is_symlink(), "Unsafe smoke output directory")
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as output:
        temporary = Path(output.name)
        try:
            os.chmod(temporary, 0o600)
            json.dump(record, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.replace(temporary, directory / "smoke.json")
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("environment", choices=["Staging", "Production"])
    parser.add_argument("--directory", required=True)
    args = parser.parse_args(argv)
    directory = Path(args.directory)
    operator_file = directory / (
        "operator-url.txt" if args.environment == "Production" else "frontend-url.txt"
    )
    from edge_fixture import restore

    restore(args.environment)
    result = run(operator_file.read_text().strip(), args.environment)
    save_smoke(result, directory)
    require(
        result["status"] == "PASS", "Hosted simulation acceptance blocked or failed"
    )


if __name__ == "__main__":
    main()
