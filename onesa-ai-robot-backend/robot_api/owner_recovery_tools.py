"""Fixed private transport and disposable restore probe for qualified owners.

Neither command enables the optional runtime socket. The bridge only connects to
an already installed, exact owner socket. The restore command opens only a new
temporary local database and never receives a database URL or credential.
"""

from __future__ import annotations

import json
import os
import re
import socket
import stat
import struct
import sys
import tempfile
import time
from pathlib import Path

from robot_api.owner_recovery import (
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    canonical,
    digest,
    require,
    terminal_history,
)
from robot_api.turso import COLUMNS

SOCKET_PATH = Path("/run/onesa-owner/journal-recovery.sock")
SERVICE_UID = 10001
FRAME_BOUND = MAX_RESPONSE_BYTES + MAX_REQUEST_BYTES


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        require(key not in value, "DUPLICATE_PRIVATE_FIELD")
        value[key] = item
    return value


def decode(raw: bytes) -> dict:
    value = json.loads(raw, object_pairs_hook=unique_object)
    require(isinstance(value, dict), "PRIVATE_OBJECT_REQUIRED")
    canonical(value)  # Refuse JSON's optional NaN/Infinity extension.
    return value


def process_identity(pid: int) -> dict:
    require(type(pid) is int and pid > 0, "EXACT_KERNEL_PROCESS_REQUIRED")
    raw = Path(f"/proc/{pid}/stat").read_bytes()
    require(len(raw) <= 8192, "KERNEL_PROCESS_BOUND")
    fields = raw.rsplit(b") ", 1)[1].split()
    require(len(fields) >= 20 and fields[19].isdigit(), "KERNEL_PROCESS_START_REQUIRED")
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    require(
        re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot) is not None,
        "EXACT_KERNEL_BOOT_REQUIRED",
    )
    return {"boot_id": boot, "process_start_ticks": int(fields[19])}


def bridge(raw: bytes) -> bytes:
    require(os.geteuid() == 0, "KERNEL_ROOT_OWNER_REQUIRED")
    require(0 < len(raw) <= MAX_REQUEST_BYTES, "OWNER_REQUEST_BOUND")
    envelope = decode(raw)
    require(set(envelope) == {"request", "expected_peer"}, "FIXED_BRIDGE_ENVELOPE_REQUIRED")
    request, expected = envelope["request"], envelope["expected_peer"]
    require(
        isinstance(request, dict)
        and request.get("command") in {"checkpoint", "prepare", "attempt"},
        "FIXED_COMMAND",
    )
    require(
        request["command"] == "checkpoint" or expected is not None,
        "MUTATION_REQUIRES_PREVIOUSLY_VERIFIED_KERNEL_PEER",
    )
    if expected is not None:
        require(
            isinstance(expected, dict)
            and set(expected)
            == {
                "server_pid",
                "server_uid",
                "boot_id",
                "process_start_ticks",
                "socket_device",
                "socket_inode",
            }
            and type(expected["server_uid"]) is int
            and expected["server_uid"] == SERVICE_UID
            and isinstance(expected["boot_id"], str)
            and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", expected["boot_id"])
            is not None
            and all(
                type(expected[key]) is int and expected[key] > 0
                for key in ["server_pid", "process_start_ticks", "socket_device", "socket_inode"]
            ),
            "COMPLETE_PREVIOUSLY_VERIFIED_KERNEL_PEER_REQUIRED",
        )
    for path in (SOCKET_PATH, *SOCKET_PATH.parents):
        info = path.lstat()
        require(not stat.S_ISLNK(info.st_mode), "SOCKET_PATH_ALIAS")
        if path == SOCKET_PATH:
            require(
                stat.S_ISSOCK(info.st_mode)
                and info.st_uid == SERVICE_UID
                and stat.S_IMODE(info.st_mode) == 0o600,
                "QUALIFIED_SERVICE_SOCKET_REQUIRED",
            )
        else:
            require(
                stat.S_ISDIR(info.st_mode)
                and info.st_uid in {0, SERVICE_UID}
                and info.st_mode & 0o022 == 0,
                "OWNER_SOCKET_ANCESTOR_CHANGED",
            )
    parent = SOCKET_PATH.parent.stat()
    require(
        parent.st_uid == SERVICE_UID and stat.S_IMODE(parent.st_mode) == 0o700,
        "PRIVATE_SERVICE_SOCKET_DIRECTORY_REQUIRED",
    )
    identity = SOCKET_PATH.lstat()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        deadline = time.monotonic() + 28
        connection.settimeout(28)
        connection.connect(str(SOCKET_PATH))
        pid, uid, _gid = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        current = SOCKET_PATH.lstat()
        require(
            pid > 0
            and uid == SERVICE_UID
            and (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino),
            "EXACT_SERVICE_PEER_REQUIRED",
        )
        generation = process_identity(pid)
        peer = {
            **generation,
            "server_pid": pid,
            "server_uid": uid,
            "socket_device": identity.st_dev,
            "socket_inode": identity.st_ino,
        }
        # Authenticate the connected generation before transmitting even a
        # prepare request. In particular, a replaced socket must never receive
        # the one-use mutation nonce from an earlier verified process.
        require(expected is None or expected == peer, "OWNER_SERVER_OR_SOCKET_CHANGED")
        connection.sendall(canonical(request) + b"\n")
        result = bytearray()
        while not result.endswith(b"\n"):
            remaining = deadline - time.monotonic()
            require(remaining > 0, "OWNER_RESPONSE_DEADLINE")
            connection.settimeout(remaining)
            part = connection.recv(min(65536, FRAME_BOUND + 1 - len(result)))
            require(bool(part), "INCOMPLETE_OWNER_RESPONSE")
            result.extend(part)
            require(len(result) <= FRAME_BOUND, "OWNER_RESPONSE_BOUND")
        require(result.count(b"\n") == 1, "ONE_OWNER_RESPONSE_REQUIRED")
        response = decode(bytes(result))
        current = SOCKET_PATH.lstat()
        require(
            generation == process_identity(pid)
            and (identity.st_dev, identity.st_ino) == (current.st_dev, current.st_ino)
            and current.st_uid == SERVICE_UID
            and stat.S_IMODE(current.st_mode) == 0o600,
            "OWNER_PEER_CHANGED_DURING_RESPONSE",
        )
        return canonical({"transport": peer, "response": response}) + b"\n"


def restore(checkpoint: dict) -> dict:
    # Import after command selection: the bridge never creates application state.
    from robot_api.main import Settings, State

    rows = terminal_history(checkpoint["history"])
    require(digest(rows) == checkpoint["history_sha256"], "RESTORE_HISTORY_BINDING")
    with tempfile.TemporaryDirectory(prefix="robot-idle-restore-") as directory:
        config = Settings(
            operator_password_hash="scrypt$" + "0" * 32 + "$" + "0" * 128,
            database_path=Path(directory) / "history.sqlite3",
            public_origin="http://localhost:3431",
            deployment_environment="local",
            storage_mode="persistent",
            turso_database_url="",
            turso_auth_token="",
            identity_enabled=False,
            actuation_enabled=False,
        )
        seeded = State(config)
        try:
            seeded.db.executemany(
                f"INSERT INTO tasks({','.join(COLUMNS)}) VALUES(?,?,?,?,?,?,?)",
                [tuple(row[key] for key in COLUMNS) for row in rows],
            )
            seeded.db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?)",
                ("disposable-session", "disposable-csrf", 1, "disposable-owner"),
            )
            seeded.db.commit()
        finally:
            seeded.db.close()
        restored = State(config)
        try:
            actual = terminal_history(
                [dict(row) for row in restored.db.execute(f"SELECT {','.join(COLUMNS)} FROM tasks")]
            )
            require(canonical(actual) == canonical(rows), "COMPLETE_RESTORED_HISTORY_REQUIRED")
            require(
                restored.journal is None
                and restored.stopped
                and restored.active is None
                and restored.humanoid.active is None
                and restored.humanoid.pending is None
                and restored.physical_permission_owner is None
                and not restored.settings.actuation_enabled
                and restored.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0,
                "STOPPED_AUTHORITY_FREE_RESTORE_REQUIRED",
            )
        finally:
            restored.db.close()
    return {
        "scope": "disposable-complete-terminal-history-restore",
        "history_sha256": digest(rows),
        "rows": len(rows),
        "sessions_revoked": True,
        "stopped_idle": True,
        "physical_actuation": False,
        "production_writes": 0,
        "release_acceptance": False,
    }


def main() -> None:
    try:
        require(len(sys.argv) == 2 and sys.argv[1] in {"bridge", "restore"}, "FIXED_COMMAND")
        limit = MAX_REQUEST_BYTES if sys.argv[1] == "bridge" else FRAME_BOUND
        raw = sys.stdin.buffer.read(limit + 1)
        require(0 < len(raw) <= limit, "PRIVATE_INPUT_BOUND")
        if sys.argv[1] == "bridge":
            sys.stdout.buffer.write(bridge(raw))
        else:
            sys.stdout.buffer.write(canonical(restore(decode(raw))) + b"\n")
    except Exception:
        # Never print private task contents, paths, tokens or arbitrary exceptions.
        sys.stderr.write("OWNER_RECOVERY_PROOF_OR_TRANSPORT_REFUSED\n")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
