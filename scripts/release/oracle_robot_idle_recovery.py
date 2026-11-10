"""One owner-local attempt for an already qualified, stopped idle runtime.

This is not a deployment, restart, forced lease release or journal repair. It
requires the supported process checkpoint, the exact installed source and image,
the original unrevoked signed deployment chain and an actual complete-history
restore in that image. No legacy-process fallback or caller PASS is accepted.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path[:0] = [
    str(Path(__file__).resolve().parent.parent),
    str(Path(__file__).resolve().parent),
]
import oracle_owner as owner
import oracle_robot as robot
import oracle_robot_journal_diagnostic as diagnostic
import oracle_robot_recovery as recovery

BOUND = 16 * 1024 * 1024 + 8192
MODULE = "robot_api.owner_recovery_tools"
COLUMNS = ("id", "owner", "proposal", "state", "reason", "created", "position")
HISTORY_SQL = (
    "SELECT id,owner,proposal,state,reason,"
    "CASE WHEN typeof(created)='real' THEN printf('%!.26g',created) "
    "WHEN typeof(created)='integer' THEN created ELSE NULL END AS created,"
    "CASE WHEN typeof(position)='real' THEN printf('%!.26g',position) "
    "WHEN typeof(position)='integer' THEN position ELSE NULL END AS position "
    "FROM robot_simulation_tasks_v1 ORDER BY id"
)


def digest(value):
    return hashlib.sha256(robot.canonical(value)).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        robot.require(key not in result, "DUPLICATE_PRIVATE_EVIDENCE_FIELD")
        result[key] = value
    return result


def decoded(raw):
    value = json.loads(raw, object_pairs_hook=unique_object)
    robot.require(isinstance(value, dict), "PRIVATE_EVIDENCE_OBJECT_REQUIRED")
    robot.canonical(value)
    return value


def complete_history(rows, *, wire=False):
    robot.require(
        isinstance(rows, list) and len(rows) <= 10000, "COMPLETE_HISTORY_BOUND"
    )
    result, seen = [], set()
    for item in rows:
        robot.require(
            isinstance(item, dict) and set(item) == set(COLUMNS), "EXACT_SEVEN_FIELDS"
        )
        row = dict(item)
        if wire:
            for key in COLUMNS[5:]:
                if isinstance(row[key], str):
                    # The allowlisted SQL emits REAL values as round-trip decimal
                    # text. Invalid/null/nonfinite values are refused, not repaired.
                    robot.require(
                        re.fullmatch(
                            r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", row[key]
                        ),
                        "INVALID_HISTORY_DECIMAL",
                    )
                    row[key] = float(row[key])
        robot.require(
            all(isinstance(row[key], str) for key in COLUMNS[:5])
            and row["id"]
            and row["id"] not in seen
            and row["state"] in {"completed", "cancelled"}
            and len(row["proposal"].encode()) <= 16384
            and all(
                type(row[key]) in (int, float) and math.isfinite(row[key])
                for key in COLUMNS[5:]
            ),
            "INVALID_OR_DUPLICATE_TERMINAL_HISTORY",
        )
        seen.add(row["id"])
        result.append(row)
    result.sort(key=lambda row: row["id"])
    robot.require(
        len(robot.canonical(result)) <= BOUND - 8192, "COMPLETE_HISTORY_BYTE_BOUND"
    )
    return result


def invoke(arguments, payload, *, timeout):
    data = robot.canonical(payload)
    robot.require(len(data) <= BOUND, "PRIVATE_INPUT_BOUND")
    result = subprocess.run(
        ["/usr/bin/docker", *arguments],
        input=data,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    robot.require(
        result.returncode == 0
        and 0 < len(result.stdout) <= BOUND
        and not result.stderr,
        "QUALIFIED_OWNER_TRANSPORT_UNAVAILABLE_NO_RETRY",
    )
    return decoded(result.stdout)


def kernel_process(pid):
    robot.require(type(pid) is int and pid > 0, "EXACT_KERNEL_PROCESS_REQUIRED")
    raw = Path(f"/proc/{pid}/stat").read_bytes()
    status = Path(f"/proc/{pid}/status").read_text()
    robot.require(len(raw) <= 8192 and len(status) <= 65536, "KERNEL_PROCESS_BOUND")
    fields = raw.rsplit(b") ", 1)[1].split()
    ids = re.findall(r"(?m)^NSpid:\s+([0-9\s]+)$", status)
    users = re.findall(r"(?m)^Uid:\s+([0-9\s]+)$", status)
    robot.require(
        len(fields) >= 20 and fields[19].isdigit() and len(ids) == len(users) == 1,
        "KERNEL_PROCESS_GENERATION_REQUIRED",
    )
    return {
        "start_ticks": int(fields[19]),
        "namespace_pids": [int(x) for x in ids[0].split()],
        "uids": [int(x) for x in users[0].split()],
        "pid_namespace": Path(f"/proc/{pid}/ns/pid").stat().st_ino,
    }


def server_process(runtime, peer):
    # Docker's host PID list and the host procfs are independent of the private
    # checkpoint. init:true is supported; the application need not be PID 1.
    initial = kernel_process(runtime["host_process_id"])
    robot.require(
        initial["start_ticks"] == runtime["init_start_ticks"]
        and initial["pid_namespace"] == runtime["pid_namespace"],
        "CONTAINER_INIT_CHANGED",
    )
    raw = robot.docker("top", runtime["container_id"], "-eo", "pid", timeout=5)
    robot.require(len(raw) <= 8192, "CONTAINER_PROCESS_LIST_BOUND")
    rows = raw.splitlines()
    robot.require(
        1 < len(rows) <= 129
        and rows[0].strip() == "PID"
        and all(row.strip().isdigit() for row in rows[1:]),
        "EXACT_CONTAINER_PROCESSES_REQUIRED",
    )
    pids = [int(row.strip()) for row in rows[1:]]
    robot.require(
        len(set(pids)) == len(pids) and runtime["host_process_id"] in pids,
        "CONTAINER_PROCESS_LIST_CHANGED",
    )
    found = []
    for pid in pids:
        identity = kernel_process(pid)
        if identity["namespace_pids"][-1:] == [peer["server_pid"]]:
            robot.require(
                identity["namespace_pids"][0] == pid
                and identity["uids"] == [10001] * 4
                and identity["pid_namespace"] == runtime["pid_namespace"]
                and identity["start_ticks"] == peer["process_start_ticks"],
                "KERNEL_SERVER_IDENTITY_CHANGED",
            )
            found.append(pid)
    robot.require(len(found) == 1, "ONE_EXACT_KERNEL_SERVER_REQUIRED")
    return found[0]


def channel(runtime, request, *, expected_peer=None):
    robot.require(
        re.fullmatch(r"[a-f0-9]{64}", runtime["container_id"]), "EXACT_CONTAINER"
    )
    robot.require(
        isinstance(request, dict)
        and request.get("command") in {"checkpoint", "prepare", "attempt"}
        and (request["command"] == "checkpoint" or expected_peer is not None),
        "MUTATION_REQUIRES_PREVIOUSLY_VERIFIED_KERNEL_PEER",
    )
    transport = None
    if expected_peer is not None:
        robot.require(
            isinstance(expected_peer, dict)
            and set(expected_peer)
            == {
                "server_pid",
                "server_uid",
                "boot_id",
                "process_start_ticks",
                "socket_device",
                "socket_inode",
                "host_server_pid",
            },
            "COMPLETE_PREVIOUSLY_VERIFIED_KERNEL_PEER_REQUIRED",
        )
        transport = {
            key: value
            for key, value in expected_peer.items()
            if key != "host_server_pid"
        }
    envelope = {"request": request, "expected_peer": transport}
    robot.require(len(robot.canonical(envelope)) <= 8192, "OWNER_REQUEST_BOUND")
    result = invoke(
        [
            "exec",
            "--user",
            "0",
            "-i",
            runtime["container_id"],
            "python",
            "-m",
            MODULE,
            "bridge",
        ],
        envelope,
        timeout=35,
    )
    robot.require(
        set(result) == {"transport", "response"}, "KERNEL_PEER_PROOF_REQUIRED"
    )
    peer = result["transport"]
    robot.require(
        isinstance(peer, dict)
        and set(peer)
        == {
            "server_pid",
            "server_uid",
            "boot_id",
            "process_start_ticks",
            "socket_device",
            "socket_inode",
        }
        and type(peer["server_pid"]) is int
        and peer["server_pid"] > 0
        and type(peer["server_uid"]) is int
        and peer["server_uid"] == 10001
        and peer["boot_id"] == runtime["boot_id"]
        and type(peer["process_start_ticks"]) is int
        and peer["process_start_ticks"] > 0
        and all(
            type(peer[key]) is int and peer[key] > 0
            for key in ["socket_device", "socket_inode"]
        ),
        "EXACT_INSTALLED_KERNEL_PEER_REQUIRED",
    )
    peer = {**peer, "host_server_pid": server_process(runtime, peer)}
    robot.require(
        expected_peer is None or peer == expected_peer, "OWNER_SERVER_OR_SOCKET_CHANGED"
    )
    robot.require(
        isinstance(result["response"], dict)
        and result["response"].get("state") != "refused",
        "SUPPORTED_ELIGIBLE_PROCESS_PROOF_REQUIRED",
    )
    return result["response"], peer


def isolated_restore(runtime, checkpoint):
    result = invoke(
        [
            "run",
            "--rm",
            "--pull",
            "never",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,mode=1777,size=64m",
            "--memory",
            "256m",
            "--cpus",
            "0.25",
            "--pids-limit",
            "64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "10001:10001",
            "--entrypoint",
            "python",
            runtime["image"],
            "-m",
            MODULE,
            "restore",
        ],
        checkpoint,
        timeout=45,
    )
    expected = {
        "scope": "disposable-complete-terminal-history-restore",
        "history_sha256": checkpoint["history_sha256"],
        "rows": len(checkpoint["history"]),
        "sessions_revoked": True,
        "stopped_idle": True,
        "physical_actuation": False,
        "production_writes": 0,
        "release_acceptance": False,
    }
    robot.require(
        robot.canonical(result) == robot.canonical(expected),
        "EXACT_IMAGE_RESTORE_REQUIRED",
    )
    return result


def checkpoint(
    runtime, installation_pin, *, environment="production", expected_peer=None
):
    result, peer = channel(
        runtime, {"command": "checkpoint"}, expected_peer=expected_peer
    )
    robot.require(
        set(result)
        == {"scope", "release_acceptance", "checkpoint", "checkpoint_sha256"}
        and result["scope"] == "private-runtime-checkpoint"
        and result["release_acceptance"] is False,
        "SUPPORTED_PRIVATE_CHECKPOINT_REQUIRED",
    )
    value = result["checkpoint"]
    fields = {
        "schema_version",
        "binding",
        "process_generation",
        "process_id",
        "failure",
        "attempts",
        "next_attempt_at",
        "stop_epoch",
        "journal_version",
        "authority_sha256",
        "history_sha256",
        "history",
        "stopped",
        "active",
        "pending",
        "dirty",
        "physical_permission",
        "physical_actuation",
    }
    robot.require(
        isinstance(value, dict)
        and set(value) == fields
        and type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and type(value["process_id"]) is int
        and value["process_id"] == peer["server_pid"]
        and type(value["attempts"]) is int
        and 0 <= value["attempts"] < 3
        and all(
            type(value[key]) is int and value[key] >= 0
            for key in ["stop_epoch", "journal_version"]
        )
        and type(value["next_attempt_at"]) in (int, float)
        and math.isfinite(value["next_attempt_at"])
        and all(
            re.fullmatch(r"[a-f0-9]{64}", value[key])
            for key in ["authority_sha256", "history_sha256"]
        )
        and re.fullmatch(r"[a-f0-9]{64}", value["process_generation"])
        and value["stopped"] is True
        and all(
            value[key] is False
            for key in [
                "active",
                "pending",
                "dirty",
                "physical_permission",
                "physical_actuation",
            ]
        )
        and value["binding"]
        == {
            "installation_sha256": installation_pin,
            "source_commit": runtime["source"],
            "image_digest": runtime["image"],
            "configuration_sha256": runtime["environment_sha256"],
            "environment": environment,
        }
        and result["checkpoint_sha256"] == digest(value),
        "CURRENT_EXACT_PROCESS_CHECKPOINT_REQUIRED",
    )
    failure = value["failure"]
    robot.require(
        isinstance(failure, dict)
        and set(failure)
        == {
            "category",
            "phase",
            "elapsed_ms",
            "http_status",
            "transport_kind",
            "reconciliation",
        }
        and failure["phase"] == "lease_renew"
        and failure["category"] in {"timeout", "transport"}
        and type(failure["elapsed_ms"]) is int
        and 0 <= failure["elapsed_ms"] <= 120000
        and type(failure["http_status"]) is int
        and (failure["http_status"] == 0 or 100 <= failure["http_status"] <= 599)
        and failure["transport_kind"] in diagnostic.TRANSPORTS
        and failure["reconciliation"] in diagnostic.RECONCILIATIONS,
        "ELIGIBLE_ORIGINAL_LATCH_REQUIRED",
    )
    history = complete_history(value["history"])
    robot.require(
        value["history"] == history and value["history_sha256"] == digest(history),
        "EXACT_CHECKPOINT_HISTORY_REQUIRED",
    )
    return {**result, "transport": peer}


def remote_matches(value):
    observed = complete_history(robot.journal("production", HISTORY_SQL), wire=True)
    robot.require(
        robot.canonical(observed) == robot.canonical(value["history"]),
        "COMPLETE_REMOTE_AND_PROCESS_HISTORY_DIFFER",
    )
    return digest(observed)


def observed_runtime():
    value = diagnostic.runtime("production")
    rows = json.loads(robot.docker("inspect", value["container_id"], timeout=5))
    robot.require(
        len(rows) == 1 and robot.summary(rows[0]) == value,
        "RUNTIME_CHANGED_DURING_CONFIGURATION_CHECK",
    )
    entries = rows[0]["Config"]["Env"]
    actual = dict(entry.split("=", 1) for entry in entries)
    expected = robot.runtime_values(robot.ENV["production"])
    robot.require(
        len(actual) == len(entries)
        and all(actual.get(key) == item for key, item in expected.items())
        and all(
            key in expected for key in actual if key.startswith(("ROBOT_", "ONESA_"))
        ),
        "COMPLETE_INSTALLED_CONFIGURATION_REQUIRED",
    )
    pid = rows[0]["State"]["Pid"]
    robot.require(
        type(pid) is int
        and pid > 0
        and rows[0]["HostConfig"]["PidMode"] == ""
        and rows[0]["HostConfig"]["ReadonlyRootfs"] is True
        and rows[0]["HostConfig"]["Privileged"] is False
        and rows[0]["Config"]["User"] in {"onesa", "10001", "10001:10001"},
        "QUALIFIED_PRIVATE_PROCESS_NAMESPACE_REQUIRED",
    )
    image_rows = json.loads(robot.docker("image", "inspect", value["image"], timeout=5))
    robot.require(
        len(image_rows) == 1 and image_rows[0]["Id"] == value["image"],
        "EXACT_LOCAL_IMAGE_REQUIRED",
    )
    original = image_rows[0]["Config"]
    approved_environment = dict(item.split("=", 1) for item in original["Env"])
    approved_environment.update(expected)
    approved_environment["PORT"] = "8431"
    robot.require(
        actual == approved_environment
        and all(
            rows[0]["Config"].get(key) == original.get(key)
            for key in ["Cmd", "Entrypoint", "WorkingDir", "User"]
        )
        and not any(
            key in actual
            for key in ["PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "LD_PRELOAD"]
        )
        and all(
            item["Destination"] in {"/app/storage", "/tmp", "/run/onesa-owner"}
            for item in rows[0]["Mounts"]
        )
        and set(rows[0]["HostConfig"].get("Tmpfs") or {})
        <= {"/tmp", "/run/onesa-owner"},
        "IMMUTABLE_APPLICATION_IMAGE_AND_APPROVED_CONFIGURATION_REQUIRED",
    )
    process = kernel_process(pid)
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    robot.require(
        re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", boot),
        "EXACT_KERNEL_BOOT_REQUIRED",
    )
    return {
        **value,
        "host_process_id": pid,
        "boot_id": boot,
        "init_start_ticks": process["start_ticks"],
        "pid_namespace": process["pid_namespace"],
    }


def retained_checkpoint(store, before):
    path = store.root / (
        "idle-journal-checkpoint-" + before["checkpoint_sha256"] + ".json"
    )
    if not path.exists() and not path.is_symlink():
        robot.write(path, before)
    owner.private(path, uid=store.uid)
    robot.require(
        path.stat().st_size <= BOUND and path.read_bytes() == robot.canonical(before),
        "EXACT_PRIVATE_RETAINED_CHECKPOINT_REQUIRED",
    )
    return {"file": path.name, "sha256": robot.sha(path)}


def validate_installation(store, operation, root, installation_pin):
    installed = diagnostic.installed(root, installation_pin)
    intent, receipt = recovery.original(store, operation)
    binding = intent["binding"]
    runtime = observed_runtime()
    robot.require(
        installed["source"] == binding["candidate_commit"] == runtime["source"]
        and installed["source_tree"] == binding["source_tree"]
        and runtime["image"] == binding["artifact_digest"]
        and receipt["details"]["observations"]["deployment_id"]
        == runtime["container_id"]
        and robot.sha(robot.private(robot.ENV["production"]))
        == binding["configuration_sha256"]["production"]
        and store.policy["toolchain"]["source_commit"] == installed["source"]
        and store.policy["toolchain"]["source_tree"] == installed["source_tree"]
        and Path(store.trust["runner_root"]) == root,
        "QUALIFIED_INSTALLED_RUNTIME_AND_CUSTODY_REQUIRED",
    )
    # A valid old record cannot qualify modified or unreviewed installed code.
    robot.require(store.qualify_source(binding), "EXACT_SOURCE_QUALIFICATION_REQUIRED")
    return binding, receipt, runtime


def current_restore(store, identity, binding, expected):
    envelope = recovery.signed_unrevoked(
        store, identity, "idle-journal-restore", binding
    )
    record = envelope["record"]
    issued, expiry = (
        dt.datetime.fromisoformat(record[key]) for key in ("issued_at", "expires_at")
    )
    robot.require(
        issued <= store.clock() < expiry
        and 0 < (expiry - issued).total_seconds() <= 120
        and robot.canonical(record["details"]) == robot.canonical(expected),
        "FRESH_SIGNED_EXACT_RESTORE_REQUIRED",
    )
    return {
        "record_id": record["id"],
        "record_sha256": digest(envelope),
        "installation_sha256": expected["installation_sha256"],
        "checkpoint_sha256": expected["checkpoint_sha256"],
        "history_sha256": expected["history_sha256"],
        "process_generation": expected["process_generation"],
        "expires_at": expiry.timestamp(),
    }


def record_attempt(store, restore_id, binding, details):
    # This committed signed record is the durable one-attempt barrier. A lost
    # response, exception or killed owner process never permits automatic retry.
    with store.transaction():
        previous = store.db.execute(
            "SELECT envelope FROM records WHERE kind='idle-journal-attempt' AND scope=? LIMIT 10001",
            (owner.component_scope(binding),),
        ).fetchall()
        robot.require(len(previous) <= 10000, "OWNER_ATTEMPT_HISTORY_BOUND")
        for (raw,) in previous:
            identity = decoded(raw)["record"]["id"]
            old = store.get(identity, "idle-journal-attempt", historical=True)[
                "record"
            ]["details"]
            robot.require(
                old["process_generation"] != details["process_generation"]
                or old["runtime"]["container_id"] != details["runtime"]["container_id"],
                "OWNER_PROCESS_ATTEMPT_ALREADY_RECORDED_NO_RETRY",
            )
        # BEGIN IMMEDIATE serializes this final signed-chain/expiry check with
        # revocation writers. The committed barrier is the authorization point;
        # a prior read outside the transaction cannot authorize this attempt.
        current_restore(store, restore_id, binding, details)
        return store.put(
            "idle-journal-attempt", binding, details, parent=restore_id, ttl=120
        )


def recover(store, operation, root, installation_pin):
    binding, original_receipt, runtime = validate_installation(
        store, operation, root, installation_pin
    )
    before = checkpoint(runtime, installation_pin)
    value = before["checkpoint"]
    remote_matches(value)
    backup = retained_checkpoint(store, before)
    restored = isolated_restore(runtime, value)
    robot.require(observed_runtime() == runtime, "PROCESS_CHANGED_DURING_RESTORE")
    robot.require(
        checkpoint(runtime, installation_pin, expected_peer=before["transport"])
        == before,
        "CHECKPOINT_CHANGED_DURING_RESTORE",
    )
    remote_matches(value)
    details = {
        "scope": "one-existing-eligible-idle-journal-attempt",
        "runtime": runtime,
        "transport": before["transport"],
        "installation_sha256": installation_pin,
        "checkpoint_sha256": before["checkpoint_sha256"],
        "process_generation": value["process_generation"],
        "history_sha256": value["history_sha256"],
        "restore": restored,
        "backup": backup,
        "automatic_retry": False,
        "physical_hardware_authorized": False,
    }
    with store.transaction():
        receipt = store.put(
            "idle-journal-restore",
            binding,
            details,
            parent=original_receipt["id"],
            # Store timestamps issuance and expiry separately. Keep a one-second
            # margin inside the strict 120-second signed receipt lifetime.
            ttl=119,
        )
    identity = current_restore(store, receipt["record"]["id"], binding, details)
    pending, _peer = channel(
        runtime,
        {
            "command": "prepare",
            "checkpoint_sha256": before["checkpoint_sha256"],
            "receipt": identity,
        },
        expected_peer=before["transport"],
    )
    robot.require(
        set(pending)
        == {
            "nonce",
            "process_generation",
            "checkpoint_sha256",
            "receipt_sha256",
            "expires_in_seconds",
            "release_acceptance",
        }
        and re.fullmatch(r"[a-f0-9]{64}", pending["nonce"])
        and pending["process_generation"] == value["process_generation"]
        and pending["checkpoint_sha256"] == before["checkpoint_sha256"]
        and pending["receipt_sha256"] == digest(identity)
        and type(pending["expires_in_seconds"]) in (int, float)
        and math.isfinite(pending["expires_in_seconds"])
        and 0 < pending["expires_in_seconds"] <= 20
        and pending["release_acceptance"] is False,
        "BOUND_ONE_USE_RUNTIME_NONCE_REQUIRED",
    )
    current_restore(store, receipt["record"]["id"], binding, details)
    robot.require(observed_runtime() == runtime, "PROCESS_CHANGED_BEFORE_ATTEMPT")
    robot.require(
        retained_checkpoint(store, before) == backup, "RETAINED_CHECKPOINT_CHANGED"
    )
    attempt = record_attempt(store, receipt["record"]["id"], binding, details)
    result, _peer = channel(
        runtime,
        {
            "command": "attempt",
            "nonce": pending["nonce"],
            "checkpoint_sha256": pending["checkpoint_sha256"],
            "receipt_sha256": pending["receipt_sha256"],
        },
        expected_peer=before["transport"],
    )
    robot.require(
        set(result)
        == {"state", "automatic_retry", "release_acceptance", "physical_actuation"}
        and result["state"] in {"latched", "recovered"}
        and all(
            result[key] is False
            for key in ["automatic_retry", "release_acceptance", "physical_actuation"]
        ),
        "AMBIGUOUS_OWNER_RESULT_NO_RETRY",
    )
    robot.require(observed_runtime() == runtime, "PROCESS_CHANGED_AFTER_ATTEMPT")
    remote_matches(value)
    with store.transaction():
        store.put(
            "idle-journal-result",
            binding,
            {**details, "result": result},
            parent=attempt["record"]["id"],
            ttl=120,
        )
    return {
        **result,
        "attempt_id": attempt["record"]["id"],
        "history_preserved": True,
        "container_replaced": False,
        "commands_started": False,
        "physical_permission_enabled": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--installation-sha256", required=True)
    args = parser.parse_args()
    owner.require(
        os.geteuid() == 0
        and os.getenv("SUDO_USER") == "ubuntu"
        and not os.getenv("GITHUB_ACTIONS"),
        "EXTERNAL_AUTHENTICATED_OWNER_ONLY",
    )
    raw = sys.stdin.buffer.read(16385)
    owner.require(0 < len(raw) <= 16384, "PRIVATE_OWNER_INPUT_BOUND")
    request = decoded(raw)
    owner.require(
        set(request) == {"owner_token"}, "NO_CALLER_PASS_OR_RESTORE_CLAIM_ACCEPTED"
    )
    root = Path(__file__).resolve().parent.parent
    store = owner.Store(args.store, owner.GitHub(request["owner_token"]))
    try:
        print(
            json.dumps(recover(store, args.operation, root, args.installation_sha256))
        )
    finally:
        store.db.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- never disclose private task history or provider errors.
        # The committed attempt, if any, remains consumed. Never print raw history,
        # credentials, URLs, subprocess output or an exception-supplied PASS.
        sys.exit("OWNER_IDLE_RECOVERY_REFUSED_OR_AMBIGUOUS_NO_RETRY")
