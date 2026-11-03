"""Read-only synthetic journal diagnostics; no provider or runtime acceptance."""

import copy
import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import oracle_robot as robot
import oracle_robot_journal_diagnostic as diagnostic

START = "2026-11-03T12:00:00.000000000Z"
LATCH = (
    "2026-11-03T12:00:01.000000001Z WARNING:robot_api.journal:"
    "Simulation journal latched category=timeout phase=lease_renew "
    "elapsed_ms=2000 http_status=0 transport_kind=ReadTimeout "
    "reconciliation=not_attempted"
)


def runtime_fixture():
    values = {
        "ROBOT_TURSO_DATABASE_URL": (
            "libsql://onesa-ai-robot-backend-staging-druhustle.aws-eu-west-1.turso.io"
        ),
        "ROBOT_TURSO_AUTH_TOKEN": "synthetic-private-token",
        "ROBOT_ACTUATION_ENABLED": "false",
    }
    return {
        "Name": "/" + robot.TARGET["staging"],
        "Id": "a" * 64,
        "Image": "sha256:" + "b" * 64,
        "State": {"StartedAt": START, "Running": True},
        "RestartCount": 0,
        "Config": {
            "Env": [key + "=" + value for key, value in values.items()],
            "Labels": {"org.onesa.source-commit": "c" * 40},
        },
    }, values


def aggregate_fixture():
    return [
        {
            "tasks": 3,
            "proposed": 0,
            "running": 0,
            "completed": 2,
            "cancelled": 1,
            "lease_rows": 1,
            "valid_leases": 1,
            "lease_remaining_seconds": -10,
        }
    ]


class LogTests(unittest.TestCase):
    def test_only_fixed_fields_are_returned_from_both_log_streams(self):
        raw = (LATCH + "\nsecret-token https://private.example/owner/task\n").encode()
        observed = diagnostic.log_fields(raw, START)
        self.assertEqual(observed["matching_events"], 1)
        self.assertEqual(observed["events"][0]["category"], "timeout")
        self.assertEqual(observed["events"][0]["elapsed_ms"], 2000)
        self.assertFalse(observed["complete_process_history"])
        self.assertNotIn("secret-token", json.dumps(observed))
        identity = robot.summary(runtime_fixture()[0])
        completed = SimpleNamespace(
            stdout=b"unrelated secret", stderr=raw, returncode=0
        )
        with patch.object(diagnostic.subprocess, "run", return_value=completed) as run:
            output = diagnostic.log_fields(diagnostic.log_bytes(identity), START)
        self.assertEqual(output, observed)
        command = run.call_args.args[0]
        self.assertEqual(
            command[:4], ["/usr/bin/docker", "logs", "--timestamps", "--tail"]
        )
        self.assertEqual(command[-3:], ["--since", START, identity["container_id"]])
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertNotIn("synthetic-private-token", str(run.call_args))

    def test_malformed_unknown_and_injected_log_fields_are_omitted(self):
        for changed in [
            LATCH + " secret-token",
            "arbitrary-prefix " + LATCH,
            LATCH.replace("category=timeout", "category=secret"),
            LATCH.replace("phase=lease_renew", "phase=secret"),
            LATCH.replace("elapsed_ms=2000", "elapsed_ms=120001"),
            LATCH.replace("http_status=0", "http_status=999"),
            LATCH.replace("transport_kind=ReadTimeout", "transport_kind=secret"),
            LATCH.replace("reconciliation=not_attempted", "reconciliation=secret"),
            LATCH.replace("2026-11-03", "2026-99-03"),
        ]:
            with self.subTest(changed=changed):
                self.assertEqual(
                    diagnostic.log_fields(changed.encode(), START)["events"], []
                )

    def test_old_process_logs_and_missing_latch_do_not_prove_recovery(self):
        old = LATCH.replace("12:00:01", "11:59:59")
        result = diagnostic.log_fields(old.encode(), START)
        self.assertEqual(result["events"], [])
        self.assertFalse(result["complete_process_history"])

    def test_recovery_events_remain_bounded_and_are_not_an_unlatch_decision(self):
        lines = [
            f"2026-11-03T12:00:{second:02}Z Idle journal recovery attempt unavailable attempt=1"
            for second in range(2, 25)
        ]
        lines.extend(
            [
                "2026-11-03T12:00:26Z Idle journal recovery blocked: history_changed",
                "2026-11-03T12:00:27Z INFO:robot_api.journal:Idle journal recovery completed attempt=3",
                "2026-11-03T12:00:28Z Idle journal recovery completed attempt=4",
            ]
        )
        result = diagnostic.log_fields("\n".join(lines).encode(), START)
        self.assertEqual(result["matching_events"], 25)
        self.assertEqual(len(result["events"]), 16)
        self.assertEqual(result["events"][-1]["result"], "completed")
        self.assertEqual(result["events"][-1]["attempt"], 3)
        self.assertFalse(result["complete_process_history"])

    def test_failed_oversized_or_slow_log_read_never_returns_raw_contents(self):
        identity = robot.summary(runtime_fixture()[0])
        for result in [
            SimpleNamespace(returncode=1, stdout=b"", stderr=b"secret"),
            SimpleNamespace(
                returncode=0, stdout=b"a" * (diagnostic.LOG_LIMIT + 1), stderr=b""
            ),
        ]:
            with patch.object(diagnostic.subprocess, "run", return_value=result):
                with self.assertRaises(robot.Refused) as error:
                    diagnostic.log_bytes(identity)
                self.assertNotIn("secret", str(error.exception))
        with (
            patch.object(
                diagnostic.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired("docker", 20),
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            diagnostic.log_bytes(identity)


class RemoteJournalTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.create_function("unixepoch", 0, lambda: 2_000_000_000)
        self.db.execute("CREATE TABLE robot_simulation_tasks_v1(id TEXT, state TEXT)")
        self.db.execute(
            "CREATE TABLE robot_simulation_lease_v1(id INTEGER, owner TEXT, expires INTEGER)"
        )
        self.db.executemany(
            "INSERT INTO robot_simulation_tasks_v1 VALUES (?,?)",
            [
                ("private-task-" + state, state)
                for state in ["completed", "cancelled", "proposed", "running", "other"]
            ],
        )
        self.db.execute(
            "INSERT INTO robot_simulation_lease_v1 VALUES (1,?,?)",
            ("a" * 32, 2_000_000_018),
        )

    def observe(self):
        return diagnostic.remote_fields(
            [dict(row) for row in self.db.execute(robot.JOURNAL_DIAGNOSTIC_SQL)]
        )

    def test_real_query_is_read_only_aggregate_and_uses_the_database_clock(self):
        before = list(self.db.iterdump())
        self.db.execute("PRAGMA query_only=ON")
        observed = self.observe()
        self.assertEqual(
            observed["task_counts"],
            {
                "tasks": 5,
                "completed": 1,
                "cancelled": 1,
                "proposed": 1,
                "running": 1,
                "unknown_states": 1,
            },
        )
        self.assertEqual(
            observed["lease"], {"state": "live", "remaining_database_seconds": 18}
        )
        self.assertEqual(list(self.db.iterdump()), before)
        self.assertNotIn("private-task", json.dumps(observed))
        self.assertNotIn("a" * 32, json.dumps(observed))
        self.assertFalse(observed["history_contents_validated"])
        self.assertEqual(observed["current_process_lease_ownership"], "unobserved")

    def test_expired_missing_foreign_or_malformed_lease_confers_no_recovery_authority(
        self,
    ):
        self.db.execute("UPDATE robot_simulation_lease_v1 SET expires=1999999999")
        self.assertEqual(self.observe()["lease"]["state"], "expired")
        self.db.execute(
            "UPDATE robot_simulation_lease_v1 SET owner='secret-private-owner'"
        )
        self.assertEqual(self.observe()["lease"]["state"], "invalid")
        self.db.execute("DELETE FROM robot_simulation_lease_v1")
        self.assertEqual(self.observe()["lease"]["state"], "missing")
        self.db.execute(
            "INSERT INTO robot_simulation_lease_v1 VALUES (2,?,?)",
            ("b" * 32, 2_000_000_030),
        )
        self.assertEqual(self.observe()["lease"]["state"], "invalid")

    def test_invalid_aggregates_fail_without_printing_provider_fields(self):
        for rows in [[], aggregate_fixture() * 2, [{"secret": "contents"}]]:
            with self.assertRaises(robot.Refused):
                diagnostic.remote_fields(rows)
        for key, value in [
            ("tasks", True),
            ("tasks", -1),
            ("tasks", 2),
            ("tasks", 2**63),
            ("lease_remaining_seconds", "30"),
            ("lease_remaining_seconds", True),
            ("valid_leases", 2),
        ]:
            rows = aggregate_fixture()
            rows[0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(robot.Refused):
                diagnostic.remote_fields(rows)

    def test_existing_journal_reader_still_rejects_arbitrary_sql_before_transport(self):
        values = runtime_fixture()[1]
        with (
            patch.object(robot, "runtime_values", return_value=values),
            patch.object(robot.urllib.request, "build_opener") as opener,
        ):
            for sql in [
                "DELETE FROM robot_simulation_lease_v1",
                robot.JOURNAL_DIAGNOSTIC_SQL + ";DELETE FROM robot_simulation_tasks_v1",
            ]:
                with self.assertRaisesRegex(robot.Refused, "READ_ONLY_ALLOWLISTED"):
                    robot.journal("staging", sql)
            opener.assert_not_called()


class ObservationTests(unittest.TestCase):
    def call(self, *, after=None, log_error=None, journal_error=None):
        row, _values = runtime_fixture()
        before = robot.summary(row)
        with (
            patch.object(
                diagnostic,
                "runtime",
                side_effect=[before, after or copy.deepcopy(before)],
            ),
            patch.object(robot, "private", side_effect=lambda path: path),
            patch.object(robot, "sha", return_value="d" * 64),
            patch.object(
                diagnostic,
                "log_bytes",
                return_value=LATCH.encode(),
                side_effect=log_error,
            ),
            patch.object(
                robot,
                "journal",
                return_value=aggregate_fixture(),
                side_effect=journal_error,
            ) as journal,
        ):
            result = diagnostic.diagnose("staging")
        journal.assert_called_once_with("staging", robot.JOURNAL_DIAGNOSTIC_SQL)
        return result

    def test_complete_snapshot_does_not_claim_readiness_or_authorize_restart(self):
        observed = self.call()
        self.assertEqual(observed["state"], "DIAGNOSTIC_ONLY")
        self.assertFalse(observed["recovery_authorized"])
        self.assertFalse(observed["release_acceptance"])
        self.assertEqual(observed["provider_mutations"], 0)
        self.assertEqual(
            observed["in_memory_stopped_idle_dirty_and_recovery_budget"], "unobserved"
        )
        self.assertNotIn("synthetic-private-token", json.dumps(observed))

    def test_missing_logs_and_provider_errors_return_only_fixed_unavailability(self):
        observed = self.call(
            log_error=ValueError("secret log"),
            journal_error=ValueError("secret provider response"),
        )
        self.assertEqual(observed["logs"], {"state": "unavailable"})
        self.assertEqual(observed["remote_journal"], {"state": "unavailable"})
        self.assertNotIn("secret", json.dumps(observed))
        self.assertFalse(observed["recovery_authorized"])

    def test_concurrent_runtime_or_configuration_change_invalidates_snapshot(self):
        before = robot.summary(runtime_fixture()[0])
        for key, value in [
            ("container_id", "e" * 64),
            ("restart_count", 1),
            ("source", "d" * 40),
            ("environment_sha256", "e" * 64),
        ]:
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(robot.Refused, "CHANGED_DURING"),
            ):
                self.call(after={**before, key: value})
        with (
            patch.object(diagnostic, "runtime", return_value=before),
            patch.object(robot, "private", side_effect=lambda path: path),
            patch.object(robot, "sha", side_effect=["a" * 64, "b" * 64]),
            patch.object(diagnostic, "log_bytes", return_value=b""),
            patch.object(robot, "journal", return_value=aggregate_fixture()),
            self.assertRaisesRegex(robot.Refused, "CHANGED_DURING"),
        ):
            diagnostic.diagnose("staging")

    def test_only_exact_running_simulation_and_matching_private_config_are_observed(
        self,
    ):
        row, values = runtime_fixture()
        with (
            patch.object(
                robot, "docker", return_value=json.dumps([row]).encode()
            ) as docker,
            patch.object(robot, "runtime_values", return_value=values),
        ):
            self.assertEqual(diagnostic.runtime("staging"), robot.summary(row))
        docker.assert_called_once_with("inspect", robot.TARGET["staging"], timeout=5)
        for changed in [
            {**values, "ROBOT_TURSO_AUTH_TOKEN": "other"},
            {**values, "ROBOT_ACTUATION_ENABLED": "true"},
        ]:
            with (
                patch.object(robot, "docker", return_value=json.dumps([row]).encode()),
                patch.object(robot, "runtime_values", return_value=changed),
                self.assertRaises(robot.Refused),
            ):
                diagnostic.runtime("staging")
        row["State"]["Running"] = False
        with (
            patch.object(robot, "docker", return_value=json.dumps([row]).encode()),
            self.assertRaises(robot.Refused),
        ):
            diagnostic.runtime("staging")
        with patch.object(robot, "docker") as docker, self.assertRaises(robot.Refused):
            diagnostic.runtime("arbitrary-container")
        docker.assert_not_called()


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.custody_check = robot.private
        custody = patch.object(
            robot, "private", side_effect=lambda path, **_kwargs: Path(path)
        )
        custody.start()
        self.addCleanup(custody.stop)
        self.temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / ("robot-" + "b" * 40)
        self.root.mkdir(mode=0o700)
        self.receipt = {
            "state": "INSTALLED_NOT_COMMISSIONED",
            "repository": "DruHustle/onesa-robot",
            "source": "a" * 40,
            "source_tree": "b" * 40,
            "files": {},
            "provider_requests": 0,
        }
        for local in diagnostic.installer.ALLOWED:
            path = self.root / local
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            data = b"synthetic immutable tool fixture"
            path.write_bytes(data)
            path.chmod(0o600)
            self.receipt["files"][local] = hashlib.sha256(data).hexdigest()
        data = robot.canonical(self.receipt)
        self.pin = hashlib.sha256(data).hexdigest()
        path = self.root / "installation.json"
        path.write_bytes(data)
        path.chmod(0o600)

    def installed(self, pin=None):
        with patch.object(diagnostic.installer, "INSTALL_PARENT", self.parent):
            return diagnostic.installed(self.root, pin or self.pin)

    def test_pinned_immutable_installation_is_required_without_granting_commission(
        self,
    ):
        observed = self.installed()
        self.assertEqual(observed, {"source": "a" * 40, "source_tree": "b" * 40})
        with self.assertRaises(robot.Refused):
            self.installed("c" * 64)
        (self.root / "runners/oracle_robot_journal_diagnostic.py").write_text(
            "tampered"
        )
        with self.assertRaisesRegex(robot.Refused, "BYTES_CHANGED"):
            self.installed()

    def test_uninstalled_or_symlinked_code_is_refused(self):
        with self.assertRaisesRegex(robot.Refused, "INSTALLED_DIAGNOSTIC_REQUIRED"):
            diagnostic.installed(self.root, self.pin)
        path = self.root / "runners/oracle_robot_journal_diagnostic.py"
        path.unlink()
        path.symlink_to(self.root / "oracle_owner.py")
        with self.assertRaisesRegex(robot.Refused, "ALIAS"):
            self.custody_check(path)

    def test_actions_and_nonowner_invocations_stop_before_installed_or_runtime_reads(
        self,
    ):
        for euid, variables in [
            (1000, {}),
            (0, {"SUDO_USER": "other"}),
            (0, {"SUDO_USER": "ubuntu", "GITHUB_ACTIONS": "true"}),
        ]:
            with (
                patch.object(
                    diagnostic.sys,
                    "argv",
                    [
                        "diagnostic",
                        "--environment",
                        "staging",
                        "--installation-pin",
                        self.pin,
                    ],
                ),
                patch.object(diagnostic.os, "geteuid", return_value=euid),
                patch.dict(os.environ, variables, clear=True),
                patch.object(diagnostic, "installed") as installed,
                patch.object(diagnostic, "diagnose") as diagnose,
            ):
                with self.assertRaisesRegex(robot.Refused, "EXTERNAL_ROBOT_OWNER_ONLY"):
                    diagnostic.main()
                installed.assert_not_called()
                diagnose.assert_not_called()


if __name__ == "__main__":
    unittest.main()
