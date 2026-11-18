"""Actual disposable signature/ledger and strict owner adapter failure proofs."""

import copy
import datetime as dt
import subprocess
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import oracle_owner as owner
import oracle_robot as robot
import oracle_robot_idle_recovery as idle
import oracle_robot_recovery as recovery
import test_oracle_owner as fixtures


def history():
    return [
        {
            "id": "disposable-history",
            "owner": "fixture",
            "proposal": "{}",
            "state": "completed",
            "reason": "original reason",
            "created": 1.0000000000000002,
            "position": 0.25,
        }
    ]


def runtime():
    return {
        "container_id": "2" * 64,
        "image": "sha256:" + "e" * 64,
        "source": "a" * 40,
        "started_at": "2026-11-10T00:00:00Z",
        "restart_count": 0,
        "environment_sha256": "3" * 64,
        "host_process_id": 4242,
        "boot_id": "11111111-2222-3333-4444-555555555555",
        "init_start_ticks": 12000,
        "pid_namespace": 999,
    }


def peer():
    return {
        "server_pid": 7,
        "server_uid": 10001,
        "boot_id": runtime()["boot_id"],
        "process_start_ticks": 12345,
        "socket_device": 12,
        "socket_inode": 3456,
    }


def verified_peer():
    return {**peer(), "host_server_pid": 4249}


def checkpoint():
    current = runtime()
    value = {
        "schema_version": 1,
        "binding": {
            "installation_sha256": "4" * 64,
            "source_commit": current["source"],
            "image_digest": current["image"],
            "configuration_sha256": current["environment_sha256"],
            "environment": "production",
        },
        "process_generation": "5" * 64,
        "process_id": 7,
        "failure": {
            "category": "timeout",
            "phase": "lease_renew",
            "elapsed_ms": 2000,
            "http_status": 0,
            "transport_kind": "ReadTimeout",
            "reconciliation": "not_attempted",
        },
        "attempts": 1,
        "next_attempt_at": 0,
        "stop_epoch": 1,
        "journal_version": 2,
        "authority_sha256": "6" * 64,
        "history_sha256": idle.digest(history()),
        "history": history(),
        "stopped": True,
        "active": False,
        "pending": False,
        "dirty": False,
        "physical_permission": False,
        "physical_actuation": False,
    }
    return {
        "scope": "private-runtime-checkpoint",
        "release_acceptance": False,
        "checkpoint": value,
        "checkpoint_sha256": idle.digest(value),
    }


def restore_result():
    return {
        "scope": "disposable-complete-terminal-history-restore",
        "history_sha256": idle.digest(history()),
        "rows": 1,
        "sessions_revoked": True,
        "stopped_idle": True,
        "physical_actuation": False,
        "production_writes": 0,
        "release_acceptance": False,
    }


class HistoryAndTransportTests(unittest.TestCase):
    def test_decimal_wire_round_trip_preserves_one_ulp_history(self):
        wire = history()
        wire[0]["created"] = "1.0000000000000002220446049"
        wire[0]["position"] = "0.25"
        self.assertEqual(idle.complete_history(wire, wire=True), history())

    def test_duplicate_unknown_active_truncated_and_nonfinite_history_are_refused(self):
        for kind in [
            "duplicate",
            "unknown",
            "active",
            "missing",
            "nonfinite",
            "bool",
            "null",
        ]:
            with self.subTest(kind=kind):
                rows = history()
                if kind == "duplicate":
                    rows.append(dict(rows[0]))
                elif kind == "unknown":
                    rows[0]["unknown"] = True
                elif kind == "active":
                    rows[0]["state"] = "proposed"
                elif kind == "missing":
                    del rows[0]["reason"]
                elif kind == "nonfinite":
                    rows[0]["created"] = "1e999"
                elif kind == "bool":
                    rows[0]["created"] = True
                else:
                    rows[0]["created"] = None
                with self.assertRaises(robot.Refused):
                    idle.complete_history(rows, wire=True)

    def test_checkpoint_rejects_stale_source_process_budget_and_caller_pass(self):
        for kind in ["source", "digest", "budget", "generation", "permission", "PASS"]:
            with self.subTest(kind=kind):
                before = checkpoint()
                value = before["checkpoint"]
                if kind == "source":
                    value["binding"]["source_commit"] = "f" * 40
                elif kind == "digest":
                    before["checkpoint_sha256"] = "f" * 64
                elif kind == "budget":
                    value["attempts"] = 3
                elif kind == "generation":
                    value["process_generation"] = "unavailable"
                elif kind == "permission":
                    value["physical_permission"] = True
                else:
                    value["PASS"] = True
                if kind != "digest":
                    before["checkpoint_sha256"] = idle.digest(value)
                with (
                    patch.object(idle, "channel", return_value=(before, peer())),
                    self.assertRaises(robot.Refused),
                ):
                    idle.checkpoint(runtime(), "4" * 64)

    def test_restore_uses_only_exact_local_image_no_network_mount_or_credentials(self):
        with patch.object(
            idle.subprocess,
            "run",
            return_value=SimpleNamespace(
                returncode=0, stdout=robot.canonical(restore_result()), stderr=b""
            ),
        ) as run:
            self.assertEqual(
                idle.isolated_restore(runtime(), checkpoint()["checkpoint"]),
                restore_result(),
            )
        command = run.call_args.args[0]
        self.assertEqual(
            command[:6], ["/usr/bin/docker", "run", "--rm", "--pull", "never", "-i"]
        )
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertEqual(command[command.index("--memory") + 1], "256m")
        self.assertNotIn("--volume", command)
        self.assertNotIn("--env", command)
        self.assertEqual(
            command[-4:], [runtime()["image"], "-m", idle.MODULE, "restore"]
        )
        self.assertEqual(run.call_args.kwargs["timeout"], 45)
        self.assertNotIn("original reason", str(command))

    def test_bridge_is_literal_root_module_without_injected_code_or_arbitrary_path(
        self,
    ):
        with (
            patch.object(
                idle,
                "invoke",
                return_value={"response": checkpoint(), "transport": peer()},
            ) as invoke,
            patch.object(idle, "server_process", return_value=4249),
        ):
            idle.channel(runtime(), {"command": "checkpoint"})
        self.assertEqual(
            invoke.call_args.args[0],
            [
                "exec",
                "--user",
                "0",
                "-i",
                "2" * 64,
                "python",
                "-m",
                idle.MODULE,
                "bridge",
            ],
        )
        self.assertEqual(
            invoke.call_args.args[1],
            {"request": {"command": "checkpoint"}, "expected_peer": None},
        )

    def test_mutation_bridge_envelope_contains_previously_verified_peer_before_io(self):
        request = {"command": "attempt", "nonce": "8" * 64}
        with (
            patch.object(
                idle,
                "invoke",
                return_value={"response": {"state": "latched"}, "transport": peer()},
            ) as invoke,
            patch.object(idle, "server_process", return_value=4249),
        ):
            idle.channel(runtime(), request, expected_peer=verified_peer())
        self.assertEqual(
            invoke.call_args.args[1], {"request": request, "expected_peer": peer()}
        )
        with patch.object(idle, "invoke") as invoke, self.assertRaises(robot.Refused):
            idle.channel(runtime(), request)
        invoke.assert_not_called()

    def test_kernel_server_uid_pid_boot_generation_and_socket_identity_are_bound(self):
        for key in [
            "server_pid",
            "server_uid",
            "boot_id",
            "process_start_ticks",
            "socket_inode",
        ]:
            with self.subTest(key=key):
                changed = peer()
                changed[key] = (
                    "99999999-2222-3333-4444-555555555555"
                    if key == "boot_id"
                    else changed[key] + 1
                )
                with (
                    patch.object(
                        idle,
                        "invoke",
                        return_value={"response": checkpoint(), "transport": changed},
                    ),
                    patch.object(idle, "server_process", return_value=4249),
                    self.assertRaises(robot.Refused),
                ):
                    idle.channel(
                        runtime(),
                        {"command": "checkpoint"},
                        expected_peer=verified_peer(),
                    )
        with (
            patch.object(
                idle,
                "invoke",
                return_value={"response": checkpoint(), "transport": peer()},
            ),
            patch.object(idle, "server_process", return_value=4249),
        ):
            result = idle.checkpoint(runtime(), "4" * 64)
        self.assertEqual(result, {**checkpoint(), "transport": verified_peer()})

    def test_independent_host_process_mapping_supports_init_and_refuses_stale_foreign_owner(
        self,
    ):
        original = {
            4242: {
                "start_ticks": 12000,
                "namespace_pids": [4242, 1],
                "uids": [10001] * 4,
                "pid_namespace": 999,
            },
            4249: {
                "start_ticks": 12345,
                "namespace_pids": [4249, 7],
                "uids": [10001] * 4,
                "pid_namespace": 999,
            },
        }
        for change in [None, "init", "start", "namespace", "uid", "missing"]:
            with self.subTest(change=change):
                data = copy.deepcopy(original)
                if change == "init":
                    data[4242]["start_ticks"] += 1
                if change == "start":
                    data[4249]["start_ticks"] += 1
                if change == "namespace":
                    data[4249]["pid_namespace"] += 1
                if change == "uid":
                    data[4249]["uids"] = [0] * 4
                if change == "missing":
                    data[4249]["namespace_pids"][-1] = 8
                with (
                    patch.object(
                        idle,
                        "kernel_process",
                        side_effect=lambda pid, data=data: data[pid],
                    ),
                    patch.object(robot, "docker", return_value="PID\n4242\n4249\n"),
                ):
                    if change is None:
                        self.assertEqual(idle.server_process(runtime(), peer()), 4249)
                    else:
                        with self.assertRaises(robot.Refused):
                            idle.server_process(runtime(), peer())

    def test_missing_legacy_socket_and_transport_errors_never_trigger_a_second_request(
        self,
    ):
        for response in [
            SimpleNamespace(returncode=1, stdout=b"", stderr=b"private detail"),
            SimpleNamespace(returncode=0, stdout=b'{"state":"refused"}', stderr=b""),
        ]:
            with (
                self.subTest(response=response.returncode),
                patch.object(idle.subprocess, "run", return_value=response) as run,
            ):
                with self.assertRaises(robot.Refused) as error:
                    idle.channel(runtime(), {"command": "checkpoint"})
                self.assertNotIn("private detail", str(error.exception))
                self.assertEqual(run.call_count, 1)

    def test_duplicate_or_nonfinite_response_is_refused(self):
        for raw in [b'{"state":"recovered","state":"latched"}', b'{"x":NaN}']:
            with self.subTest(raw=raw), self.assertRaises((ValueError, robot.Refused)):
                idle.decoded(raw)


class CustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.OwnerStoreTests.setUpClass()

    def setUp(self):
        self.fixture = fixtures.OwnerStoreTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store, self.root = self.fixture.store, self.fixture.root
        self.binding = self.fixture.binding
        old = self.binding["repository"]
        self.binding["repository"] = "DruHustle/onesa-robot"
        self.store.policy["components"][self.binding["repository"] + ":api"] = (
            self.store.policy["components"].pop(old + ":api")
        )
        (self.root / "policy.json").write_bytes(owner.canonical(self.store.policy))
        self.store.trust["policy_sha256"] = owner.file_digest(self.root / "policy.json")
        (self.root / "trust.json").write_bytes(owner.canonical(self.store.trust))
        self.fixture.commission_fixture()
        self.fixture.settings({"deployment_id": runtime()["container_id"]})
        authorization = self.fixture.authorization()
        with (
            patch.object(
                self.store,
                "inspect",
                side_effect=owner.Rejected("disposable observer unavailable"),
            ),
            self.assertRaises(owner.Rejected),
        ):
            self.store.deploy(authorization)
        self.operation = self.store.db.execute("SELECT id FROM deployments").fetchone()[
            0
        ]
        self.intent, self.receipt = recovery.original(self.store, self.operation)
        self.details = {
            "scope": "one-existing-eligible-idle-journal-attempt",
            "runtime": runtime(),
            "installation_sha256": "4" * 64,
            "checkpoint_sha256": checkpoint()["checkpoint_sha256"],
            "process_generation": "5" * 64,
            "history_sha256": idle.digest(history()),
            "restore": restore_result(),
            "backup": {"file": "private-fixture.json", "sha256": "7" * 64},
            "automatic_retry": False,
            "physical_hardware_authorized": False,
        }

    def signed_restore(self):
        return self.store.put(
            "idle-journal-restore",
            self.binding,
            self.details,
            parent=self.receipt["id"],
            ttl=119,
        )

    def test_exact_installed_source_receipt_image_configuration_and_main_gate(self):
        self.store.policy["toolchain"] = {
            "source_commit": self.binding["candidate_commit"],
            "source_tree": self.binding["source_tree"],
        }
        for change in [
            None,
            "source",
            "image",
            "container",
            "tree",
            "configuration",
            "ci",
        ]:
            with self.subTest(change=change), ExitStack() as stack:
                installed = {"source": "a" * 40, "source_tree": "b" * 40}
                actual = runtime()
                if change == "source":
                    actual["source"] = "9" * 40
                if change == "image":
                    actual["image"] = "sha256:" + "9" * 64
                if change == "container":
                    actual["container_id"] = "9" * 64
                if change == "tree":
                    installed["source_tree"] = "9" * 40
                stack.enter_context(
                    patch.object(idle.diagnostic, "installed", return_value=installed)
                )
                stack.enter_context(
                    patch.object(idle, "observed_runtime", return_value=actual)
                )
                stack.enter_context(
                    patch.object(robot, "private", return_value=Path("/disposable/env"))
                )
                stack.enter_context(
                    patch.object(
                        robot,
                        "sha",
                        return_value=("9" if change == "configuration" else "1") * 64,
                    )
                )
                self.fixture.ci.fail = change == "ci"
                if change is None:
                    binding, receipt, observed = idle.validate_installation(
                        self.store, self.operation, self.root, "4" * 64
                    )
                    self.assertEqual(
                        (binding, receipt, observed),
                        (self.binding, self.receipt, runtime()),
                    )
                else:
                    with self.assertRaises((robot.Refused, owner.Rejected)):
                        idle.validate_installation(
                            self.store, self.operation, self.root, "4" * 64
                        )
        self.fixture.ci.fail = False

    def test_actual_signature_and_each_parent_revocation_are_checked(self):
        receipt = self.signed_restore()
        identity = receipt["record"]["id"]
        actual = idle.current_restore(self.store, identity, self.binding, self.details)
        self.assertEqual(actual["record_sha256"], idle.digest(receipt))
        for (parent,) in self.store.db.execute("SELECT id FROM records").fetchall():
            with self.subTest(parent=parent):
                self.store.db.execute(
                    "UPDATE records SET revoked=1 WHERE id=?", (parent,)
                )
                try:
                    with self.assertRaises(owner.Rejected):
                        idle.current_restore(
                            self.store, identity, self.binding, self.details
                        )
                finally:
                    self.store.db.execute(
                        "UPDATE records SET revoked=0 WHERE id=?", (parent,)
                    )

    def test_tampered_signature_stale_receipt_and_changed_checkpoint_are_refused(self):
        receipt = self.signed_restore()
        identity = receipt["record"]["id"]
        changed = copy.deepcopy(self.details)
        changed["checkpoint_sha256"] = "0" * 64
        with self.assertRaises(robot.Refused):
            idle.current_restore(self.store, identity, self.binding, changed)
        current_clock = self.store.clock
        self.store.clock = lambda: current_clock() + dt.timedelta(seconds=121)
        with self.assertRaises(robot.Refused):
            idle.current_restore(self.store, identity, self.binding, self.details)
        self.store.clock = current_clock
        receipt["record"]["details"]["history_sha256"] = "0" * 64
        self.store.db.execute(
            "UPDATE records SET envelope=? WHERE id=?",
            (owner.canonical(receipt).decode(), identity),
        )
        with self.assertRaises(owner.Rejected):
            idle.current_restore(self.store, identity, self.binding, self.details)

    def test_committed_attempt_survives_reopening_and_cannot_replay(self):
        receipt = self.signed_restore()["record"]["id"]
        idle.record_attempt(self.store, receipt, self.binding, self.details)
        with self.assertRaises(robot.Refused):
            idle.record_attempt(self.store, receipt, self.binding, self.details)
        reopened = self.fixture.open()
        try:
            with self.assertRaises(robot.Refused):
                idle.record_attempt(reopened, receipt, self.binding, self.details)
            self.assertEqual(
                reopened.db.execute(
                    "SELECT count(*) FROM records WHERE kind='idle-journal-attempt'"
                ).fetchone()[0],
                1,
            )
        finally:
            reopened.db.close()

    def test_tampering_attempt_generation_does_not_reopen_single_attempt_budget(self):
        receipt = self.signed_restore()["record"]["id"]
        attempt = idle.record_attempt(self.store, receipt, self.binding, self.details)
        changed = copy.deepcopy(attempt)
        changed["record"]["details"]["process_generation"] = "0" * 64
        self.store.db.execute(
            "UPDATE records SET envelope=? WHERE id=?",
            (
                owner.canonical(changed).decode(),
                attempt["record"]["id"],
            ),
        )
        with self.assertRaises(owner.Rejected):
            idle.record_attempt(self.store, receipt, self.binding, self.details)
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM records WHERE kind='idle-journal-attempt'"
            ).fetchone()[0],
            1,
        )

    def test_retained_checkpoint_is_exact_private_and_never_overwritten(self):
        before = checkpoint()
        backup = idle.retained_checkpoint(self.store, before)
        path = self.root / backup["file"]
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(idle.retained_checkpoint(self.store, before), backup)
        path.write_bytes(b"changed private checkpoint")
        with self.assertRaises(robot.Refused):
            idle.retained_checkpoint(self.store, before)
        self.assertEqual(path.read_bytes(), b"changed private checkpoint")

    def test_ambiguous_attempt_is_recorded_before_io_and_never_repeated(self):
        before = {**checkpoint(), "transport": verified_peer()}
        calls = []

        def channel(_runtime, request, *, expected_peer):
            self.assertEqual(expected_peer, verified_peer())
            calls.append(request["command"])
            if request["command"] == "prepare":
                return {
                    "nonce": "8" * 64,
                    "process_generation": "5" * 64,
                    "checkpoint_sha256": before["checkpoint_sha256"],
                    "receipt_sha256": idle.digest(request["receipt"]),
                    "expires_in_seconds": 20,
                    "release_acceptance": False,
                }, verified_peer()
            self.assertEqual(
                self.store.db.execute(
                    "SELECT count(*) FROM records WHERE kind='idle-journal-attempt'"
                ).fetchone()[0],
                1,
            )
            raise subprocess.TimeoutExpired("fixed-owner-transport", 35)

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    idle,
                    "validate_installation",
                    return_value=(self.binding, self.receipt, runtime()),
                )
            )
            stack.enter_context(patch.object(idle, "checkpoint", return_value=before))
            stack.enter_context(
                patch.object(
                    idle, "remote_matches", return_value=idle.digest(history())
                )
            )
            stack.enter_context(
                patch.object(idle, "isolated_restore", return_value=restore_result())
            )
            stack.enter_context(
                patch.object(idle, "observed_runtime", return_value=runtime())
            )
            stack.enter_context(patch.object(idle, "channel", side_effect=channel))
            with self.assertRaises(subprocess.TimeoutExpired):
                idle.recover(self.store, self.operation, self.root, "4" * 64)
            with self.assertRaises(robot.Refused):
                idle.recover(self.store, self.operation, self.root, "4" * 64)
        self.assertEqual(calls.count("attempt"), 1)
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM records WHERE kind='idle-journal-result'"
            ).fetchone()[0],
            0,
        )

    def refuse_late_authority_change(self, kind):
        before = {**checkpoint(), "transport": verified_peer()}
        calls = []
        observations = 0

        def observed_runtime():
            nonlocal observations
            observations += 1
            if observations == 2:
                # This is after recover's last stand-alone receipt check and
                # before record_attempt starts its durable authorization barrier.
                restore_id = self.store.db.execute(
                    "SELECT id FROM records WHERE kind='idle-journal-restore'"
                ).fetchone()[0]
                identity = self.receipt["id"] if "parent" in kind else restore_id
                if kind.endswith("revoked"):
                    self.store.revoke(identity)
                elif kind == "expired":
                    previous_clock = self.store.clock
                    self.store.clock = lambda: (
                        previous_clock() + dt.timedelta(seconds=121)
                    )
                else:
                    changed = self.store.get(identity, historical=True)
                    changed["record"]["details"]["late_change"] = True
                    self.store.db.execute(
                        "UPDATE records SET envelope=? WHERE id=?",
                        (owner.canonical(changed).decode(), identity),
                    )
            return runtime()

        def channel(_runtime, request, *, expected_peer):
            self.assertEqual(expected_peer, verified_peer())
            calls.append(request["command"])
            if request["command"] == "prepare":
                return {
                    "nonce": "8" * 64,
                    "process_generation": "5" * 64,
                    "checkpoint_sha256": before["checkpoint_sha256"],
                    "receipt_sha256": idle.digest(request["receipt"]),
                    "expires_in_seconds": 20,
                    "release_acceptance": False,
                }, verified_peer()
            return {
                "state": "recovered",
                "automatic_retry": False,
                "release_acceptance": False,
                "physical_actuation": False,
            }, verified_peer()

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    idle,
                    "validate_installation",
                    return_value=(self.binding, self.receipt, runtime()),
                )
            )
            stack.enter_context(patch.object(idle, "checkpoint", return_value=before))
            stack.enter_context(
                patch.object(
                    idle, "remote_matches", return_value=idle.digest(history())
                )
            )
            stack.enter_context(
                patch.object(idle, "isolated_restore", return_value=restore_result())
            )
            stack.enter_context(
                patch.object(idle, "observed_runtime", side_effect=observed_runtime)
            )
            stack.enter_context(patch.object(idle, "channel", side_effect=channel))
            with self.assertRaises((robot.Refused, owner.Rejected)):
                idle.recover(self.store, self.operation, self.root, "4" * 64)
        self.assertEqual(calls, ["prepare"])
        self.assertEqual(
            self.store.db.execute(
                "SELECT count(*) FROM records WHERE kind='idle-journal-attempt'"
            ).fetchone()[0],
            0,
        )

    def test_late_revoked_restore_never_reaches_mutation_channel(self):
        self.refuse_late_authority_change("restore_revoked")

    def test_late_revoked_parent_never_reaches_mutation_channel(self):
        self.refuse_late_authority_change("parent_revoked")

    def test_late_expired_restore_never_reaches_mutation_channel(self):
        self.refuse_late_authority_change("expired")

    def test_late_changed_restore_never_reaches_mutation_channel(self):
        self.refuse_late_authority_change("restore_changed")

    def test_late_changed_parent_never_reaches_mutation_channel(self):
        self.refuse_late_authority_change("parent_changed")


if __name__ == "__main__":
    unittest.main()
