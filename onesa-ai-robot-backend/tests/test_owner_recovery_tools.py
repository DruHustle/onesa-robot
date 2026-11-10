"""Disposable restore and private protocol proofs; no production connection."""

import copy
import json
import os
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace

import pytest

from robot_api import owner_recovery as protocol
from robot_api import owner_recovery_tools as tools


def checkpoint():
    rows = [
        {
            "id": "disposable-completed",
            "owner": "disposable-owner",
            "proposal": '{"preserve":"literal bytes — δ"}',
            "state": "completed",
            "reason": "original observation",
            "created": 1.0000000000000002,
            "position": -0.0010000000000000002,
        },
        {
            "id": "disposable-cancelled",
            "owner": "disposable-owner",
            "proposal": "{}",
            "state": "cancelled",
            "reason": "original stop",
            "created": 3.25,
            "position": 0.0,
        },
    ]
    rows = protocol.terminal_history(rows)
    return {"history": rows, "history_sha256": protocol.digest(rows)}


def test_complete_history_restores_through_actual_application_without_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("The disposable restore must not open a socket")

    monkeypatch.setattr(tools.socket, "socket", forbidden)
    before = checkpoint()
    original = copy.deepcopy(before)
    result = tools.restore(before)
    assert result == {
        "scope": "disposable-complete-terminal-history-restore",
        "history_sha256": before["history_sha256"],
        "rows": 2,
        "sessions_revoked": True,
        "stopped_idle": True,
        "physical_actuation": False,
        "production_writes": 0,
        "release_acceptance": False,
    }
    assert before == original


@pytest.mark.parametrize("kind", ["active", "duplicate", "unknown", "nonfinite", "missing", "pin"])
def test_restore_refuses_bad_or_changed_full_history(kind):
    before = checkpoint()
    if kind == "active":
        before["history"][0]["state"] = "running"
    elif kind == "duplicate":
        before["history"].append(dict(before["history"][0]))
    elif kind == "unknown":
        before["history"][0]["unreviewed"] = True
    elif kind == "nonfinite":
        before["history"][0]["position"] = float("inf")
    elif kind == "missing":
        before["history"].pop()
    else:
        before["history_sha256"] = "0" * 64
    with pytest.raises(protocol.RecoveryRefused):
        tools.restore(before)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b"[]"])
def test_private_decode_rejects_duplicate_nonfinite_and_nonobject(raw):
    with pytest.raises(protocol.RecoveryRefused):
        tools.decode(raw)


def test_real_restore_entrypoint_returns_only_digest_and_generic_failure():
    command = [sys.executable, "-m", "robot_api.owner_recovery_tools", "restore"]
    result = subprocess.run(
        command,
        input=protocol.canonical(checkpoint()),
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)["rows"] == 2
    assert b"original observation" not in result.stdout
    assert b"disposable-owner" not in result.stdout
    result = subprocess.run(
        command,
        input=b'{"secret":"must not be echoed"}',
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1 and not result.stdout
    assert result.stderr == b"OWNER_RECOVERY_PROOF_OR_TRANSPORT_REFUSED\n"


def test_bridge_does_not_accept_nonroot_or_a_request_supplied_identity(monkeypatch):
    monkeypatch.setattr(tools.os, "geteuid", lambda: 1000)
    with pytest.raises(protocol.RecoveryRefused, match="KERNEL_ROOT"):
        tools.bridge(b'{"command":"checkpoint","uid":0}')


def test_process_identity_reads_actual_kernel_generation_without_network():
    first = tools.process_identity(os.getpid())
    assert first == tools.process_identity(os.getpid())
    assert first["process_start_ticks"] > 0
    assert len(first["boot_id"]) == 36


def peer():
    return {
        "server_pid": 7,
        "server_uid": tools.SERVICE_UID,
        "boot_id": "11111111-2222-3333-4444-555555555555",
        "process_start_ticks": 12345,
        "socket_device": 12,
        "socket_inode": 3456,
    }


@pytest.fixture
def bridge_socket(monkeypatch):
    class Connection:
        def __init__(self, actual):
            self.actual = actual
            self.sent = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def settimeout(self, value):
            assert 0 < value <= 28

        def connect(self, path):
            assert path == str(tools.SOCKET_PATH)

        def getsockopt(self, level, option, size):
            assert (level, option, size) == (tools.socket.SOL_SOCKET, tools.socket.SO_PEERCRED, 12)
            return struct.pack("3i", self.actual["server_pid"], self.actual["server_uid"], 10001)

        def sendall(self, value):
            self.sent.append(value)

        def recv(self, _size):
            return b'{"state":"latched"}\n'

    def prepare(actual):
        connection = Connection(actual)

        def metadata(path):
            if path == tools.SOCKET_PATH:
                return SimpleNamespace(
                    st_mode=stat.S_IFSOCK | 0o600,
                    st_uid=tools.SERVICE_UID,
                    st_dev=actual["socket_device"],
                    st_ino=actual["socket_inode"],
                )
            if path == tools.SOCKET_PATH.parent:
                return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=tools.SERVICE_UID)
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)

        monkeypatch.setattr(tools.os, "geteuid", lambda: 0)
        monkeypatch.setattr(tools.Path, "lstat", metadata)
        monkeypatch.setattr(tools.Path, "stat", metadata)
        monkeypatch.setattr(tools.socket, "socket", lambda *_args: connection)
        monkeypatch.setattr(
            tools,
            "process_identity",
            lambda _pid: {key: actual[key] for key in ["boot_id", "process_start_ticks"]},
        )
        return connection

    return prepare


@pytest.mark.parametrize("command", ["checkpoint", "prepare", "attempt"])
def test_bridge_authenticates_expected_peer_before_sending_only_runtime_request(
    bridge_socket, command
):
    connection = bridge_socket(peer())
    request = (
        {"command": command, "nonce": "a" * 64} if command == "attempt" else {"command": command}
    )
    result = tools.decode(
        tools.bridge(protocol.canonical({"request": request, "expected_peer": peer()}))
    )
    assert connection.sent == [protocol.canonical(request) + b"\n"]
    assert result == {"transport": peer(), "response": {"state": "latched"}}


@pytest.mark.parametrize(
    "changed",
    ["server_pid", "server_uid", "boot_id", "process_start_ticks", "socket_device", "socket_inode"],
)
def test_replaced_peer_or_socket_receives_zero_bytes_including_no_nonce(bridge_socket, changed):
    actual = peer()
    actual[changed] = (
        "99999999-2222-3333-4444-555555555555" if changed == "boot_id" else actual[changed] + 1
    )
    connection = bridge_socket(actual)
    envelope = {"request": {"command": "attempt", "nonce": "a" * 64}, "expected_peer": peer()}
    match = "EXACT_SERVICE_PEER" if changed == "server_uid" else "OWNER_SERVER_OR_SOCKET_CHANGED"
    with pytest.raises(protocol.RecoveryRefused, match=match):
        tools.bridge(protocol.canonical(envelope))
    assert connection.sent == []


@pytest.mark.parametrize("command", ["prepare", "attempt"])
def test_mutation_without_previous_peer_is_refused_before_opening_a_socket(monkeypatch, command):
    monkeypatch.setattr(tools.os, "geteuid", lambda: 0)

    def forbidden(*_args):
        raise AssertionError("An unbound mutation must not open a socket")

    monkeypatch.setattr(tools.socket, "socket", forbidden)
    with pytest.raises(protocol.RecoveryRefused, match="MUTATION_REQUIRES_PREVIOUSLY_VERIFIED"):
        tools.bridge(protocol.canonical({"request": {"command": command}, "expected_peer": None}))
