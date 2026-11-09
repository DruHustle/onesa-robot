"""New owner-TTY collection of existing Robot test credentials, never authorization.

The root-only temporary file is on /run, expires, and contains no changed account,
password or device identity. This command is installed/pinned separately from Git.
Never send its inputs in chat, Actions, arguments or an echoed SSH stdin stream.
"""

import argparse
import fcntl
import getpass
import hashlib
import hmac
import json
import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import oracle_robot as r


def matches_password(password, encoded):
    if not isinstance(password, str) or not 1 <= len(password) <= 256:
        return False
    if not re.fullmatch(r"scrypt\$[a-f0-9]{32}\$[a-f0-9]{128}", encoded):
        return False
    _, salt, expected = encoded.split("$")
    actual = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64
    )
    return hmac.compare_digest(actual.hex(), expected)


@contextmanager
def custody_lock():
    parent = r.CUSTODY.parent
    if not parent.exists():
        parent.mkdir(mode=0o700)
    r.private(parent, directory=True)
    lock = parent / "robot-operator.lock"
    with os.fdopen(
        os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+"
    ) as stream:
        r.private(lock)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise r.Refused("CONCURRENT_CREDENTIAL_COLLECTION_REFUSED") from None
        yield


def collect(plan, candidate, prompt=getpass.getpass):
    with custody_lock():
        return _collect(plan, candidate, prompt)


def clear_expired(candidate):
    with custody_lock():
        value = r.read(r.CUSTODY)
        r.require(
            value["mechanism"] == "owner-tty-existing-credential-hash-confirmation"
            and value["release_authority"] is False
            and value["candidate_commit"] == candidate
            and r.dt.datetime.fromisoformat(value["expires_at"]) <= r.now(),
            "ONLY_THIS_EXPIRED_TEMPORARY_CUSTODY_CAN_BE_CLEARED",
        )
        r.CUSTODY.unlink()
        r.sync_directory(r.CUSTODY.parent)
    return {
        "state": "EXPIRED_TEMPORARY_CUSTODY_CLEARED",
        "accounts_or_credentials_changed": False,
    }


def _collect(plan, candidate, prompt):
    component = plan.get(
        "release_component",
        "identity-api" if plan.get("identity") is not None else "api",
    )
    r.require(component in r.COMPONENTS, "EXPLICIT_EXISTING_COMPONENT_MODE")
    binding = plan["bindings"][component]
    r.require(
        binding["candidate_commit"] == candidate,
        "CREDENTIAL_SOURCE_SCOPE",
    )
    r.verify_toolchain(plan, binding)
    r.require(
        not r.CUSTODY.exists() and not r.CUSTODY.is_symlink(),
        "EXISTING_CUSTODY_PRESERVED_INSPECT_EXPIRY_FIRST",
    )
    credentials = {}
    for env in ["staging", "production"]:
        values = r.runtime_values(r.ENV[env])
        r.require(
            values.get("ROBOT_ACTUATION_ENABLED") == "false",
            "PHYSICAL_ACTUATION_MUST_REMAIN_DISABLED",
        )
        r.require(
            values.get("ROBOT_STORAGE_MODE") == "turso",
            "EXISTING_DURABLE_JOURNAL_REQUIRED",
        )
        name, encoded = (
            values["ROBOT_OPERATOR_NAME"],
            values["ROBOT_OPERATOR_PASSWORD_HASH"],
        )
        password = prompt(f"Existing Robot {env} operator password (hidden): ")
        r.require(
            matches_password(password, encoded),
            "EXISTING_OPERATOR_PASSWORD_DID_NOT_MATCH_NO_ACCOUNT_CHANGE",
        )
        credentials[env] = {
            "operator": name,
            "password": password,
            "runtime_sha256": r.sha(r.ENV[env]),
            "password_hash_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
        }
        if env == "staging" and values.get("ROBOT_EDGE_DEVICE_ID"):
            edge = prompt("Existing staging synthetic-edge bearer (hidden): ")
            r.require(
                1 <= len(edge) <= 8192
                and hmac.compare_digest(
                    hashlib.sha256(edge.encode()).hexdigest(),
                    values["ROBOT_EDGE_TOKEN_SHA256"],
                ),
                "EXISTING_STAGING_EDGE_CREDENTIAL_DID_NOT_MATCH",
            )
            credentials[env]["edge_token"] = edge
    parent = r.CUSTODY.parent
    if not parent.exists():
        parent.mkdir(mode=0o700)
    r.private(parent, directory=True)
    created = r.now()
    r.write(
        r.CUSTODY,
        {
            "schema_version": 1,
            "candidate_commit": candidate,
            "created_at": created.isoformat(),
            "expires_at": (created + r.dt.timedelta(hours=1)).isoformat(),
            "mechanism": "owner-tty-existing-credential-hash-confirmation",
            "release_authority": False,
            "credentials": credentials,
        },
    )
    return {
        "state": "ROBOT_OPERATOR_CUSTODY_READY_NOT_AUTHORIZATION",
        "path": str(r.CUSTODY),
        "candidate_commit": candidate,
        "expires_at": (created + r.dt.timedelta(hours=1)).isoformat(),
        "accounts_or_credentials_changed": False,
        "release_authority": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument(
        "--canonical-source",
        action="store_true",
        help="Use the fixed separately reviewed canonical Robot plan",
    )
    parser.add_argument("--inspect", action="store_true")
    parser.add_argument("--clear-expired", action="store_true")
    args = parser.parse_args()
    r.require(
        os.geteuid() == 0
        and os.getenv("SUDO_USER") == "ubuntu"
        and not os.getenv("GITHUB_ACTIONS"),
        "EXISTING_OWNER_SSH_SUDO_REQUIRED",
    )
    r.require(re.fullmatch("[a-f0-9]{40}", args.candidate), "EXACT_CANDIDATE_REQUIRED")
    r.require(not (args.inspect and args.clear_expired), "ONE_CUSTODY_ACTION_REQUIRED")
    if args.clear_expired:
        print(json.dumps(clear_expired(args.candidate)))
        return
    if args.inspect:
        value = r.read(r.CUSTODY)
        r.require(
            value["candidate_commit"] == args.candidate, "CREDENTIAL_SOURCE_SCOPE"
        )
        print(
            json.dumps(
                {
                    key: value[key]
                    for key in [
                        "candidate_commit",
                        "created_at",
                        "expires_at",
                        "release_authority",
                    ]
                }
            )
        )
        return
    r.require(sys.stdin.isatty(), "OWNER_HIDDEN_TTY_REQUIRED_NO_STDIN_SECRET_FALLBACK")
    print(
        json.dumps(
            collect(
                r.read(
                    r.PLAN.with_name("oracle-robot-canonical-release.json")
                    if args.canonical_source
                    else r.PLAN
                ),
                args.candidate,
            )
        )
    )


if __name__ == "__main__":
    try:
        main()
    except r.Refused as error:
        raise SystemExit("Robot credential prerequisite: " + str(error)) from None
