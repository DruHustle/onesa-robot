"""Owner-installed read-only journal evidence; never repair or release authority.

Observe one existing simulation runtime, fixed diagnostic log fields and aggregate
remote state. No login, SQL write, lease takeover, task replay or container mutation
is permitted. Missing logs or terminal remote rows cannot prove in-memory safety.
"""

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parent.parent),
    str(Path(__file__).resolve().parent),
]
import oracle_install as installer
import oracle_robot as robot

LOG_LIMIT = 2 * 1024 * 1024
LOG_LINES = 2048
TIMESTAMP = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z"
PREFIX = rf"(?P<at>{TIMESTAMP}) (?:(?:WARNING|INFO):robot_api\.journal:|(?:WARNING|INFO): +)?"
LATCH = re.compile(
    PREFIX + r"Simulation journal latched category=(?P<category>[a-z_]+) "
    r"phase=(?P<phase>[a-z_]+) elapsed_ms=(?P<elapsed_ms>\d{1,6}) "
    r"http_status=(?P<http_status>\d{1,3}) "
    r"transport_kind=(?P<transport_kind>[A-Za-z]+) "
    r"reconciliation=(?P<reconciliation>[a-z_]+)"
)
RECOVERY = re.compile(
    PREFIX + r"Idle journal recovery (?:(?P<result>completed|attempt unavailable) "
    r"attempt=(?P<attempt>[1-3])|blocked: (?P<reason>history_changed|live_owner))"
)
CATEGORIES = {
    "timeout",
    "transport",
    "http_status",
    "protocol",
    "ownership",
    "schema",
    "capacity",
    "sqlite",
    "startup_deadline",
    "lease_stale",
    "unknown",
}
PHASES = {
    "query",
    "lease_schema",
    "lease_acquire",
    "history_schema",
    "history_read",
    "history_validate",
    "task_save",
    "task_save_reconcile",
    "lease_renew",
    "lease_renew_reconcile",
    "lease_probe",
    "lease_release",
    "initialize",
    "flush",
    "ready",
    "unknown",
}
TRANSPORTS = {
    "none",
    "ConnectError",
    "ConnectTimeout",
    "PoolTimeout",
    "ReadError",
    "ReadTimeout",
    "WriteError",
    "WriteTimeout",
    "RemoteProtocolError",
    "LocalProtocolError",
    "ProxyError",
    "UnsupportedProtocol",
    "other",
}
RECONCILIATIONS = {"not_attempted", "unavailable", "not_confirmed"}


def installed(root, receipt_pin):
    """Verify the immutable installer receipt, not a caller-supplied PASS claim."""
    robot.require(
        root.parent == installer.INSTALL_PARENT, "INSTALLED_DIAGNOSTIC_REQUIRED"
    )
    robot.private(root, directory=True)
    path = robot.private(root / "installation.json")
    robot.require(
        re.fullmatch(r"[a-f0-9]{64}", receipt_pin)
        and path.stat().st_size <= 131072
        and robot.sha(path) == receipt_pin,
        "INSTALLATION_RECEIPT_PIN_REQUIRED",
    )
    receipt = json.loads(path.read_bytes())
    robot.require(
        receipt["state"] == "INSTALLED_NOT_COMMISSIONED"
        and receipt["repository"] == "DruHustle/onesa-robot"
        and re.fullmatch(r"[a-f0-9]{40}", receipt["source"])
        and re.fullmatch(r"[a-f0-9]{40}", receipt["source_tree"])
        and root.name == "robot-" + receipt["source_tree"]
        and set(receipt["files"]) == set(installer.ALLOWED)
        and receipt["provider_requests"] == 0,
        "IMMUTABLE_INSTALLATION_RECEIPT_REQUIRED",
    )
    for local, pin in receipt["files"].items():
        path = robot.private(root / local)
        robot.require(
            re.fullmatch(r"[a-f0-9]{64}", pin) and robot.sha(path) == pin,
            "INSTALLED_DIAGNOSTIC_BYTES_CHANGED",
        )
    return {key: receipt[key] for key in ["source", "source_tree"]}


def runtime(environment):
    robot.require(environment in robot.TARGET, "EXACT_ROBOT_ENVIRONMENT_REQUIRED")
    rows = json.loads(robot.docker("inspect", robot.TARGET[environment], timeout=5))
    robot.require(len(rows) == 1, "EXACT_ROBOT_CONTAINER_REQUIRED")
    row = rows[0]
    value = robot.summary(row)
    robot.require(
        row["Name"] == "/" + robot.TARGET[environment]
        and re.fullmatch(r"[a-f0-9]{64}", value["container_id"])
        and re.fullmatch(r"sha256:[a-f0-9]{64}", value["image"])
        and re.fullmatch(r"[a-f0-9]{40}", value["source"])
        and re.fullmatch(TIMESTAMP, value["started_at"])
        and type(value["restart_count"]) is int
        and value["restart_count"] >= 0
        and row["State"]["Running"] is True,
        "BOUND_RUNNING_ROBOT_IDENTITY_REQUIRED",
    )
    entries = row["Config"]["Env"]
    robot.require(
        all(isinstance(entry, str) and "=" in entry for entry in entries),
        "ROBOT_RUNTIME_CONFIGURATION_REQUIRED",
    )
    configured = dict(entry.split("=", 1) for entry in entries)
    expected = robot.runtime_values(robot.ENV[environment])
    robot.require(
        len(configured) == len(entries)
        and configured.get("ROBOT_ACTUATION_ENABLED") == "false"
        and all(
            configured.get(key) == expected.get(key) and bool(expected.get(key))
            for key in [
                "ROBOT_TURSO_DATABASE_URL",
                "ROBOT_TURSO_AUTH_TOKEN",
                "ROBOT_ACTUATION_ENABLED",
            ]
        ),
        "EXACT_SIMULATION_JOURNAL_CONFIGURATION_REQUIRED",
    )
    return value


def log_bytes(identity):
    result = subprocess.run(
        [
            "/usr/bin/docker",
            "logs",
            "--timestamps",
            "--tail",
            str(LOG_LINES),
            "--since",
            identity["started_at"],
            identity["container_id"],
        ],
        capture_output=True,
        timeout=20,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    # Application warnings are normally on stderr. Neither stream is printed.
    robot.require(
        result.returncode == 0 and len(result.stdout) + len(result.stderr) <= LOG_LIMIT,
        "BOUNDED_CURRENT_PROCESS_LOGS_REQUIRED",
    )
    return result.stdout + b"\n" + result.stderr


def log_fields(raw, started_at):
    robot.require(len(raw) <= LOG_LIMIT + 1, "JOURNAL_LOG_BOUND")
    events = []
    start = dt.datetime.fromisoformat(started_at)
    for line in raw.decode("utf-8", errors="replace").splitlines():
        match = LATCH.fullmatch(line)
        if match:
            fields = match.groupdict()
            fields["elapsed_ms"] = int(fields["elapsed_ms"])
            fields["http_status"] = int(fields["http_status"])
            if not (
                fields["category"] in CATEGORIES
                and fields["phase"] in PHASES
                and fields["transport_kind"] in TRANSPORTS
                and fields["reconciliation"] in RECONCILIATIONS
                and fields["elapsed_ms"] <= 120000
                and (fields["http_status"] == 0 or 100 <= fields["http_status"] <= 599)
            ):
                continue
            fields["event"] = "journal_latched"
        else:
            match = RECOVERY.fullmatch(line)
            if not match:
                continue
            fields = {key: value for key, value in match.groupdict().items() if value}
            fields["event"] = "idle_recovery"
            if "attempt" in fields:
                fields["attempt"] = int(fields["attempt"])
        try:
            at = dt.datetime.fromisoformat(fields["at"])
        except ValueError:
            continue
        if at >= start:
            events.append(fields)
    events.sort(key=lambda fields: dt.datetime.fromisoformat(fields["at"]))
    return {
        "state": "observed",
        "scope": "bounded_current_process_log_window",
        "maximum_lines": LOG_LINES,
        "matching_events": len(events),
        "events": events[-16:],
        "complete_process_history": False,
    }


def remote_fields(rows):
    keys = {
        "tasks",
        "proposed",
        "running",
        "completed",
        "cancelled",
        "lease_rows",
        "valid_leases",
        "lease_remaining_seconds",
    }
    robot.require(len(rows) == 1 and set(rows[0]) == keys, "JOURNAL_AGGREGATE_SHAPE")
    row = rows[0]
    robot.require(
        all(
            type(row[key]) is int and 0 <= row[key] <= 2**63 - 1
            for key in keys - {"lease_remaining_seconds"}
        )
        and (
            row["lease_remaining_seconds"] is None
            or type(row["lease_remaining_seconds"]) is int
            and -(2**63) <= row["lease_remaining_seconds"] <= 2**63 - 1
        )
        and row["valid_leases"] <= row["lease_rows"],
        "JOURNAL_AGGREGATE_TYPES",
    )
    tasks = {
        key: row[key]
        for key in ["tasks", "proposed", "running", "completed", "cancelled"]
    }
    tasks["unknown_states"] = tasks["tasks"] - sum(
        tasks[key] for key in ["proposed", "running", "completed", "cancelled"]
    )
    robot.require(tasks["unknown_states"] >= 0, "JOURNAL_AGGREGATE_COUNTS")
    remaining = row["lease_remaining_seconds"]
    lease = "invalid"
    if row["lease_rows"] == 0 and row["valid_leases"] == 0 and remaining is None:
        lease = "missing"
    elif row["lease_rows"] == 1 and row["valid_leases"] == 1 and remaining is not None:
        lease = "live" if remaining > 0 else "expired"
    return {
        "state": "observed",
        "task_counts": tasks,
        "lease": {"state": lease, "remaining_database_seconds": remaining},
        "history_contents_validated": False,
        "current_process_lease_ownership": "unobserved",
    }


def diagnose(environment):
    before = runtime(environment)
    configuration_pin = robot.sha(robot.private(robot.ENV[environment]))
    try:
        logs = log_fields(log_bytes(before), before["started_at"])
    except Exception:  # noqa: BLE001 -- fixed failure metadata; never raw logs.
        logs = {"state": "unavailable"}
    try:
        journal = remote_fields(
            robot.journal(environment, robot.JOURNAL_DIAGNOSTIC_SQL)
        )
    except Exception:  # noqa: BLE001 -- provider errors may contain private contents.
        journal = {"state": "unavailable"}
    robot.require(
        runtime(environment) == before
        and robot.sha(robot.private(robot.ENV[environment])) == configuration_pin,
        "ROBOT_CHANGED_DURING_DIAGNOSTIC",
    )
    return {
        "schema_version": 1,
        "state": "DIAGNOSTIC_ONLY",
        "environment": environment,
        "runtime": before,
        "logs": logs,
        "remote_journal": journal,
        "in_memory_stopped_idle_dirty_and_recovery_budget": "unobserved",
        "recovery_authorized": False,
        "release_acceptance": False,
        "provider_mutations": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment", choices=sorted(robot.ENV), required=True)
    parser.add_argument("--installation-pin", required=True)
    args = parser.parse_args()
    robot.require(
        os.geteuid() == 0
        and os.getenv("SUDO_USER") == "ubuntu"
        and not os.getenv("GITHUB_ACTIONS"),
        "EXTERNAL_ROBOT_OWNER_ONLY",
    )
    root = Path(__file__).resolve().parent.parent
    robot.require(
        Path(installer.__file__).resolve() == root / "oracle_install.py"
        and Path(robot.__file__).resolve() == root / "runners/oracle_robot.py",
        "INSTALLED_DIAGNOSTIC_MODULES_REQUIRED",
    )
    tool = installed(root, args.installation_pin)
    print(json.dumps({**diagnose(args.environment), "tool_source": tool}))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- never disclose runtime credentials or logs.
        raise SystemExit(
            "ROBOT_JOURNAL_DIAGNOSTIC_REFUSED_INSPECT_PRIVATE_STATE"
        ) from None
