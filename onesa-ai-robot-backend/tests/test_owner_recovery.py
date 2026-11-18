"""Owner-local recovery keeps real SQL fences and refuses incomplete proofs."""

import asyncio
import json
import os
import socket
import sqlite3
import stat
import struct
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from robot_api import owner_recovery as owner
from robot_api.main import create_app
from robot_api.turso import LEASE, JournalFailure, TursoJournal
from tests.test_idle_journal_recovery import close, expire, fail, history, prepared
from tests.test_turso import client, config
from tests.test_turso import remote as remote


def configuration(path):
    return owner.OwnerRecoveryConfiguration(
        socket_path=path / owner.SOCKET_NAME,
        installation_sha256="a" * 64,
        source_commit="b" * 40,
        image_digest="sha256:" + "c" * 64,
        configuration_sha256="d" * 64,
        environment="staging",
    )


async def ready_owner(tmp_path, remote_path):
    sim = await prepared(tmp_path)
    fail(sim)
    expire(remote_path)
    sim.journal_recovery_next_at = 0
    return sim, owner.OwnerRecovery(sim, configuration(tmp_path), lambda: True)


def receipt_for(checkpoint):
    value = checkpoint["checkpoint"]
    return {
        "record_id": str(uuid4()),
        "record_sha256": "e" * 64,
        "installation_sha256": value["binding"]["installation_sha256"],
        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
        "history_sha256": value["history_sha256"],
        "process_generation": value["process_generation"],
        "expires_at": time.time() + 60,
    }


async def request_for(recovery):
    checkpoint = await recovery.checkpoint()
    prepared = await recovery.prepare(
        {
            "command": "prepare",
            "checkpoint_sha256": checkpoint["checkpoint_sha256"],
            "receipt": receipt_for(checkpoint),
        }
    )
    return {
        "command": "attempt",
        "nonce": prepared["nonce"],
        "checkpoint_sha256": prepared["checkpoint_sha256"],
        "receipt_sha256": prepared["receipt_sha256"],
    }


@pytest.mark.parametrize("prior_attempts", [0, 2])
def test_owner_attempt_preserves_complete_history_and_revokes_authority(
    remote, tmp_path, prior_attempts
):
    path, _ = remote

    async def run():
        sim, recovery = await ready_owner(tmp_path, path)
        sim.journal_recovery_attempts = prior_attempts
        sim.db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)",
            ("private-session-hash", "private-csrf", time.time() + 60, "operator"),
        )
        sim.db.commit()
        previous_owner = sim.journal.owner
        original = history(path)
        try:
            checkpoint = await recovery.checkpoint()
            assert "private-session-hash" not in json.dumps(checkpoint)
            assert "private-csrf" not in json.dumps(checkpoint)
            assert checkpoint["checkpoint"]["history"][0]["id"] == "synthetic-terminal"
            result = await recovery.attempt(await request_for(recovery))
            assert result["state"] == "recovered"
            assert result["release_acceptance"] is False
            assert sim.journal.owner != previous_owner
            assert sim.journal_recovery_attempts == prior_attempts + 1
            assert sim.stopped and not sim.journal_failed
            assert sim.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
            assert sim.physical_permission()["permission"] == "blocked"
            assert history(path) == original
            assert recovery.pending is None
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize(
    "change",
    [
        "dirty",
        "active",
        "pending",
        "permission",
        "not_stopped",
        "budget",
        "failure",
        "legacy",
        "watchdog",
        "acknowledged",
        "unknown_state",
        "nonfinite",
    ],
)
def test_checkpoint_refuses_unproven_or_ineligible_runtime(remote, tmp_path, change):
    path, _ = remote

    async def run():
        sim, recovery = await ready_owner(tmp_path, path)
        original = history(path)
        try:
            if change == "dirty":
                sim.journal_dirty["synthetic-terminal"] = 1
            elif change == "active":
                sim.active = {"id": "synthetic-terminal"}
            elif change == "pending":
                sim.humanoid.pending = object()
            elif change == "permission":
                sim.physical_permission_owner = "old-session"
            elif change == "not_stopped":
                sim.db.execute("UPDATE safety SET stopped=0")
            elif change == "budget":
                sim.journal_recovery_attempts = 3
            elif change == "failure":
                sim.journal_failure_metadata["category"] = "http_status"
            elif change == "legacy":
                sim.journal_failure_metadata = None
            elif change == "watchdog":
                recovery.watchdog_healthy = lambda: False
            elif change == "acknowledged":
                sim.journal.acknowledged.clear()
            elif change == "unknown_state":
                sim.db.execute("UPDATE tasks SET state='unknown'")
            elif change == "nonfinite":
                sim.db.execute("UPDATE tasks SET position=?", (float("inf"),))
            with pytest.raises(owner.RecoveryRefused):
                await recovery.checkpoint()
            assert sim.journal_failed
            assert recovery.pending is None
            assert history(path) == original
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize(
    "field",
    [
        "installation_sha256",
        "checkpoint_sha256",
        "history_sha256",
        "process_generation",
        "expires_at",
        "PASS",
        "record_sha256",
    ],
)
def test_prepare_rejects_changed_stale_or_caller_pass_receipts(remote, tmp_path, field):
    path, _ = remote

    async def run():
        sim, recovery = await ready_owner(tmp_path, path)
        try:
            checkpoint = await recovery.checkpoint()
            receipt = receipt_for(checkpoint)
            receipt[field] = (
                time.time() - 1
                if field == "expires_at"
                else ("PASS" if field in {"PASS", "record_sha256"} else "0" * 64)
            )
            with pytest.raises(owner.RecoveryRefused):
                await recovery.prepare(
                    {
                        "command": "prepare",
                        "checkpoint_sha256": checkpoint["checkpoint_sha256"],
                        "receipt": receipt,
                    }
                )
            assert recovery.pending is None
            assert sim.journal_recovery_attempts == 0
            assert sim.journal_failed
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize("field", ["receipt_sha256", "checkpoint_sha256", "expired"])
def test_consumed_nonce_cannot_retry_after_proof_failure(remote, tmp_path, field):
    path, _ = remote

    async def run():
        sim, recovery = await ready_owner(tmp_path, path)
        try:
            request = await request_for(recovery)
            if field == "expired":
                recovery.pending["deadline"] = time.monotonic() - 1
            else:
                request[field] = "0" * 64
            with pytest.raises(owner.RecoveryRefused):
                await recovery.attempt(request)
            with pytest.raises(owner.RecoveryRefused, match="MISSING_OR_CONSUMED"):
                await recovery.attempt(request)
            assert recovery.pending is None
            assert sim.journal_recovery_attempts == 0
            assert sim.journal_failed
        finally:
            await close(sim)

    asyncio.run(run())


def test_concurrent_owner_requests_make_one_fenced_attempt(remote, tmp_path):
    path, _ = remote

    async def run():
        sim, recovery = await ready_owner(tmp_path, path)
        try:
            request = await request_for(recovery)
            results = await asyncio.gather(
                recovery.attempt(request), recovery.attempt(request), return_exceptions=True
            )
            assert (
                sum(
                    isinstance(result, dict) and result["state"] == "recovered"
                    for result in results
                )
                == 1
            )
            assert sum(isinstance(result, owner.RecoveryRefused) for result in results) == 1
            assert sim.journal_recovery_attempts == 1
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize("change", ["session", "stop", "permission", "dirty"])
def test_changes_during_remote_acquire_keep_latch_and_history(
    remote, tmp_path, monkeypatch, change
):
    path, _ = remote
    initialize = TursoJournal.initialize

    async def run():
        sim, recovery = await ready_owner(tmp_path, path)
        original = history(path)
        try:
            request = await request_for(recovery)

            async def changed(journal):
                rows = await initialize(journal)
                if change == "session":
                    sim.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", ("new", "csrf", 1, "op"))
                    sim.db.commit()
                elif change == "stop":
                    sim.stop_epoch += 1
                elif change == "permission":
                    sim.physical_permission_revision += 1
                elif change == "dirty":
                    sim.journal_dirty["synthetic-terminal"] = 1
                return rows

            monkeypatch.setattr(TursoJournal, "initialize", changed)
            result = await recovery.attempt(request)
            assert result["state"] == "latched"
            assert sim.journal_failed and not sim.journal_recovery_allowed
            assert sim.journal_recovery_attempts == 1
            assert history(path) == original
            assert recovery.pending is None
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize("cancelled", [False, True])
def test_ambiguous_or_cancelled_acquire_never_replays_owner_attempt(
    remote, tmp_path, monkeypatch, cancelled
):
    path, _ = remote
    initialize = TursoJournal.initialize

    async def run():
        sim, recovery = await ready_owner(tmp_path, path)
        original = history(path)
        entered = asyncio.Event()
        try:
            request = await request_for(recovery)

            async def ambiguous(journal):
                await initialize(journal)  # Lease acquisition committed; reply becomes ambiguous.
                entered.set()
                if cancelled:
                    await asyncio.Event().wait()
                raise JournalFailure("timeout", "lease_acquire", 2000)

            monkeypatch.setattr(TursoJournal, "initialize", ambiguous)
            task = asyncio.create_task(recovery.attempt(request))
            if cancelled:
                await entered.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                assert (await task)["state"] == "latched"
            assert sim.journal_failed and not sim.journal_recovery_allowed
            assert sim.journal_recovery_attempts == 1
            assert history(path) == original
            with pytest.raises(owner.RecoveryRefused, match="MISSING_OR_CONSUMED"):
                await recovery.attempt(request)
            with sqlite3.connect(path) as db:
                assert db.execute(f"SELECT count(*) FROM {LEASE}").fetchone()[0] == 0
        finally:
            await close(sim)

    asyncio.run(run())


def test_terminal_history_rejects_duplicates_truncation_unknown_fields_and_nonfinite():
    row = {
        "id": "one",
        "owner": "operator",
        "proposal": "{}",
        "state": "completed",
        "reason": "",
        "created": 1.0,
        "position": 0.0,
    }
    assert owner.terminal_history([row]) == [row]
    for rows in (
        [row, row],
        [{**row, "state": "running"}],
        [{**row, "unknown": 0}],
        [{**row, "position": float("nan")}],
        [{**row, "created": True}],
        [{**row, "proposal": "x" * 16385}],
    ):
        with pytest.raises(owner.RecoveryRefused):
            owner.terminal_history(rows)


@pytest.mark.parametrize("pid,uid,accepted", [(3, 0, True), (0, 0, False), (3, 10001, False)])
def test_peer_identity_comes_from_kernel_not_request(pid, uid, accepted):
    calls = []

    def getsockopt(*args):
        calls.append(args)
        return struct.pack("3i", pid, uid, 0)

    writer = SimpleNamespace(get_extra_info=lambda key: SimpleNamespace(getsockopt=getsockopt))
    assert owner.root_peer(writer) is accepted
    assert calls == [(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)]


def test_default_factory_has_no_owner_recovery_route_or_socket(remote, tmp_path):
    with client(config(tmp_path)) as api:
        assert api.get("/api/v1/owner/recovery").status_code == 404
        assert not (tmp_path / owner.SOCKET_NAME).exists()
    assert create_app.__kwdefaults__ == {"owner_recovery": None}


def test_socket_refuses_symlinks_existing_files_and_nonprivate_parent(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / owner.SOCKET_NAME
    owner.private_socket_parent(path)
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(owner.RecoveryRefused):
        owner.private_socket_parent(path)
    path.unlink()
    path.write_text("existing")
    with pytest.raises(owner.RecoveryRefused):
        owner.private_socket_parent(path)
    path.unlink()
    private.chmod(0o755)
    with pytest.raises(owner.RecoveryRefused):
        owner.private_socket_parent(path)
    private.chmod(0o700)
    link = tmp_path / "linked"
    link.symlink_to(private, target_is_directory=True)
    with pytest.raises(owner.RecoveryRefused):
        owner.private_socket_parent(link / owner.SOCKET_NAME)


def test_real_socket_denies_nonroot_unknown_duplicate_and_oversized_requests(
    remote, tmp_path, monkeypatch
):
    path, _ = remote

    async def run(directory):
        sim, recovery = await ready_owner(tmp_path, path)
        recovery.configuration = configuration(directory)
        server = owner.OwnerRecoveryServer(recovery)
        await server.start()
        try:
            assert stat.S_IMODE(recovery.configuration.socket_path.stat().st_mode) == 0o600

            async def call(payload):
                reader, writer = await asyncio.open_unix_connection(
                    recovery.configuration.socket_path
                )
                writer.write(payload)
                await writer.drain()
                result = json.loads(await reader.readline())
                writer.close()
                await writer.wait_closed()
                return result

            if os.geteuid() != 0:
                assert (await call(b'{"command":"checkpoint","uid":0}\n'))["state"] == "refused"
            monkeypatch.setattr(owner, "root_peer", lambda writer: True)
            result = await call(b'{"command":"checkpoint"}\n')
            assert result["scope"] == "private-runtime-checkpoint"
            for payload in (
                b'{"command":"reset"}\n',
                b'{"command":"reset","command":"checkpoint"}\n',
                b"x" * (owner.MAX_REQUEST_BYTES + 1) + b"\n",
            ):
                assert (await call(payload))["state"] == "refused"
            assert sim.journal_failed and sim.journal_recovery_attempts == 0
        finally:
            await server.close()
            await close(sim)
        assert not recovery.configuration.socket_path.exists()

    with tempfile.TemporaryDirectory(prefix="robot-owner-") as directory:
        asyncio.run(run(Path(directory)))
