"""Opt-in owner-local evidence and one bounded, already-eligible journal attempt.

This channel never verifies release authority or a backup by accepting a caller's
PASS. The commissioned external owner verifier must validate the signed receipt
chain, actual runtime and isolated restore before requesting a nonce. Kernel root
peer authentication protects that private handoff. Runtime safety, history,
generation, freshness and the existing attempt budget are independently enforced
here. No public route, automatic enablement or legacy-process fallback exists.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import math
import os
import re
import secrets
import socket
import stat
import struct
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from robot_api.turso import COLUMNS, MAX_RESPONSE_BYTES

if TYPE_CHECKING:
    from robot_api.main import State

MAX_REQUEST_BYTES = 8192
NONCE_SECONDS = 20
SOCKET_NAME = "journal-recovery.sock"


class RecoveryRefused(ValueError):
    """Only fixed codes cross this private protocol; no provider or task text."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise RecoveryRefused(code)


def canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise RecoveryRefused("INVALID_CANONICAL_EVIDENCE") from None


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def terminal_history(rows: list[dict]) -> list[dict]:
    """Keep complete values; never collapse duplicates or normalize bad numbers."""
    require(len(rows) <= 10000, "HISTORY_CAPACITY")
    seen: set[str] = set()
    result = []
    for row in rows:
        require(isinstance(row, dict) and set(row) == set(COLUMNS), "HISTORY_FIELDS")
        require(
            all(isinstance(row[key], str) for key in COLUMNS[:5])
            and bool(row["id"])
            and row["id"] not in seen
            and row["state"] in {"completed", "cancelled"}
            and len(row["proposal"].encode()) <= 16384
            and all(
                type(row[key]) in (int, float) and math.isfinite(row[key]) for key in COLUMNS[5:]
            ),
            "INVALID_TERMINAL_HISTORY",
        )
        seen.add(row["id"])
        result.append(dict(row))
    result.sort(key=lambda row: row["id"])
    require(len(canonical(result)) <= MAX_RESPONSE_BYTES, "HISTORY_BYTES")
    return result


@dataclass(frozen=True)
class OwnerRecoveryConfiguration:
    """Explicit qualified factory input, not a public or environment-driven switch.

    These immutable identities bind the separately verified installation. Their
    syntax is checked here; provenance is checked by the external owner verifier.
    No existing deployment configuration is changed by defining this interface.
    """

    socket_path: Path
    installation_sha256: str
    source_commit: str
    image_digest: str
    configuration_sha256: str
    environment: str

    def binding(self) -> dict[str, str]:
        value = asdict(self)
        del value["socket_path"]
        require(
            all(
                re.fullmatch(r"[a-f0-9]{64}", value[key]) is not None
                for key in ("installation_sha256", "configuration_sha256")
            )
            and re.fullmatch(r"[a-f0-9]{40}", self.source_commit) is not None
            and re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest) is not None
            and self.environment in {"staging", "production"},
            "EXACT_INSTALLATION_BINDING_REQUIRED",
        )
        return value


class OwnerRecovery:
    def __init__(
        self,
        state: State,
        configuration: OwnerRecoveryConfiguration,
        watchdog_healthy: Callable[[], bool],
    ):
        self.state = state
        self.configuration = configuration
        self.binding = configuration.binding()
        require(
            state.settings.storage_mode == "turso"
            and state.settings.deployment_environment == configuration.environment,
            "EXACT_REMOTE_RUNTIME_REQUIRED",
        )
        self.watchdog_healthy = watchdog_healthy
        self.generation = secrets.token_hex(32)
        self.pending: dict | None = None

    def snapshot_locked(self, *, expected_attempts: int | None = None) -> dict:
        sim = self.state
        require(
            sim.journal is not None
            and sim.journal_initialized
            and sim.journal_failed
            and self.watchdog_healthy()
            and sim.journal_failure_metadata is not None,
            "CURRENT_LATCHED_PROCESS_PROOF_REQUIRED",
        )
        assert sim.journal is not None and sim.journal_failure_metadata is not None
        local = terminal_history(
            [dict(row) for row in sim.db.execute(f"SELECT {','.join(COLUMNS)} FROM tasks")]
        )
        acknowledged = terminal_history(list(sim.journal.acknowledged.values()))
        require(
            set(sim.journal.acknowledged) == {row["id"] for row in acknowledged}
            and canonical(local) == canonical(acknowledged),
            "EXACT_ACKNOWLEDGED_HISTORY_REQUIRED",
        )
        require(
            sim.journal_recovery_allowed
            and (sim.journal_recovery_attempts < 3 or expected_attempts == 3)
            and sim.active is None
            and sim.humanoid.active is None
            and sim.humanoid.pending is None
            and sim.stopped
            and not sim.journal_dirty
            and not sim.settings.actuation_enabled
            and sim.physical_permission_owner is None
            and sim.physical_permission_expires == 0
            and sim.journal_failure_metadata["phase"] == "lease_renew"
            and sim.journal_failure_metadata["category"] in {"timeout", "transport"},
            "ELIGIBLE_STOPPED_IDLE_BUDGET_REQUIRED",
        )
        authority = {
            "legacy": [
                list(row) for row in sim.db.execute("SELECT * FROM sessions ORDER BY token")
            ],
            "identity_sessions": {key: asdict(row) for key, row in sim.identity_sessions.items()},
            "identity_flows": {key: asdict(row) for key, row in sim.identity_flows.items()},
            "physical_revision": sim.physical_permission_revision,
        }
        # Raw session/CSRF/access-token values stay inside this one-way digest.
        value = {
            "schema_version": 1,
            "binding": dict(self.binding),
            "process_generation": self.generation,
            "process_id": os.getpid(),
            "failure": dict(sim.journal_failure_metadata),
            "attempts": sim.journal_recovery_attempts,
            "next_attempt_at": sim.journal_recovery_next_at,
            "stop_epoch": sim.stop_epoch,
            "journal_version": sim.journal_version,
            "authority_sha256": digest(authority),
            "history_sha256": digest(local),
            "history": local,
            "stopped": True,
            "active": False,
            "pending": False,
            "dirty": False,
            "physical_permission": False,
            "physical_actuation": False,
        }
        if expected_attempts is not None:
            require(sim.journal_recovery_attempts == expected_attempts, "ATTEMPT_BUDGET_CHANGED")
        return value

    async def checkpoint(self) -> dict:
        async with self.state.mutation_lock, self.state.journal_lock:
            snapshot = self.snapshot_locked()
            return {
                "scope": "private-runtime-checkpoint",
                "release_acceptance": False,
                "checkpoint": snapshot,
                "checkpoint_sha256": digest(snapshot),
            }

    async def prepare(self, request: dict) -> dict:
        require(set(request) == {"command", "checkpoint_sha256", "receipt"}, "PREPARE_FIELDS")
        receipt = request["receipt"]
        require(
            isinstance(receipt, dict)
            and set(receipt)
            == {
                "record_id",
                "record_sha256",
                "installation_sha256",
                "checkpoint_sha256",
                "history_sha256",
                "process_generation",
                "expires_at",
            }
            and isinstance(receipt["record_id"], str)
            and re.fullmatch(r"[a-f0-9-]{36}", receipt["record_id"]) is not None
            and all(
                isinstance(receipt[key], str)
                and re.fullmatch(r"[a-f0-9]{64}", receipt[key]) is not None
                for key in (
                    "record_sha256",
                    "installation_sha256",
                    "checkpoint_sha256",
                    "history_sha256",
                    "process_generation",
                )
            )
            and type(receipt["expires_at"]) in (int, float)
            and math.isfinite(receipt["expires_at"])
            and time.time() < receipt["expires_at"] <= time.time() + 300,
            "BOUND_CURRENT_RECEIPT_IDENTITY_REQUIRED",
        )
        async with self.state.mutation_lock, self.state.journal_lock:
            require(
                self.pending is None or time.monotonic() >= self.pending["deadline"],
                "ONE_OWNER_NONCE_AT_A_TIME",
            )
            snapshot = self.snapshot_locked()
            require(
                time.monotonic() >= self.state.journal_recovery_next_at
                and request["checkpoint_sha256"] == digest(snapshot)
                and receipt["checkpoint_sha256"] == digest(snapshot)
                and receipt["history_sha256"] == snapshot["history_sha256"]
                and receipt["process_generation"] == self.generation
                and receipt["installation_sha256"] == self.binding["installation_sha256"],
                "CHECKPOINT_OR_RECEIPT_CHANGED",
            )
            nonce = secrets.token_hex(32)
            lifetime = min(NONCE_SECONDS, receipt["expires_at"] - time.time())
            self.pending = {
                "nonce": nonce,
                "deadline": time.monotonic() + lifetime,
                "snapshot": snapshot,
                "receipt": dict(receipt),
            }
            return {
                "nonce": nonce,
                "process_generation": self.generation,
                "checkpoint_sha256": digest(snapshot),
                "receipt_sha256": digest(receipt),
                "expires_in_seconds": lifetime,
                "release_acceptance": False,
            }

    async def attempt(self, request: dict) -> dict:
        require(
            set(request) == {"command", "nonce", "checkpoint_sha256", "receipt_sha256"},
            "ATTEMPT_FIELDS",
        )
        pending = self.pending
        require(pending is not None, "OWNER_NONCE_MISSING_OR_CONSUMED")
        assert pending is not None
        require(
            isinstance(request["nonce"], str)
            and secrets.compare_digest(request["nonce"], pending["nonce"]),
            "OWNER_NONCE_MISMATCH",
        )
        # Consume before locks, remote I/O, cancellation or any other await.
        self.pending = None
        require(
            time.monotonic() < pending["deadline"]
            and time.time() < pending["receipt"]["expires_at"]
            and request["checkpoint_sha256"] == digest(pending["snapshot"])
            and request["receipt_sha256"] == digest(pending["receipt"]),
            "OWNER_PROOF_EXPIRED_OR_CHANGED",
        )

        def guard(after_acquire: bool) -> bool:
            try:
                expected = pending["snapshot"]
                snapshot = self.snapshot_locked(
                    expected_attempts=expected["attempts"] + int(after_acquire)
                )
                snapshot["attempts"] = expected["attempts"]
                return (
                    time.monotonic() < pending["deadline"]
                    and time.time() < pending["receipt"]["expires_at"]
                    and canonical(snapshot) == canonical(expected)
                )
            except RecoveryRefused:
                return False

        recovered = False
        try:
            recovered = await self.state.recover_idle_journal(owner_guard=guard)
            return {
                "state": "recovered" if recovered else "latched",
                "automatic_retry": False,
                "release_acceptance": False,
                "physical_actuation": False,
            }
        finally:
            if not recovered:
                # An ambiguous owner attempt is not a new automatic retry budget.
                self.state.journal_recovery_allowed = False


def private_socket_parent(path: Path) -> None:
    require(path.is_absolute() and path.name == SOCKET_NAME, "EXACT_OWNER_SOCKET_PATH")
    uid = os.geteuid()
    for parent in (path.parent, *path.parent.parents):
        info = parent.lstat()
        require(
            stat.S_ISDIR(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and info.st_uid in {0, uid}
            and (
                info.st_mode & 0o022 == 0 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX != 0
            ),
            "OWNER_SOCKET_ANCESTOR_CHANGED",
        )
    info = path.parent.lstat()
    require(info.st_uid == uid and info.st_mode & 0o077 == 0, "PRIVATE_SOCKET_DIRECTORY")
    require(not path.exists() and not path.is_symlink(), "OWNER_SOCKET_ALREADY_EXISTS")


def root_peer(writer: asyncio.StreamWriter) -> bool:
    connection = writer.get_extra_info("socket")
    if connection is None or not hasattr(socket, "SO_PEERCRED"):
        return False
    try:
        pid, uid, _gid = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        return pid > 0 and uid == 0
    except (OSError, struct.error):
        return False


class OwnerRecoveryServer:
    def __init__(self, recovery: OwnerRecovery):
        self.recovery = recovery
        self.server: asyncio.AbstractServer | None = None
        self.socket_identity: tuple[int, int] | None = None
        self.handlers: set[asyncio.Task] = set()

    async def start(self) -> None:
        require(hasattr(socket, "SO_PEERCRED"), "KERNEL_PEER_CREDENTIALS_REQUIRED")
        path = self.recovery.configuration.socket_path
        private_socket_parent(path)
        self.server = await asyncio.start_unix_server(
            self.handle, path=str(path), limit=MAX_REQUEST_BYTES + 1, start_serving=False
        )
        path.chmod(0o600)
        info = path.lstat()
        require(
            stat.S_ISSOCK(info.st_mode)
            and info.st_uid == os.geteuid()
            and info.st_mode & 0o777 == 0o600,
            "EXACT_OWNER_SOCKET_REQUIRED",
        )
        self.socket_identity = (info.st_dev, info.st_ino)
        await self.server.start_serving()

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        handlers = list(self.handlers)
        for task in handlers:
            task.cancel()
        if handlers:
            await asyncio.gather(*handlers, return_exceptions=True)
        path = self.recovery.configuration.socket_path
        with contextlib.suppress(FileNotFoundError):
            info = path.lstat()
            if (
                self.socket_identity == (info.st_dev, info.st_ino)
                and stat.S_ISSOCK(info.st_mode)
                and info.st_uid == os.geteuid()
            ):
                path.unlink()

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.handlers.add(task)
        try:
            require(root_peer(writer), "ROOT_OWNER_PEER_REQUIRED")
            info = self.recovery.configuration.socket_path.lstat()
            require(
                self.socket_identity == (info.st_dev, info.st_ino)
                and stat.S_ISSOCK(info.st_mode)
                and info.st_uid == os.geteuid()
                and info.st_mode & 0o777 == 0o600,
                "OWNER_SOCKET_IDENTITY_CHANGED",
            )
            async with asyncio.timeout(3):
                raw = await reader.readline()
            require(len(raw) <= MAX_REQUEST_BYTES and raw.endswith(b"\n"), "REQUEST_BOUND")

            def unique_object(pairs):
                value = {}
                for key, item in pairs:
                    require(key not in value, "DUPLICATE_REQUEST_FIELD")
                    value[key] = item
                return value

            request = json.loads(raw, object_pairs_hook=unique_object)
            require(isinstance(request, dict), "REQUEST_OBJECT_REQUIRED")
            async with asyncio.timeout(25):
                if request == {"command": "checkpoint"}:
                    result = await self.recovery.checkpoint()
                elif request.get("command") == "prepare":
                    result = await self.recovery.prepare(request)
                elif request.get("command") == "attempt":
                    result = await self.recovery.attempt(request)
                else:
                    raise RecoveryRefused("UNKNOWN_OWNER_COMMAND")
            payload = canonical(result)
            require(len(payload) <= MAX_RESPONSE_BYTES + MAX_REQUEST_BYTES, "RESPONSE_BOUND")
        except (Exception, asyncio.CancelledError):
            # No arbitrary exception, task/history or credential data crosses errors.
            payload = b'{"state":"refused","release_acceptance":false}'
        try:
            writer.write(payload + b"\n")
            async with asyncio.timeout(3):
                await writer.drain()
        finally:
            writer.close()
            try:
                with contextlib.suppress(Exception):
                    async with asyncio.timeout(3):
                        await writer.wait_closed()
            finally:
                self.handlers.discard(task)
