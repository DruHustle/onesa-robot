"""Owner-installed Robot Oracle adapter; no repository Actions deployment credentials.

Shared fail-closed primitives retain the reviewed AI owner-run semantics. Robot
profiles, observations and provider operations are independently scoped here.
Installation and commissioning are separate from application release authority.
"""

import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

# Sibling observers must share this module's refusal type when invoked as a CLI.
if __name__ == "__main__":
    sys.modules["oracle_robot"] = sys.modules[__name__]

ROOT = Path("/var/lib/onesa/release-verification/robot")
ANCHOR = Path("/etc/onesa/oracle-release-trust.json")
PLAN = Path("/etc/onesa/oracle-robot-release.json")
COMPOSE = Path("/opt/onesa/backends/robot.yaml")
IMAGES = Path("/etc/onesa/backends/robot.images.env")
ENV = {
    env: Path("/etc/onesa/backends/robot-" + env + ".env")
    for env in ["staging", "production"]
}
TARGET = {env: "onesa-robot-oracle-api-" + env + "-1" for env in ENV}
VERIFIER = Path(__file__).resolve().with_name("oracle_owner.py")
if not VERIFIER.exists():
    VERIFIER = Path(__file__).resolve().parent.parent / "oracle_owner.py"
OPENSSL = "/usr/bin/openssl" if sys.platform == "linux" else "/opt/homebrew/bin/openssl"
CUSTODY = Path("/run/onesa-release/robot-operator.json")
COMPONENTS = {
    "api": "api",
    "frontend": "frontend",
    "identity-api": "api",
    "identity-frontend": "frontend",
}
IDENTITY_COMPONENTS = {"identity-api", "identity-frontend"}


class Refused(ValueError):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def base_component(binding):
    """Select implementation only; never rewrite an authority or artifact binding."""
    component = binding["component"]
    require(component in COMPONENTS, "ROBOT_COMPONENT_SCOPE")
    return COMPONENTS[component]


SOURCE_FIELDS = {
    "schema_version",
    "repository",
    "repository_id",
    "product",
    "source_path",
    "candidate_commit",
    "source_tree",
    "product_source_tree",
    "branch",
}
CANONICAL_REPOSITORY = "DruHustle/onesa-ug"
LEGACY_REPOSITORY = "DruHustle/onesa-robot"


def is_canonical(binding):
    return (
        isinstance(binding, dict) and binding.get("repository") == CANONICAL_REPOSITORY
    )


def source_binding(binding):
    """Source identity only; this does not commission or authorize a release."""
    require(
        isinstance(binding, dict) and SOURCE_FIELDS <= set(binding),
        "COMPLETE_CANONICAL_ROBOT_SOURCE_REQUIRED",
    )
    source = {key: binding[key] for key in SOURCE_FIELDS}
    require(
        type(source["schema_version"]) is int
        and source["schema_version"] == 2
        and source["repository"] == CANONICAL_REPOSITORY
        and type(source["repository_id"]) is int
        and source["repository_id"] == 1444746154
        and source["product"] == "robot"
        and source["source_path"] == "onesa-robot"
        and source["branch"] == "main"
        and all(
            isinstance(source[key], str) and re.fullmatch(r"[a-f0-9]{40}", source[key])
            for key in ("candidate_commit", "source_tree", "product_source_tree")
        ),
        "CANONICAL_ROBOT_APPLICATION_SOURCE_SCOPE",
    )
    return source


def validate_source(binding):
    require(isinstance(binding, dict), "ROBOT_SOURCE_REQUIRED")
    if is_canonical(binding):
        source_binding(binding)
    else:
        require(
            binding.get("repository") == LEGACY_REPOSITORY, "ROBOT_REPOSITORY_SCOPE"
        )
    base_component(binding)
    return binding


def source_identity(binding):
    fields = {"repository", "candidate_commit", "source_tree", "source_sha256"}
    if is_canonical(binding):
        source_binding(binding)
        fields |= SOURCE_FIELDS
    return {key: binding[key] for key in fields}


def component_scope(binding):
    validate_source(binding)
    return (
        CANONICAL_REPOSITORY + ":robot:"
        if is_canonical(binding)
        else LEGACY_REPOSITORY + ":"
    ) + binding["component"]


def source_metadata(binding):
    if not is_canonical(binding):
        return {}
    source = source_binding(binding)
    return {
        "onesaSource" + suffix: str(source[key])
        for suffix, key in [
            ("Schema", "schema_version"),
            ("Repository", "repository"),
            ("RepositoryId", "repository_id"),
            ("Product", "product"),
            ("Path", "source_path"),
            ("RootTree", "source_tree"),
            ("ProductTree", "product_source_tree"),
            ("Branch", "branch"),
        ]
    }


def frontend_root(binding):
    if is_canonical(binding):
        source_binding(binding)
        return "onesa-robot/onesa-ai-robot-frontend"
    return "onesa-ai-robot-frontend"


def plan_path(binding):
    return (
        PLAN.with_name("oracle-robot-canonical-release.json")
        if is_canonical(binding)
        else PLAN
    )


def verify_retained_dependency(consumer_binding, retained, plan, component):
    """Read the original accepted chain without invoking or renewing its issuer."""
    validate_source(consumer_binding)
    require(
        consumer_binding["component"] in {"api", "frontend"},
        "RETAINED_DEPENDENCY_CANNOT_SUPPLY_IDENTITY_BRIDGE",
    )
    binding = retained["binding"]
    validate_source(binding)
    require(
        component in {"api", "frontend"}
        and binding["component"] == component
        and binding["target_environment"] == "production",
        "RETAINED_ROBOT_NATIVE_DEPENDENCY_REQUIRED",
    )
    original = retained.get("verifier_path")
    if is_canonical(consumer_binding):
        require(
            consumer_binding.get("target_environment") == "production",
            "CANONICAL_DEPENDENCY_CONSUMER_TARGET",
        )
        require(isinstance(original, str), "ORIGINAL_RETAINED_VERIFIER_REQUIRED")
    else:
        require(
            original is None and binding["repository"] == LEGACY_REPOSITORY,
            "LEGACY_INVOCATION_PIN_UNCHANGED",
        )
    return verify_invocation(
        binding,
        {"operation_id": retained["operation_id"], "read_only": True},
        "production",
        {"verifier_store": retained.get("verifier_store", plan["verifier_store"])},
        dependency=True,
        **(
            {"retained_verifier": original, "consumer_binding": consumer_binding}
            if original is not None
            else {}
        ),
    )


def plan_binding(plan, component):
    require(component in {"api", "frontend"}, "ROBOT_DEPENDENCY_COMPONENT_SCOPE")
    key = ("identity-" if plan.get("identity") is not None else "") + component
    binding = plan["bindings"][key]
    require(binding["component"] == key, "ROBOT_DEPENDENCY_PROFILE_MISMATCH")
    return binding


def now():
    return dt.datetime.now(dt.timezone.utc)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def private(path, *, uid=0, directory=False):
    path = Path(path)
    require(
        path.is_absolute() and not any(p.is_symlink() for p in [path, *path.parents]),
        "CUSTODY_PATH_ALIAS",
    )
    s = path.stat()
    require(
        s.st_uid == uid
        and s.st_mode & 0o077 == 0
        and (
            stat.S_ISDIR(s.st_mode)
            if directory
            else stat.S_ISREG(s.st_mode) and s.st_nlink == 1
        ),
        "PRIVATE_CUSTODY_REQUIRED",
    )
    require(
        all(
            (p.stat().st_uid == 0 and p.stat().st_mode & 0o022 == 0)
            or (uid != 0 and p.stat().st_uid == uid and p.stat().st_mode & 0o077 == 0)
            for p in path.parents
        ),
        "CUSTODY_ANCESTOR",
    )
    return path


def read(path):
    return json.loads(private(path).read_bytes())


def pinned_public_file(path, expected):
    # Existing code and public recovery material are read-only, not secrets.
    # Their reviewed modes need not be changed to match credential custody.
    path = Path(path)
    require(
        path.is_absolute() and not any(p.is_symlink() for p in [path, *path.parents]),
        "PINNED_PUBLIC_FILE_ALIAS",
    )
    info = path.stat()
    require(
        path.is_file()
        and info.st_uid == 0
        and not info.st_mode & 0o022
        and info.st_nlink == 1
        and all(
            p.stat().st_uid == 0 and not p.stat().st_mode & 0o022 for p in path.parents
        )
        and sha(path) == expected,
        "PINNED_PUBLIC_FILE_CHANGED",
    )
    return path


def write(path, value):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
    ) as f:
        f.write(canonical(value))
        f.flush()
        os.fsync(f.fileno())
    fd = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_raw(path, data):
    with os.fdopen(
        os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), "wb"
    ) as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(Path(path).parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def command(args, *, data=None, timeout=30):
    r = subprocess.run(
        args,
        input=data,
        capture_output=True,
        timeout=timeout,
        check=False,
        env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    require(
        r.returncode == 0 and len(r.stdout) <= 8 * 1024 * 1024,
        "PINNED_COMMAND_FAILED_INSPECT_WITHOUT_RETRY",
    )
    return r.stdout


def docker(*args, **kwargs):
    return command(["/usr/bin/docker", *args], **kwargs)


def inventory():
    """Capture every workload only after temporary Docker clients finish."""
    deadline = time.monotonic() + 20
    while True:
        require(time.monotonic() < deadline, "RUNTIME_INVENTORY_NOT_QUIESCENT")
        names = docker("ps", "--format", "{{.Names}}", timeout=5).decode().splitlines()
        require(all(v in names for v in TARGET.values()), "ROBOT_RUNTIME_MISSING")
        try:
            values = json.loads(docker("inspect", *names, timeout=5))
        except Refused:
            # A --rm client can finish between list and inspect. Only retry this
            # read when a fresh list proves the inventory actually changed.
            current = docker("ps", "--format", "{{.Names}}", timeout=5)
            require(
                set(current.decode().splitlines()) != set(names),
                "PINNED_COMMAND_FAILED_INSPECT_WITHOUT_RETRY",
            )
        else:
            observed = {v["Name"].lstrip("/"): v for v in values}
            require(set(observed) == set(names), "RUNTIME_INVENTORY_INCOMPLETE")
            require(
                all(
                    type(v.get("HostConfig", {}).get("AutoRemove")) is bool
                    for v in values
                ),
                "RUNTIME_LIFECYCLE_METADATA_REQUIRED",
            )
            # Do not omit helpers or classify an unknown workload as safe. Wait
            # for automatic removal; a long-lived --rm workload blocks release.
            if not any(v["HostConfig"]["AutoRemove"] for v in values):
                return observed
        require(time.monotonic() < deadline, "RUNTIME_INVENTORY_NOT_QUIESCENT")
        time.sleep(0.25)


def summary(row):
    return {
        "container_id": row["Id"],
        "image": row["Image"],
        "started_at": row["State"]["StartedAt"],
        "restart_count": row["RestartCount"],
        "source": row["Config"].get("Labels", {}).get("org.onesa.source-commit")
        or row["Config"].get("Labels", {}).get("org.opencontainers.image.revision"),
        "environment_sha256": hashlib.sha256(
            "\n".join(sorted(row["Config"]["Env"])).encode()
        ).hexdigest(),
    }


def backend_runtime(row):
    """Bind acceptance to the exact running configuration without disclosing it."""
    configuration = {
        "Config": row["Config"],
        "HostConfig": row["HostConfig"],
        "Mounts": row["Mounts"],
        "Networks": {
            name: network["NetworkID"]
            for name, network in row["NetworkSettings"]["Networks"].items()
        },
    }
    return {
        "container_id": row["Id"],
        "image_id": row["Image"],
        "source_commit": row["Config"].get("Labels", {}).get("org.onesa.source-commit")
        or row["Config"].get("Labels", {}).get("org.opencontainers.image.revision"),
        "configuration_sha256": hashlib.sha256(canonical(configuration)).hexdigest(),
    }


def healthy(row):
    return (
        row["State"]["Running"]
        and not row["State"]["OOMKilled"]
        and row["State"].get("Health", {}).get("Status") == "healthy"
    )


def unchanged(before, after, except_names=()):
    require(
        all(
            name in after and summary(row) == summary(after[name])
            for name, row in before.items()
            if name not in except_names
        ),
        "CONCURRENT_OR_INDEPENDENT_RUNTIME_CHANGED",
    )


def runtime_values(path):
    rows = private(path).read_text().splitlines()
    require(
        all("=" in r and not r.startswith("#") for r in rows if r), "RUNTIME_FILE_SHAPE"
    )
    values = dict(r.split("=", 1) for r in rows if r)
    require(len(values) == len([r for r in rows if r]), "DUPLICATE_CONFIGURATION_KEY")
    return values


def scanned_configuration_digest(identity, proof, architecture="arm64"):
    # OCI metadata is a content-addressed proof, never caller authorization.
    # The starting identity is independently pinned/observed from Docker.
    require(
        re.fullmatch("sha256:[a-f0-9]{64}", identity) and isinstance(proof, dict),
        "OCI_DESCRIPTOR_PROOF_REQUIRED",
    )
    require(architecture in {"arm64", "amd64"}, "SUPPORTED_SCAN_ARCHITECTURE_REQUIRED")
    current, visited = identity, set()
    for _ in range(5):
        require(current not in visited, "OCI_DESCRIPTOR_CYCLE")
        visited.add(current)
        encoded = proof.get(current)
        require(
            isinstance(encoded, str) and len(encoded) <= 1024 * 1024,
            "OCI_DESCRIPTOR_BOUND",
        )
        raw = base64.b64decode(encoded, validate=True)
        require(
            "sha256:" + hashlib.sha256(raw).hexdigest() == current,
            "OCI_DESCRIPTOR_HASH_MISMATCH",
        )
        item = json.loads(raw)
        require(item.get("schemaVersion") == 2, "OCI_DESCRIPTOR_SCHEMA")
        if "manifests" in item:
            native = [
                row
                for row in item["manifests"]
                if row.get("platform", {}).get("os") == "linux"
                and row.get("platform", {}).get("architecture") == architecture
            ]
            require(len(native) == 1, "SINGLE_EXPECTED_PLATFORM_MANIFEST_REQUIRED")
            current = native[0]["digest"]
            require(re.fullmatch("sha256:[a-f0-9]{64}", current), "OCI_CHILD_DIGEST")
            continue
        require(
            item.get("mediaType")
            in [
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            ],
            "OCI_IMAGE_MANIFEST_REQUIRED",
        )
        result = item.get("config", {}).get("digest", "")
        require(re.fullmatch("sha256:[a-f0-9]{64}", result), "OCI_CONFIGURATION_DIGEST")
        return result
    raise Refused("OCI_DESCRIPTOR_DEPTH")


def image_security(scan, identity, architecture="arm64"):
    configuration = identity
    if scan["Metadata"]["ImageID"] != identity:
        require(
            re.fullmatch("sha256:[a-f0-9]{64}", identity),
            "EXACT_IMAGE_IDENTITY_REQUIRED",
        )
        configuration = scanned_configuration_digest(
            identity,
            read(
                Path("/var/lib/onesa/release-verification/image-descriptors")
                / (identity[7:] + ".json")
            ),
            architecture,
        )
    require(
        scan["Metadata"]["ImageID"] == configuration
        and scan.get("Results")
        and scan.get("ArtifactName")
        and scan.get("CreatedAt"),
        "COMPLETE_CURRENT_IMAGE_SCAN_REQUIRED",
    )
    require(
        dt.timedelta(0)
        <= now() - dt.datetime.fromisoformat(scan["CreatedAt"].replace("Z", "+00:00"))
        < dt.timedelta(hours=24),
        "IMAGE_SCAN_STALE",
    )
    # Same gate as scripts/release/image_gate.py; retain all vendor findings.
    require(
        not any(
            row.get("Secrets")
            or any(
                v.get("Severity") in ["HIGH", "CRITICAL"]
                for v in row.get("Vulnerabilities", [])
            )
            for row in scan["Results"]
        ),
        "IMAGE_SECURITY_FAILED",
    )


def verify_invocation(
    binding,
    context,
    phase,
    plan,
    *,
    dependency=False,
    repair_dependency=None,
    retained_verifier=None,
    consumer_binding=None,
):
    verifier = VERIFIER
    if retained_verifier is not None or consumer_binding is not None:
        require(
            dependency
            and phase == "production"
            and repair_dependency is None
            and is_canonical(consumer_binding)
            and context.get("read_only") is True
            and consumer_binding.get("component") in {"api", "frontend"}
            and consumer_binding.get("target_environment") == "production"
            and binding.get("component") in {"api", "frontend"}
            and binding.get("target_environment") == "production"
            and isinstance(retained_verifier, str),
            "RETAINED_VERIFIER_READ_ONLY_DEPENDENCY_REQUIRED",
        )
        validate_source(consumer_binding)
        validate_source(binding)
        verifier = private(Path(retained_verifier))
        require(
            0 < verifier.stat().st_size <= 2 * 1024 * 1024,
            "RETAINED_VERIFIER_BYTE_BOUND",
        )
    require(
        repair_dependency is None
        or phase == "production"
        and binding["component"] == "api"
        and not is_canonical(binding)
        and not dependency
        and str(UUID(repair_dependency)) == repair_dependency,
        "EXACT_READONLY_REPAIR_DEPENDENCY_SCOPE_REQUIRED",
    )
    strict_ancestors = dependency or repair_dependency is not None
    if phase == "staging":
        require(set(context) == {"context_id"}, "STAGING_SIGNED_CONTEXT_ONLY")
    elif phase == "deploy":
        require(
            set(context) == {"operation_id", "authorization_id"},
            "DEPLOY_SIGNED_AUTHORIZATION_OPERATION_ONLY",
        )
    elif phase == "production":
        require(
            set(context) == {"operation_id", "read_only"}
            and context["read_only"] is True,
            "READ_ONLY_PRODUCTION_OBSERVATION_ONLY",
        )
    elif phase == "operations":
        require(
            set(context) == {"context_id"}
            or set(context) == {"qualification_id", "owner_decision"}
            and context["owner_decision"] == "approve"
            or set(context) == {"authorization_id", "before_deployment"}
            and context["before_deployment"] is True,
            "OPERATIONS_SIGNED_INVOCATION_ONLY",
        )
    else:
        raise Refused("RUNNER_PHASE_SCOPE")
    store = private(Path(plan["verifier_store"]), directory=True)
    for name in ["trust.json", "public.pem", "policy.json", "records.sqlite"]:
        private(store / name)
    trust = read(store / "trust.json")
    registry = read(ANCHOR)
    anchor = registry["installations"].get(str(store))
    require(
        anchor
        and anchor["active"] is True
        and all(
            anchor[k] == trust[k]
            for k in ["installation_id", "key_id", "verifier_sha256", "policy_sha256"]
        ),
        "ISSUER_NOT_COMMISSIONED",
    )
    require(
        sha(store / "public.pem") == trust["key_id"]
        and sha(store / "policy.json") == trust["policy_sha256"]
        and sha(verifier) == trust["verifier_sha256"],
        "ISSUER_CUSTODY_PIN_CHANGED",
    )
    require(sha(OPENSSL) == trust["openssl_sha256"], "SIGNATURE_TOOL_CHANGED")
    db = sqlite3.connect(
        "file:" + str(store / "records.sqlite") + "?mode=ro", uri=True, timeout=20
    )

    records = []
    visited = set()

    def record(identifier, kind=None, historical=False, *, parent_checked=False):
        require(
            identifier not in visited and len(visited) < 12,
            "SIGNED_RECORD_CHAIN_CYCLE_OR_BOUND",
        )
        visited.add(identifier)
        require(str(UUID(identifier)) == identifier, "CANONICAL_LEDGER_RECORD_REQUIRED")
        row = db.execute(
            "SELECT envelope,revoked,parent,kind FROM records WHERE id=?", (identifier,)
        ).fetchone()
        require(
            row and ((historical and not strict_ancestors) or not row[1]),
            "SIGNED_LEDGER_RECORD_MISSING_OR_REVOKED",
        )
        envelope = json.loads(row[0])
        r = envelope["record"]
        sig = envelope["signature"]
        require(
            sig["algorithm"] == "Ed25519" and sig["key_id"] == trust["key_id"],
            "FOREIGN_RECORD_SIGNER",
        )
        with tempfile.TemporaryDirectory(
            prefix=".runner-signature-",
            dir=None if retained_verifier is not None else store,
        ) as folder:
            p = Path(folder)
            (p / "record").write_bytes(canonical(r))
            (p / "signature").write_bytes(base64.b64decode(sig["value"], validate=True))
            command(
                [
                    OPENSSL,
                    "pkeyutl",
                    "-verify",
                    "-rawin",
                    "-pubin",
                    "-inkey",
                    str(store / "public.pem"),
                    "-sigfile",
                    str(p / "signature"),
                    "-in",
                    str(p / "record"),
                ]
            )
        require(
            r["id"] == identifier
            and r["kind"] == row[3]
            and r["parent"] == row[2]
            and (kind is None or r["kind"] == kind)
            and r["binding"] == binding
            and type(r["workflow_run_id"]) is int
            and r["workflow_run_id"] == 0,
            "SIGNED_RECORD_SOURCE_OR_PROVENANCE_MISMATCH",
        )
        provenance = r["owner_provenance"]
        require(
            provenance["owner"] == "DruHustle"
            and provenance["mechanism"]
            == "commissioned-pinned-OpenSSH-and-authenticated-owner"
            and provenance["installation_id"] == trust["installation_id"]
            and provenance["verifier_sha256"] == trust["verifier_sha256"],
            "OWNER_PROVENANCE_REQUIRED",
        )
        issued, expiry = (
            dt.datetime.fromisoformat(r["issued_at"]),
            dt.datetime.fromisoformat(r["expires_at"]),
        )
        require(
            type(r.get("schema_version")) is int
            and r.get("schema_version") == 1
            and r.get("execution_context")
            == (
                "owner-operated-oracle-staging"
                if r["kind"] in ["context", "qualification"]
                else "owner-operated-oracle-release"
            )
            and dt.timedelta(0) < expiry - issued <= dt.timedelta(hours=24)
            and (historical or issued <= now() < expiry),
            "SIGNED_RECORD_EXPIRED",
        )
        records.append(r)
        if r["parent"] and not parent_checked:
            record(r["parent"], historical=historical)
        if strict_ancestors and r["kind"] == "authorization":
            # The decision and authorization share the already verified Q parent.
            # Validate the decision itself without revisiting that ancestor; keep
            # the existing cycle/size guard and require the identical signed Q.
            attempt = record(
                r["details"]["attempt_id"],
                "authorization-attempt",
                historical,
                parent_checked=True,
            )
            require(
                attempt["parent"] == r["parent"],
                "AUTHORIZATION_DECISION_QUALIFICATION_MISMATCH",
            )
        return r

    try:
        historical = phase == "production"
        if "operation_id" in context:
            if dependency:
                child = db.execute(
                    "SELECT id FROM records WHERE kind='production-verification' AND parent=? ORDER BY sequence DESC LIMIT 1",
                    (context["operation_id"],),
                ).fetchone()
                require(child, "ACCEPTED_API_VERIFICATION_REQUIRED")
                record(child[0], "production-verification", True)
                op = next(
                    item for item in records if item["id"] == context["operation_id"]
                )
            else:
                op = record(context["operation_id"], "deployment-intent", historical)
            row = db.execute(
                "SELECT state,authorization FROM deployments WHERE id=?", (op["id"],)
            ).fetchone()
            require(
                row
                and (phase != "deploy" or row[0] == "request-recorded")
                and (not dependency or row[0] == "accepted"),
                "DURABLE_DEPLOYMENT_INTENT_REQUIRED",
            )
            require(
                op["details"]["provider_request_budget"] == 1
                and op["details"]["automatic_retry"] is False,
                "SINGLE_REQUEST_BUDGET_REQUIRED",
            )
            if repair_dependency is not None:
                require(
                    row[0] == "deployed-unverified",
                    "REPAIR_DEPENDENCY_API_MUST_REMAIN_UNVERIFIED",
                )
                prerequisite = record(
                    repair_dependency,
                    "frontend-repair-dependency-commission",
                    False,
                    parent_checked=True,
                )
                require(
                    prerequisite["parent"] == op["id"],
                    "REPAIR_PREREQUISITE_API_OPERATION_CHANGED",
                )
                identities = {
                    prerequisite["details"]["observer_commission"],
                    prerequisite["details"]["failure"]["observer_commission"],
                }
                for identity in identities:
                    observed = record(
                        identity,
                        "production-observer-commission",
                        True,
                        parent_checked=True,
                    )
                    require(
                        observed["parent"] == op["id"], "REPAIR_OBSERVER_PARENT_CHANGED"
                    )
            if phase == "deploy":
                require(
                    row[1] == context["authorization_id"] and op["parent"] == row[1],
                    "AUTHORIZATION_OPERATION_MISMATCH",
                )
        elif "context_id" in context:
            r = record(context["context_id"], "context")
            state = db.execute(
                "SELECT state FROM contexts WHERE id=?", (r["id"],)
            ).fetchone()
            require(
                state and state[0] == "reserved",
                "QUALIFICATION_NOT_ATOMICALLY_RESERVED",
            )
        elif "qualification_id" in context:
            record(context["qualification_id"], "qualification")
        elif "authorization_id" in context:
            record(context["authorization_id"], "authorization")
        else:
            raise Refused("SIGNED_OWNER_INVOCATION_REQUIRED")
    finally:
        db.close()
    return records


def source_inventory(archive_path, binding):
    expected = {}
    files = {}
    tree = {}
    total = 0
    with tarfile.open(archive_path, "r:") as archive:
        members = archive.getmembers()
        require(len(members) < 20000, "SOURCE_ARCHIVE_ENTRY_BOUND")
        for member in members:
            path = Path(member.name)
            require(
                not path.is_absolute()
                and ".." not in path.parts
                and (member.isfile() or member.isdir()),
                "SOURCE_ARCHIVE_TYPE_OR_PATH",
            )
            if member.isfile():
                total += member.size
                require(
                    member.size <= 32 * 1024 * 1024 and total <= 512 * 1024 * 1024,
                    "SOURCE_ARCHIVE_BYTE_BOUND",
                )
                data = archive.extractfile(member).read()
                node = tree
                for name in path.parts[:-1]:
                    node = node.setdefault(name, {})
                    require(isinstance(node, dict), "SOURCE_DIRECTORY_ALIAS")
                require(path.name not in node, "DUPLICATE_SOURCE_ARCHIVE_PATH")
                files[member.name] = hashlib.sha256(data).hexdigest()
                blob = hashlib.sha1(
                    b"blob " + str(len(data)).encode() + b"\0" + data
                ).digest()
                node[path.name] = (
                    b"100755" if member.mode & 0o111 else b"100644",
                    blob,
                )
            prefix = "onesa-ai-robot-backend/"
            if member.isfile() and member.name.startswith(prefix):
                rel = member.name[len(prefix) :]
                if rel.startswith("robot_api/") or rel == "start.sh":
                    require(member.size < 8 * 1024 * 1024, "SOURCE_FILE_BOUND")
                    expected[rel] = hashlib.sha256(data).hexdigest()

    def git_tree(node):
        value = b""
        for name, entry in sorted(
            node.items(),
            key=lambda p: (p[0] + ("/" if isinstance(p[1], dict) else "")).encode(),
        ):
            mode, digest = (
                (b"40000", git_tree(entry)) if isinstance(entry, dict) else entry
            )
            value += mode + b" " + name.encode() + b"\0" + digest
        return hashlib.sha1(
            b"tree " + str(len(value)).encode() + b"\0" + value
        ).digest()

    require(
        git_tree(tree).hex()
        == (
            source_binding(binding)["product_source_tree"]
            if is_canonical(binding)
            else binding["source_tree"]
        ),
        "ARCHIVE_DIFFERS_FROM_QUALIFIED_GIT_TREE",
    )
    require(
        expected
        and "robot_api/main.py" in expected
        and "robot_api/inspection.py" in expected,
        "COMPLETE_BACKEND_ARCHIVE_REQUIRED",
    )
    return expected, files


def probe_cpu_limit():
    """Fit the bounded probe to the actual daemon; never request new capacity."""
    value = docker("info", "--format", "{{.NCPU}}", timeout=15).strip()
    require(re.fullmatch(rb"[1-9][0-9]{0,3}", value), "PROBE_HOST_CPU_UNVERIFIED")
    return str(min(2, int(value)))


CHECKS = {
    "staging": {
        "source_build_image_identity",
        "configuration_and_isolation",
        "authentication_and_authorization",
        "persistence",
        "affected_workflows",
        "schema_and_migrations",
        "rollback_readiness",
        "security_and_contracts",
    },
    "operations": {
        "backup_restore",
        "monitoring_and_capacity",
        "privacy_publication_controls",
        "preservation_baseline",
        "rollback_readiness",
    },
    "deploy": {
        "exact_authorized_artifact",
        "configuration_preserved",
        "independent_components_preserved",
        "provider_receipt",
    },
    "production": {
        "source_build_image_identity",
        "health_readiness",
        "authentication_and_authorization",
        "affected_workflows",
        "persistence_preserved",
        "independent_components_preserved",
        "configuration_preserved",
        "no_unapproved_processing_or_actuation",
    },
}


def verify_toolchain(plan, binding, *, observer=None):
    validate_source(binding)
    require(
        binding["component"] in COMPONENTS
        and binding["target_environment"] == "production",
        "ROBOT_COMPONENT_SCOPE",
    )
    store = private(Path(plan["verifier_store"]), directory=True)
    trust = read(store / "trust.json")
    anchor = read(ANCHOR)["installations"].get(str(store))
    require(
        anchor
        and anchor["active"] is True
        and all(
            anchor[k] == trust[k]
            for k in ["installation_id", "key_id", "verifier_sha256", "policy_sha256"]
        ),
        "OWNER_INSTALLATION_NOT_COMMISSIONED",
    )
    require(
        sha(private(store / "policy.json")) == trust["policy_sha256"]
        and sha(private(VERIFIER)) == trust["verifier_sha256"]
        and sha(OPENSSL) == trust["openssl_sha256"],
        "COMMISSIONED_TOOLCHAIN_CHANGED",
    )
    policy = read(store / "policy.json")
    if is_canonical(binding):
        source = source_binding(binding)
        tool = policy.get("toolchain", {})
        names = set(policy.get("components", {}))
        require(
            policy.get("schema_version") == 2
            and tool.get("source_binding") == source
            and tool.get("source_commit") == binding["candidate_commit"]
            and tool.get("source_tree") == binding["source_tree"]
            and tool.get("source_sha256") == binding["source_sha256"]
            and names
            in [
                {CANONICAL_REPOSITORY + ":robot:" + name for name in selected}
                for selected in ({"api", "frontend"}, set(COMPONENTS))
            ]
            and all(
                type(item.get("qualification_attempt_limit")) is int
                and item["qualification_attempt_limit"] == 1
                for item in policy["components"].values()
            ),
            "CANONICAL_ROBOT_COMMISSIONED_SOURCE_REQUIRED",
        )
        pins = tool.get("source_archives", {})
        require(
            set(pins)
            == {
                "robot-runtime-onesa-ai-robot-backend",
                "robot-runtime-onesa-ai-robot-frontend",
            },
            "CANONICAL_ROBOT_SOURCE_ARCHIVE_SET",
        )
        for name, pin in pins.items():
            archive = private(VERIFIER.parent / "source-artifacts" / (name + ".zip"))
            require(
                isinstance(pin, str)
                and re.fullmatch(r"[a-f0-9]{64}", pin)
                and 0 < archive.stat().st_size <= 24 * 1024
                and sha(archive) == pin,
                "CANONICAL_ROBOT_SOURCE_ARCHIVE_CHANGED",
            )
    profile = policy["components"].get(component_scope(binding))
    require(
        profile and profile["qualification_attempt_limit"] == 1,
        "COMPONENT_NOT_COMMISSIONED_WITH_ONE_CONTEXT",
    )
    require(
        all(
            set(CHECKS[phase]) <= set(profile["required_checks"][phase])
            for phase in CHECKS
        ),
        "MANDATORY_CHECKS_REMOVED",
    )
    if observer is not None:
        require(
            not is_canonical(binding), "CANONICAL_RECOVERY_HANDOFF_NOT_COMMISSIONED"
        )
        # The original issuer/source chain remains pinned above. A separately
        # commissioned, read-only observer supplies its own reviewed tool policy.
        policy = observer.current_policy()
        profile = policy["components"][binding["repository"] + ":api"]
        require(
            profile["qualification_attempt_limit"] == 1
            and all(
                set(CHECKS[phase]) <= set(profile["required_checks"][phase])
                for phase in CHECKS
            ),
            "OBSERVER_CANNOT_REMOVE_MANDATORY_CHECKS",
        )
    installation = read(VERIFIER.parent / "installation.json")
    require(
        installation["state"] == "INSTALLED_NOT_COMMISSIONED"
        and installation["source"] == policy["toolchain"]["source_commit"],
        "INSTALLED_SOURCE_IDENTITY",
    )
    if is_canonical(binding):
        require(
            installation.get("source_binding") == source_binding(binding),
            "CANONICAL_INSTALLED_SOURCE_IDENTITY",
        )
    for name, pin in installation["files"].items():
        p = private(VERIFIER.parent / name)
        require(sha(p) == pin, "INSTALLED_EXECUTABLE_CHANGED")
    for phase in CHECKS:
        runner = profile["runners"][phase]
        script = (
            "oracle_robot.py"
            if base_component(binding) == "api"
            else "oracle_robot_frontend.py"
        )
        path = Path(__file__).with_name(script)
        require(
            runner["path"] == str(path.resolve())
            and runner["sha256"] == sha(private(path)),
            "RUNNER_PATH_OR_PIN_CHANGED",
        )
    tool = policy["toolchain"]
    require(
        plan["probe_image"] == tool["probe_image"]
        and plan["cli_image"] == tool["cli_image"]
        and plan["builder_image"] == tool["builder_image"],
        "CANDIDATE_SELECTED_UNREVIEWED_TOOL_IMAGE",
    )
    return tool


def component_folder(binding):
    require(
        binding["component"] in COMPONENTS
        and re.fullmatch("[a-f0-9]{40}", binding["candidate_commit"]),
        "COMPONENT_ARTIFACT_SCOPE",
    )
    return private(
        (ROOT / "canonical-robot" if is_canonical(binding) else ROOT)
        / binding["component"]
        / "artifacts"
        / binding["candidate_commit"],
        directory=True,
    )


def candidate_environment(binding, env):
    require(
        env in ENV and re.fullmatch("[a-f0-9]{40}", binding["candidate_commit"]),
        "CANDIDATE_ENVIRONMENT_SCOPE",
    )
    base_component(binding)
    root = Path("/etc/onesa/backends/candidates")
    if is_canonical(binding):
        source_binding(binding)
        root /= "canonical-robot"
    root /= binding["candidate_commit"]
    if binding["component"] in IDENTITY_COMPONENTS:
        # The paired frontend consumes its independently accepted identity API
        # configuration. Native bridge bytes remain in their original location.
        root /= "identity-api"
    return root / ("robot-" + env + ".env")


def verify_application_bytes(folder, binding):
    expected, _ = source_inventory(private(folder / "source.tar"), binding)
    program = "import pathlib,hashlib,json;root=pathlib.Path('/app');out={}\nfor p in (root/'robot_api').rglob('*'):\n if p.is_file() and '__pycache__' not in p.parts and not p.name.endswith('.pyc'):out[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()\nout['start.sh']=hashlib.sha256((root/'start.sh').read_bytes()).hexdigest()\nprint(json.dumps(out))"
    actual = json.loads(
        docker(
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--entrypoint",
            "python",
            binding["artifact_digest"],
            "-c",
            program,
            timeout=60,
        )
    )
    require(actual == expected, "IMAGE_APPLICATION_BYTES_DIFFER_FROM_QUALIFIED_SOURCE")
    image = json.loads(docker("image", "inspect", binding["artifact_digest"]))[0]
    labels = image["Config"].get("Labels", {})
    require(
        image["Id"] == binding["artifact_digest"]
        and image["Architecture"] == "arm64"
        and labels.get("org.opencontainers.image.revision")
        == binding["candidate_commit"]
        and labels.get("org.onesa.source-archive-sha256") == binding["source_sha256"],
        "IMAGE_BUILD_SOURCE_OR_ARCHITECTURE_MISMATCH",
    )
    return hashlib.sha256(canonical(expected)).hexdigest()


def credentials(plan, binding):
    value = read(CUSTODY)
    require(
        value["schema_version"] == 1
        and value["candidate_commit"] == binding["candidate_commit"]
        and value["release_authority"] is False
        and value["mechanism"] == "owner-tty-existing-credential-hash-confirmation",
        "OPERATOR_CUSTODY_SOURCE_SCOPE",
    )
    created, expiry = (
        dt.datetime.fromisoformat(value["created_at"]),
        dt.datetime.fromisoformat(value["expires_at"]),
    )
    require(
        dt.timedelta(0) < expiry - created <= dt.timedelta(hours=1)
        and created <= now() < expiry,
        "OPERATOR_CUSTODY_EXPIRED_OWNER_TTY_REQUIRED",
    )
    for env, runtime_path in ENV.items():
        item = value["credentials"][env]
        current = runtime_values(runtime_path)
        prepared = runtime_values(candidate_environment(binding, env))
        require(
            item["operator"]
            == current["ROBOT_OPERATOR_NAME"]
            == prepared["ROBOT_OPERATOR_NAME"]
            and item["password_hash_sha256"]
            == hashlib.sha256(
                current["ROBOT_OPERATOR_PASSWORD_HASH"].encode()
            ).hexdigest()
            and current["ROBOT_OPERATOR_PASSWORD_HASH"]
            == prepared["ROBOT_OPERATOR_PASSWORD_HASH"],
            "OPERATOR_IDENTITY_OR_PASSWORD_CHANGED",
        )
        require(
            item["runtime_sha256"]
            in [sha(runtime_path), plan["baseline_runtime_sha256"][env]],
            "CUSTODY_RUNTIME_BINDING_CHANGED",
        )
    return value["credentials"]


def frontend_configuration(binding, env):
    require(
        env in ENV and re.fullmatch("[a-f0-9]{40}", binding["candidate_commit"]),
        "FRONTEND_CONFIGURATION_SCOPE",
    )
    base_component(binding)
    root = Path("/etc/onesa/backends/candidates")
    if is_canonical(binding):
        source_binding(binding)
        root /= "canonical-robot"
    root /= binding["candidate_commit"]
    if binding["component"] in IDENTITY_COMPONENTS:
        root /= "identity-frontend"
    return root / ("robot-frontend-" + env + ".json")


def frontend_configuration_value(plan, env):
    binding = (
        plan_binding(plan, "frontend")
        if "bindings" in plan
        else {"repository": LEGACY_REPOSITORY}
    )
    return {
        "schema_version": 1,
        "repository": binding["repository"],
        **(source_binding(binding) if is_canonical(binding) else {}),
        "component": "identity-frontend"
        if plan.get("identity") is not None
        else "frontend",
        "environment": env,
        "project_id": plan["project_id"],
        "team_id": plan["team_id"],
        "application_root": frontend_root(binding),
        "origin": plan["origins"][env],
        "api_origin": plan["api_origins"][env],
        "protection_sha256": plan["vercel_protection_sha256"],
        "api_routing_variable_id": plan["vercel_api_url_variable_ids"][env],
        "serverless_function_region": plan["serverless_function_region"],
    }


def validate_plan(binding, *, observer=None):
    import oracle_robot_identity as identity

    require(
        observer is None or not is_canonical(binding),
        "CANONICAL_RECOVERY_HANDOFF_NOT_COMMISSIONED",
    )
    plan = read(plan_path(binding)) if observer is None else observer.original_plan()
    require(
        plan["schema_version"] == 1
        and plan["bindings"].get(binding["component"]) == binding
        and plan.get("release_component", binding["component"]) == binding["component"]
        and ("independent_api" not in plan or binding["component"] == "frontend"),
        "OWNER_PLAN_BINDING",
    )
    require(
        plan["api_origins"]
        == {
            "staging": "https://robot-api-staging.onesa.eu",
            "production": "https://robot-api.onesa.eu",
        },
        "CANONICAL_ROBOT_API_ORIGINS_REQUIRED",
    )
    tool = verify_toolchain(
        plan, binding, **({"observer": observer} if observer is not None else {})
    )
    if is_canonical(binding) and "independent_api" not in plan:
        expected = (
            IDENTITY_COMPONENTS
            if binding["component"] in IDENTITY_COMPONENTS
            else {"api", "frontend"}
        )
        require(
            set(plan["bindings"]) == expected
            and all(
                value["component"] == name
                and source_identity(value) == source_identity(binding)
                for name, value in plan["bindings"].items()
            ),
            "CANONICAL_PAIRED_COMPONENT_SOURCE_REQUIRED",
        )
    identity_profile = identity.profile(plan, binding, tool)
    require(
        observer is None or identity_profile is None,
        "LEGACY_OBSERVER_CANNOT_CLAIM_IDENTITY_QUALIFICATION",
    )
    if identity_profile is not None:
        identity.native_bridge(plan, binding)
    folder = component_folder(binding)
    require(
        sha(private(folder / "source.tar")) == binding["source_sha256"],
        "SOURCE_ARCHIVE_DIGEST",
    )
    source_inventory(folder / "source.tar", binding)
    require(sha(private(COMPOSE)) == plan["compose_sha256"], "COMPOSE_CHANGED")
    for env, runtime_path in ENV.items():
        dependency_binding = (
            plan["independent_api"][env + "_binding"]
            if base_component(binding) == "frontend" and "independent_api" in plan
            else plan_binding(plan, "api")
        )
        prepared = private(candidate_environment(binding, env))
        current = runtime_values(runtime_path)
        values = runtime_values(prepared)
        require(
            sha(prepared) == dependency_binding["configuration_sha256"][env]
            and (
                observer is not None
                and env == "staging"
                or sha(runtime_path)
                in [
                    plan["baseline_runtime_sha256"][env],
                    dependency_binding["configuration_sha256"][env],
                ]
            ),
            "RUNTIME_CONFIGURATION_CHANGED",
        )
        allowed = {"ROBOT_PUBLIC_ORIGIN"} if env == "staging" else set()
        allowed |= identity.configuration(identity_profile, env, values)
        require(
            observer is not None
            and env == "staging"
            or {k: v for k, v in values.items() if k not in allowed}
            == {k: v for k, v in current.items() if k not in allowed},
            "UNRELATED_CONFIGURATION_OR_CREDENTIAL_CHANGE",
        )
        require(
            values["ROBOT_DEPLOYMENT_ENVIRONMENT"] == env
            and values["ROBOT_STORAGE_MODE"] == "turso"
            and values["ROBOT_ACTUATION_ENABLED"] == "false"
            and values.get("ROBOT_CONTEXT_IMPORT_ENABLED", "false") == "false",
            "APPROVED_SIMULATION_PROFILE_REQUIRED",
        )
        require(
            values["ROBOT_PUBLIC_ORIGIN"] == plan["origins"][env]
            and values["ROBOT_PUBLIC_ORIGIN"].startswith("https://"),
            "EXACT_PROTECTED_HTTPS_ORIGIN_REQUIRED",
        )
        require(
            not values.get("ROBOT_ADMINISTRATOR_NAME")
            or values["ROBOT_ADMINISTRATOR_NAME"] == values["ROBOT_OPERATOR_NAME"],
            "EXISTING_SINGLE_OPERATOR_ROLE_REQUIRED",
        )
    stage_values = runtime_values(candidate_environment(binding, "staging"))
    prod_values = runtime_values(candidate_environment(binding, "production"))
    require(
        all(
            stage_values[k] != prod_values[k]
            for k in [
                "ROBOT_TURSO_DATABASE_URL",
                "ROBOT_TURSO_AUTH_TOKEN",
                "ROBOT_OPERATOR_PASSWORD_HASH",
            ]
        ),
        "STAGING_AND_PRODUCTION_IDENTITIES_MUST_REMAIN_SEPARATE",
    )
    if base_component(binding) == "api":
        require(
            sha(private(folder / "image.tar.gz")) == binding["build_sha256"],
            "BUILD_ARCHIVE_DIGEST",
        )
        image_security(
            read(
                folder / "scan.json"
                if observer is None
                else observer.security_path("api")
            ),
            binding["artifact_digest"],
        )
        verify_application_bytes(folder, binding)
    else:
        for env in ENV:
            configuration = private(frontend_configuration(binding, env))
            require(
                sha(configuration) == binding["configuration_sha256"][env]
                and read(configuration) == frontend_configuration_value(plan, env),
                "FRONTEND_TARGET_CONFIGURATION_CHANGED",
            )
        validate_frontend_builds(folder, binding, plan)
    verify_probe_bytes(
        folder,
        binding,
        plan,
        **({"observer": observer, "tool": tool} if observer is not None else {}),
    )
    if observer is None:
        credentials(plan, binding)
        if identity_profile is not None:
            identity.credentials(plan, binding)
    else:
        observer.production_credentials()
    return folder, plan


def inspect_database_backup(receipt, plan):
    profile = plan["legacy_backup"]
    script = pinned_public_file(
        Path(profile["transport_path"]), profile["transport_sha256"]
    )
    require(
        sha(script) == profile["transport_sha256"]
        and json.loads(docker("image", "inspect", profile["transport_image"]))[0]["Id"]
        == profile["transport_image"],
        "ESTABLISHED_BACKUP_TRANSPORT_CHANGED",
    )
    public = Path("/etc/onesa/recovery-public.pem")
    require(
        not public.is_symlink()
        and public.stat().st_uid == 0
        and not public.stat().st_mode & 0o022
        and sha(public) == profile["key_id"] == receipt["key_id"],
        "RECOVERY_PUBLIC_KEY_CHANGED",
    )
    require(
        receipt["product"] == "robot"
        and receipt["off_instance_storage_verified"] is True
        and re.fullmatch(
            r"\.onesa-database-backups/v1/robot/[a-f0-9]{32}\.json",
            receipt["object_key"],
        ),
        "ROBOT_BACKUP_IDENTITY",
    )
    values = runtime_values(Path(profile["transport_env"]))
    keys = [
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "S3_ENDPOINT",
        "S3_REGION",
        "S3_BUCKET",
    ]
    selected = {k: values[k] for k in keys if k in values}
    require(
        {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "S3_ENDPOINT",
            "S3_REGION",
            "S3_BUCKET",
        }
        <= set(selected),
        "EXISTING_ROBOT_BACKUP_S3_CUSTODY",
    )
    with tempfile.TemporaryDirectory(prefix=".backup-readback-", dir=ROOT) as folder:
        env = Path(folder) / "transport.env"
        with os.fdopen(
            os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600),
            "w",
        ) as f:
            f.write("".join(k + "=" + v + "\n" for k, v in selected.items()))
        objects = [
            (receipt["object_key"], receipt["ciphertext_sha256"]),
            (profile["escrow_object"], profile["escrow_ciphertext_sha256"]),
        ]
        for key, expected in objects:
            require(
                re.fullmatch(
                    r"\.onesa-(?:database-backups/v1/robot/[a-f0-9]{32}\.json|portal-backups/v1/keys/[a-f0-9]{64}\.enc)",
                    key,
                ),
                "ESTABLISHED_BACKUP_OBJECT_SCOPE",
            )
            raw = docker(
                "run",
                "--rm",
                "-i",
                "--pull=never",
                "--read-only",
                "--tmpfs",
                "/tmp",
                "--memory",
                "256m",
                "--cpus",
                "0.1",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--env-file",
                str(env),
                "-v",
                str(script) + ":/app/backup.mjs:ro",
                "--entrypoint",
                "node",
                profile["transport_image"],
                "/app/backup.mjs",
                data=canonical({"action": "read", "key": key}),
                timeout=90,
            )
            require(
                hashlib.sha256(raw).hexdigest() == expected,
                "BACKUP_OR_ESCROW_CIPHERTEXT_DIFFERS",
            )
    return {
        "database_ciphertext_sha256": receipt["ciphertext_sha256"],
        "key_id": receipt["key_id"],
        "escrow_ciphertext_sha256": profile["escrow_ciphertext_sha256"],
        "actual_off_instance_readback": "PASS",
        "key_custody_changed": False,
        "production_restore_performed": False,
    }


def trusted_toolchain(plan):
    store = private(Path(plan["verifier_store"]), directory=True)
    trust = read(store / "trust.json")
    require(
        sha(private(store / "policy.json")) == trust["policy_sha256"], "TOOL_POLICY_PIN"
    )
    return read(store / "policy.json")["toolchain"]


def probe_source_files(files):
    names = {
        "scripts/release/oracle_robot_probe.py",
        "scripts/release/oracle_robot_browser.mjs",
        "scripts/release/oracle_robot_identity.py",
        "scripts/release/oracle_robot_identity_browser.mjs",
        "scripts/release/oracle_robot.py",
        "onesa-ai-robot-frontend/package.json",
        "onesa-ai-robot-frontend/package-lock.json",
        "scripts/release/smoke.py",
        "scripts/release/cloud.py",
        "scripts/release/service_runtime.py",
        "scripts/release/evidence.py",
        "scripts/release/edge_fixture.py",
    }
    require(names <= set(files), "COMPLETE_COMMISSIONED_PROBE_SOURCE_REQUIRED")
    return {name: files[name] for name in names}


def verify_probe_bytes(folder, binding, plan, *, observer=None, tool=None):
    tool = trusted_toolchain(plan) if tool is None else tool
    expected = tool["probe_files"]
    require(
        {
            "scripts/release/oracle_robot_probe.py",
            "scripts/release/oracle_robot_browser.mjs",
        }
        <= set(expected),
        "COMPLETE_PROBE_PINS_REQUIRED",
    )
    program = "import pathlib,hashlib,json,sys;root=pathlib.Path('/opt/onesa-ai-robot');names=json.load(sys.stdin);print(json.dumps({n:hashlib.sha256((root/n).read_bytes()).hexdigest() for n in names}))"
    actual = json.loads(
        docker(
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--entrypoint",
            "python3",
            plan["probe_image"],
            "-c",
            program,
            data=canonical(sorted(expected)),
            timeout=60,
        )
    )
    require(actual == expected, "PROBE_BYTES_DIFFER_FROM_COMMISSIONED_TOOL_SOURCE")
    for kind in ["probe", "cli", "builder"]:
        image_security(
            read(
                private(
                    folder / (kind + "-scan.json")
                    if observer is None
                    else observer.security_path(kind)
                )
            ),
            tool[kind + "_image"],
            "amd64" if kind == "builder" else "arm64",
        )


def validate_frontend_builds(folder, binding, plan):
    tool = trusted_toolchain(plan)
    for env in ENV:
        build = plan["builds"][env]
        for name in ["archive", "receipt"]:
            require(Path(build[name]).name == build[name], "BUILD_RECEIPT_PATH_SCOPE")
        receipt = read(folder / build["receipt"])
        require(
            sha(private(folder / build["receipt"])) == build["receipt_sha256"]
            and receipt["binding"]["candidate_commit"] == binding["candidate_commit"]
            and receipt["binding"]["source_tree"] == binding["source_tree"]
            and receipt["binding"]["source_sha256"] == binding["source_sha256"]
            and (
                not is_canonical(binding)
                or source_identity(receipt["binding"]) == source_identity(binding)
            )
            and receipt["environment"] == env
            and receipt["source_tree_verified"] is True
            and receipt["provider_credentials_mounted"] is False
            and receipt["builder_sha256"] == tool["builder_sha256"]
            and receipt["tool_image"] == tool["builder_image"],
            "CREDENTIAL_FREE_BUILD_PROVENANCE",
        )
        require(
            sha(private(folder / build["archive"]))
            == build["archive_sha256"]
            == receipt["artifact_sha256"],
            "FRONTEND_BUILD_BYTES_CHANGED",
        )
    require(
        binding["build_sha256"] == plan["builds"]["production"]["receipt_sha256"]
        and binding["artifact_digest"]
        == "sha256:" + plan["builds"]["production"]["archive_sha256"],
        "FRONTEND_ARTIFACT_BINDING",
    )
    require(plan["builder_sha256"] == tool["builder_sha256"], "BUILD_TOOL_PIN")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


JOURNAL_DIAGNOSTIC_SQL = (
    "SELECT count(*) AS tasks,"
    "count(CASE WHEN state='proposed' THEN 1 END) AS proposed,"
    "count(CASE WHEN state='running' THEN 1 END) AS running,"
    "count(CASE WHEN state='completed' THEN 1 END) AS completed,"
    "count(CASE WHEN state='cancelled' THEN 1 END) AS cancelled,"
    "(SELECT count(*) FROM robot_simulation_lease_v1) AS lease_rows,"
    "(SELECT count(*) FROM robot_simulation_lease_v1 WHERE id=1 "
    "AND typeof(owner)='text' AND length(owner)=32 "
    "AND owner NOT GLOB '*[^0-9a-f]*' AND typeof(expires)='integer') AS valid_leases,"
    "(SELECT expires-unixepoch() FROM robot_simulation_lease_v1 WHERE id=1) "
    "AS lease_remaining_seconds FROM robot_simulation_tasks_v1"
)


def journal(env, sql):
    """Read-only complete seven-column history; never a lease repair or SQL write."""
    require(env in ENV, "JOURNAL_ENVIRONMENT")
    values = runtime_values(ENV[env])
    url = values["ROBOT_TURSO_DATABASE_URL"]
    expected = (
        "libsql://onesa-ai-robot-backend-"
        + ("staging-" if env == "staging" else "")
        + "druhustle.aws-eu-west-1.turso.io"
    )
    require(url == expected, "EXISTING_JOURNAL_TARGET_REQUIRED")
    allowed = {
        JOURNAL_DIAGNOSTIC_SQL,
        "SELECT id,owner,proposal,state,reason,CASE WHEN typeof(created)='real' THEN printf('%!.26g',created) WHEN typeof(created)='integer' THEN created ELSE NULL END AS created,CASE WHEN typeof(position)='real' THEN printf('%!.26g',position) WHEN typeof(position)='integer' THEN position ELSE NULL END AS position FROM robot_simulation_tasks_v1 ORDER BY id",
        "SELECT owner,expires FROM robot_simulation_lease_v1 WHERE id=1",
        "SELECT sql FROM sqlite_master WHERE name IN ('robot_simulation_tasks_v1','robot_simulation_lease_v1') ORDER BY name",
    }
    require(sql in allowed, "READ_ONLY_ALLOWLISTED_JOURNAL_QUERY_REQUIRED")
    body = {"requests": [{"type": "execute", "stmt": {"sql": sql}}, {"type": "close"}]}
    request = urllib.request.Request(
        url.replace("libsql://", "https://") + "/v2/pipeline",
        data=canonical(body),
        headers={
            "Authorization": "Bearer " + values["ROBOT_TURSO_AUTH_TOKEN"],
            "Content-Type": "application/json",
        },
    )
    with urllib.request.build_opener(NoRedirect()).open(
        request, timeout=20
    ) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    require(len(raw) <= 16 * 1024 * 1024, "JOURNAL_RESPONSE_BOUND")
    result = json.loads(raw)["results"]
    require(
        len(result) == 2
        and all(x["type"] == "ok" for x in result)
        and result[1]["response"]["type"] == "close",
        "JOURNAL_READ_FAILED",
    )
    result = result[0]["response"]
    require(result["type"] == "execute", "JOURNAL_READ_TYPE")
    columns = [x["name"] for x in result["result"]["cols"]]
    rows = []
    for row in result["result"]["rows"]:
        require(
            len(columns) == len(row)
            and all(x["type"] in ["text", "integer", "null"] for x in row),
            "JOURNAL_CELL_TYPE",
        )
        rows.append(
            dict(
                zip(
                    columns,
                    [
                        None
                        if x["type"] == "null"
                        else int(x["value"])
                        if x["type"] == "integer"
                        else x["value"]
                        for x in row
                    ],
                    strict=True,
                )
            )
        )
    return rows


def history(env):
    rows = journal(
        env,
        "SELECT id,owner,proposal,state,reason,CASE WHEN typeof(created)='real' THEN printf('%!.26g',created) WHEN typeof(created)='integer' THEN created ELSE NULL END AS created,CASE WHEN typeof(position)='real' THEN printf('%!.26g',position) WHEN typeof(position)='integer' THEN position ELSE NULL END AS position FROM robot_simulation_tasks_v1 ORDER BY id",
    )
    require(
        len(rows) <= 100000
        and all(x["state"] not in ["proposed", "running"] for x in rows),
        "PENDING_OR_ACTIVE_JOURNAL_WORK_PRESERVED",
    )
    return {x["id"]: hashlib.sha256(canonical(x)).hexdigest() for x in rows}


def preserved(before, after):
    require(
        all(after.get(k) == v for k, v in before.items()),
        "EXISTING_JOURNAL_HISTORY_CHANGED",
    )


def local_identity(binding, env, plan):
    row = inventory()[TARGET[env]]
    dependency = (
        plan["independent_api"][env + "_binding"]
        if base_component(binding) == "frontend" and "independent_api" in plan
        else None
    )
    expected = (
        dependency["artifact_digest"]
        if dependency is not None
        else binding["artifact_digest"]
        if base_component(binding) == "api"
        else plan["retained_api"]["image"]
    )
    source = (
        dependency["candidate_commit"]
        if dependency is not None
        else binding["candidate_commit"]
        if base_component(binding) == "api"
        else plan["retained_api"]["source"]
    )
    require(
        healthy(row)
        and row["Image"] == expected
        and row["Config"]["Labels"].get("org.onesa.source-commit") == source,
        "ACTUAL_RUNNING_SOURCE_OR_IMAGE_MISMATCH",
    )
    if dependency is not None:
        require(
            summary(row) == plan["independent_api"]["runtimes"][env],
            "INDEPENDENT_API_RUNTIME_CHANGED",
        )
    values = dict(x.split("=", 1) for x in row["Config"]["Env"])
    prepared = runtime_values(candidate_environment(binding, env))
    require(
        all(values.get(k) == v for k, v in prepared.items()),
        "RUNNING_CONFIGURATION_MISMATCH",
    )
    if base_component(binding) == "api":
        verify_application_bytes(component_folder(binding), binding)
    return summary(row)


def authenticated_state(binding, env, plan):
    """Read only: never stop, reset, cancel or fence pre-existing work."""
    import http.cookiejar

    if runtime_values(ENV[env]).get("ONESA_IDENTITY_ENABLED") == "true":
        import oracle_robot_identity as identity

        require(plan.get("identity") is not None, "EXPLICIT_IDENTITY_PROFILE_REQUIRED")
        return identity.authenticated_state(plan, binding, env)

    values = credentials(plan, binding)[env]
    origin = runtime_values(ENV[env])["ROBOT_PUBLIC_ORIGIN"]
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        NoRedirect(), urllib.request.HTTPCookieProcessor(jar)
    )

    # Use the direct existing API listener with the exact configured Origin. HTTPS
    # cookies remain on the public canonical host; no cookie-domain rewrite occurs.
    def call(path, body=None, csrf=None):
        headers = {
            "Origin": origin,
            "Content-Type": "application/json",
            "User-Agent": "OnesaOracleRobotOwner/1",
        }
        if csrf:
            headers["X-CSRF-Token"] = csrf
        request = urllib.request.Request(
            plan["api_origins"][env] + path,
            data=canonical(body) if body is not None else None,
            headers=headers,
        )
        with opener.open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
            require(len(raw) <= 1024 * 1024, "STATE_RESPONSE_BOUND")
            return json.loads(raw)

    ready = call("/api/v1/ready")
    require(
        ready["mode"] == "simulation"
        and ready["physical_actuation"] is False
        and ready["deployment_environment"] == env
        and ready["storage_mode"] == "turso",
        "SIMULATION_READINESS_REQUIRED",
    )
    login = call(
        "/api/v1/auth/login",
        {"name": values["operator"], "password": values["password"]},
    )
    try:
        value = call("/api/v1/status")
        require(
            value["stopped"] is True
            and value["active_task"] is None
            and value["physical_actuation"] is False
            and value["mode"] == "simulation",
            "EXISTING_RUNTIME_NOT_STOPPED_IDLE_PRESERVE_WORK",
        )
        return value
    finally:
        call("/api/v1/auth/logout", {}, login["csrf"])


def preflight(binding):
    _folder, plan = validate_plan(binding)
    before = inventory()
    frontend = base_component(binding) == "frontend"
    if frontend:
        # The API release precedes independent frontend qualification. Require
        # its genuine accepted chain, then inspect both actual API runtimes.
        # Keep the original API baseline and all later terminal rows intact.
        import oracle_robot_frontend as frontend_runner

        frontend_runner.accepted_api(plan)
    for env in ENV:
        if frontend:
            local_identity(
                binding if "independent_api" in plan else plan_binding(plan, "api"),
                env,
                plan,
            )
        else:
            require(
                summary(before[TARGET[env]]) == plan["baseline"][env]
                and healthy(before[TARGET[env]]),
                "RUNTIME_CHANGED_SINCE_PREPARATION",
            )
        authenticated_state(binding, env, plan)
        current_history = history(env)
        if frontend:
            preserved(plan["history_baseline"][env], current_history)
        else:
            require(
                current_history == plan["history_baseline"][env],
                "HISTORY_CHANGED_SINCE_PREPARATION",
            )
    unchanged(before, inventory())
    probe_cpu_limit()
    require(shutil.disk_usage("/").free > 8 * 1024**3, "EXISTING_HOST_DISK_CAPACITY")
    return {
        "status": "PREFLIGHT_READY_NOT_QUALIFIED",
        "candidate_commit": binding["candidate_commit"],
        "component": binding["component"],
        "provider_writes": 0,
        "accounts_changed": False,
        "physical_hardware_authorized": False,
    }


@contextmanager
def provider_lock():
    path = ROOT / "provider.lock"
    with os.fdopen(
        os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "r+"
    ) as stream:
        private(path)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Refused("CONCURRENT_PROVIDER_OPERATION_INSPECT_ONLY") from None
        yield


def atomic_replace(path, data, operation):
    target = path.with_name(path.name + "." + operation)
    write_raw(target, data)
    os.replace(target, path)
    sync_directory(path.parent)


def activate(binding, env, operation, folder, plan):
    require(
        str(UUID(operation)) == operation
        and env in ENV
        and base_component(binding) == "api",
        "API_DEPLOYMENT_SCOPE",
    )
    with provider_lock():
        before = inventory()
        name = TARGET[env]
        require(
            summary(before[name]) == plan["baseline"][env] and healthy(before[name]),
            "PREPARED_TARGET_CHANGED",
        )
        authenticated_state(binding, env, plan)
        snapshot = history(env)
        require(
            snapshot == plan["history_baseline"][env], "PRESERVATION_BASELINE_CHANGED"
        )
        op = folder / "operations" / operation
        op.mkdir(mode=0o700, exist_ok=False)
        images = runtime_values(IMAGES)
        prefix = "ORACLE_" + env.upper()
        require(
            images[prefix + "_IMAGE"] == before[name]["Image"]
            and images[prefix + "_COMMIT"] == plan["baseline"][env]["source"],
            "IMAGE_MAP_CHANGED",
        )
        require(
            sha(ENV[env]) == plan["baseline_runtime_sha256"][env],
            "RUNTIME_FILE_CHANGED",
        )
        write(
            op / "intent.json",
            {
                "binding": binding,
                "operation_id": operation,
                "environment": env,
                "provider_request_budget": 1,
                "automatic_retry": False,
                "requested_at": now().isoformat(),
                "before": {n: summary(x) for n, x in before.items()},
                "history": snapshot,
            },
        )
        write_raw(op / "previous-runtime.env", private(ENV[env]).read_bytes())
        write_raw(op / "previous-images.env", private(IMAGES).read_bytes())
        sync_directory(op)
        atomic_replace(
            ENV[env],
            private(candidate_environment(binding, env)).read_bytes(),
            operation,
        )
        images.update(
            {
                prefix + "_IMAGE": binding["artifact_digest"],
                prefix + "_COMMIT": binding["candidate_commit"],
            }
        )
        atomic_replace(
            IMAGES,
            ("".join(k + "=" + v + "\n" for k, v in images.items())).encode(),
            operation,
        )
        # The sole replacement request is journalled first. The old process must
        # release its lease normally; no old-source restart or SQL fencing occurs.
        args = [
            "/usr/bin/docker",
            "compose",
            "--env-file",
            str(IMAGES),
            "-f",
            str(COMPOSE),
            "--profile",
            env,
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "--wait",
            "--wait-timeout",
            "120",
            env,
        ]
        try:
            result = subprocess.run(
                args,
                capture_output=True,
                timeout=240,
                check=False,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = None
        after = inventory()
        actual = after[name]
        receipt = {
            "binding": binding,
            "operation_id": operation,
            "environment": env,
            "deployment_id": actual["Id"],
            "provider_exit": code,
            "actual": summary(actual),
            "observed_at": now().isoformat(),
        }
        write(op / "provider-receipt.json", receipt)
        require(
            actual["Id"] != before[name]["Id"],
            "PROVIDER_REPLACEMENT_NOT_OBSERVED_INSPECT_ONLY",
        )
        local_identity(binding, env, plan)
        preserved(snapshot, history(env))
        unchanged(before, after, [name])
        return receipt


RECOVERY_PROGRAM = "import json,time,secrets,tempfile,sqlite3\nfrom pathlib import Path\nfrom uuid import uuid4\nfrom fastapi.testclient import TestClient\nfrom robot_api.main import Settings,create_app\nfrom robot_api.security import password_hash\npassword=secrets.token_urlsafe(24)\nwith tempfile.TemporaryDirectory() as directory:\n config=Settings(operator_password_hash=password_hash(password),database_path=Path(directory)/'journal.sqlite3')\n with TestClient(create_app(config)) as api:\n  api.headers['Origin']=config.public_origin\n  login=api.post('/api/v1/auth/login',json={'name':'operator','password':password});assert login.status_code==200\n  api.headers['X-CSRF-Token']=login.json()['csrf']\n  assert api.post('/api/v1/reset',json={'password':password}).status_code==200\n  identifier=str(uuid4())\n  planned=api.post('/api/v1/missions',json={'command_id':identifier,'robot_profile_id':'sim-inspector','scenario':'threshold','seed':101})\n  assert planned.status_code==201\n  detail=planned.json()\n  approved=api.post('/api/v1/missions/'+identifier+'/approve',json={'password':password,'expected_plan_sha256':detail['record']['plan_sha256']});assert approved.status_code==200\n  deadline=time.monotonic()+12\n  while time.monotonic()<deadline:\n   api.post('/api/v1/heartbeat',json={})\n   detail=api.get('/api/v1/missions/'+identifier).json()\n   if detail['record']['outcome']=='completed_findings':break\n   time.sleep(.2)\n  assert detail['record']['outcome']=='completed_findings'\n  exported=api.get('/api/v1/missions/'+identifier+'/export').json()\n  assert exported==detail\n  cookies=dict(api.cookies)\n with TestClient(create_app(config)) as restored:\n  restored.headers['Origin']=config.public_origin\n  restored.cookies.update(cookies)\n  assert restored.get('/api/v1/auth/me').status_code==401\n  restored.cookies.clear()\n  login=restored.post('/api/v1/auth/login',json={'name':'operator','password':password});assert login.status_code==200\n  actual=restored.get('/api/v1/missions/'+identifier+'/export').json()\n  assert actual==exported\n  status=restored.get('/api/v1/status').json();assert status['stopped'] is True and status['active_task'] is None\n  assert status['physical_actuation'] is False\n connection=sqlite3.connect(config.database_path)\n columns=[x[1] for x in connection.execute('PRAGMA table_info(tasks)')]\n assert columns==['id','owner','proposal','state','reason','created','position']\n print(json.dumps({'status':'PASS','fixture':'owned-disposable-synthetic','schema':'seven-fields','completed_findings_restored':True,'sessions_revoked':True,'stopped_idle':True,'network':'none','production_writes':0}))\n"


def schema_recovery(binding, folder):
    require(base_component(binding) == "api", "API_SCHEMA_RECOVERY_ONLY")
    raw = docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,mode=1777,size=64m",
        "--memory",
        "256m",
        "--cpus",
        "0.25",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--entrypoint",
        "python",
        binding["artifact_digest"],
        "-c",
        RECOVERY_PROGRAM,
        timeout=45,
    )
    value = json.loads(raw)
    require(
        value
        == {
            "status": "PASS",
            "fixture": "owned-disposable-synthetic",
            "schema": "seven-fields",
            "completed_findings_restored": True,
            "sessions_revoked": True,
            "stopped_idle": True,
            "network": "none",
            "production_writes": 0,
        },
        "ACTUAL_IMAGE_OFFLINE_RECOVERY_FAILED",
    )
    return value


def post_staging_monitor(stage):
    """Wait for actual monitor recovery; never reuse a pre-staging sample."""
    staged_at = dt.datetime.fromisoformat(stage["observed_at"])
    require(
        staged_at.tzinfo is not None
        and dt.timedelta(0) <= now() - staged_at < dt.timedelta(minutes=5),
        "FRESH_STAGING_RECEIPT_REQUIRED",
    )
    deadline = time.monotonic() + 240
    while True:
        require(
            dt.timedelta(0) <= now() - staged_at < dt.timedelta(minutes=5),
            "FRESH_STAGING_RECEIPT_REQUIRED",
        )
        metrics = read(Path("/var/lib/onesa/operations/metrics.json"))
        checked_at = dt.datetime.fromisoformat(metrics["checked_at"])
        require(
            metrics["simulation"] is False
            and checked_at.tzinfo is not None
            and checked_at <= now(),
            "GENUINE_MONITOR_SAMPLE_REQUIRED",
        )
        if (
            checked_at >= staged_at
            and now() - checked_at < dt.timedelta(minutes=5)
            and not metrics["alerts"]
            and not metrics.get("notification_pending")
            and not metrics.get("notification_reconciliation_required")
            and metrics.get("incident_open") is False
            and type(metrics.get("healthy_samples")) is int
            and metrics["healthy_samples"] >= 2
        ):
            return metrics
        remaining = deadline - time.monotonic()
        require(remaining > 0, "POST_STAGING_MONITOR_NOT_RECOVERED")
        time.sleep(min(5, remaining))


def qualified_staging_observation(binding, plan, folder, context_id, qualification):
    """Re-observe existing staging read-only; preserve signed qualification bytes."""
    original = folder / "observations" / context_id / "receipt.json"
    require(
        qualification["kind"] == "qualification"
        and qualification["binding"] == binding
        and qualification["parent"] == context_id
        and qualification["details"]["operations"]["observations"][
            "staging_receipt_sha256"
        ]
        == sha(original),
        "SIGNED_STAGING_RECEIPT_CHANGED",
    )
    # The runner verifies expiry, revocation and all parent signatures first.
    issued = dt.datetime.fromisoformat(qualification["issued_at"])
    expiry = dt.datetime.fromisoformat(qualification["expires_at"])
    require(
        issued.tzinfo is not None
        and expiry.tzinfo is not None
        and issued <= now() < expiry,
        "CURRENT_SIGNED_QUALIFICATION_REQUIRED",
    )
    import oracle_robot_frontend as frontend

    frontend_binding = plan_binding(plan, "frontend")
    preview = (
        component_folder(frontend_binding)
        / "operations"
        / context_id
        / "provider-receipt.json"
    )
    receipt = read(preview)
    require(
        receipt["binding"] == frontend_binding
        and receipt["environment"] == "staging"
        and receipt["operation_id"] == context_id
        and receipt["artifact_sha256"] == plan["builds"]["staging"]["archive_sha256"],
        "EXISTING_SOURCE_BOUND_STAGING_PREVIEW_REQUIRED",
    )
    # Only existing receipt inspection and GET requests; no create/activate call.
    observed_plan = {**plan, "preview_receipt_path": str(preview)}
    operation = context_id + "-recheck-" + str(time.time_ns())
    frontend.verify_static(
        frontend_binding,
        "staging",
        component_folder(frontend_binding),
        observed_plan,
        operation,
    )
    observed = probe(
        binding, "staging", operation, folder, observed_plan, read_only=True
    )
    require(
        observed["binding"] == binding
        and observed["environment"] == "staging"
        and observed["operation_id"] == operation
        and observed["status"] == "PASS"
        and observed["read_only"] is True
        and observed["schema_compatibility"] == "PASS"
        and observed["existing_records_preserved"] is True,
        "READ_ONLY_STAGING_RECHECK_FAILED",
    )
    require(
        sha(original)
        == qualification["details"]["operations"]["observations"][
            "staging_receipt_sha256"
        ],
        "SIGNED_STAGING_RECEIPT_CHANGED",
    )
    return observed, operation


def operational_observations(binding, plan, folder, context_id, *, qualification=None):
    stage = read(folder / "observations" / context_id / "receipt.json")
    require(
        stage["binding"] == binding
        and stage["status"] == "PASS"
        and stage["schema_compatibility"] == "PASS"
        and stage["existing_records_preserved"] is True,
        "STAGING_PRESERVATION_OR_RESTORE_NOT_PROVEN",
    )
    original_stage = stage
    recheck = None
    staged_at = dt.datetime.fromisoformat(stage["observed_at"])
    require(
        staged_at.tzinfo is not None and staged_at <= now(),
        "FRESH_STAGING_RECEIPT_REQUIRED",
    )
    if now() - staged_at >= dt.timedelta(minutes=5) and qualification is not None:
        stage, recheck = qualified_staging_observation(
            binding, plan, folder, context_id, qualification
        )
    metrics = post_staging_monitor(stage)
    m = metrics["metrics"]
    require(
        m["database_available"]
        and not m["database_backup_failed"]
        and m["memory_available_bytes"] > 2 * 1024**3
        and m["disk_used_percent"] < 80,
        "OPERATIONS_CAPACITY",
    )
    for timer in ["onesa-database-backup-robot.timer", "onesa-portal-monitor.timer"]:
        require(
            command(["/usr/bin/systemctl", "is-active", timer]).strip() == b"active",
            "EXISTING_TIMER_INACTIVE",
        )
    backup = read(Path("/var/lib/onesa/operations/database-backup-robot-success.json"))
    require(
        backup["consistent_snapshot"] is True
        and dt.timedelta(0)
        <= now() - dt.datetime.fromisoformat(backup["verified_at"])
        < dt.timedelta(hours=36),
        "ROBOT_BACKUP_STALE",
    )
    readback = inspect_database_backup(backup, plan)
    rollback = plan["rollback"]
    require(
        json.loads(docker("image", "inspect", rollback["image"]))[0]["Id"]
        == rollback["image"]
        and rollback["image"] == plan_binding(plan, "api")["artifact_digest"]
        and rollback["source"] == plan_binding(plan, "api")["candidate_commit"]
        and rollback["journal_schema"] == "robot_simulation_tasks_v1/seven-fields"
        and rollback["mode"] == "preserve-qualified-api-and-journal-roll-forward-only"
        and rollback["automatic_rollback"] is False,
        "ROLLBACK_ARTIFACT_OR_SCHEMA_MISSING",
    )
    require(
        plan["privacy_decision"] == "DEFERRED_BY_OWNER_UNTIL_LAUNCH"
        and plan["physical_hardware_authorized"] is False
        and plan["processing_scope"]
        == "restricted-development-owned-synthetic-verification"
        and plan["publication_controls_unchanged"] is True,
        "EXISTING_DEVELOPMENT_DEFERRAL_SCOPE",
    )
    return {
        "checks": {k: "PASS" for k in CHECKS["operations"]},
        "post_staging_monitor": {
            "checked_at": metrics["checked_at"],
            "healthy_samples": metrics["healthy_samples"],
            "observed_metrics_sha256": hashlib.sha256(canonical(metrics)).hexdigest(),
            "unresolved_alerts": 0,
            "unresolved_notifications": 0,
        },
        "backup_readback": readback,
        "backup_verified_at": backup["verified_at"],
        "staging_receipt_sha256": sha(
            folder / "observations" / context_id / "receipt.json"
        ),
        "qualified_staging_observed_at": original_stage["observed_at"],
        "read_only_staging_recheck": (
            {
                "operation_id": recheck,
                "observed_at": stage["observed_at"],
                "receipt_sha256": sha(
                    folder / "observations" / recheck / "receipt.json"
                ),
            }
            if recheck
            else None
        ),
        "privacy_status": "DEFERRED_BY_OWNER_UNTIL_LAUNCH",
        "physical_hardware_authorized": False,
    }


def probe(binding, env, operation, folder, plan, *, read_only=False, observer=None):
    from importlib.util import module_from_spec, spec_from_file_location

    require(
        observer is None or (env == "production" and read_only is True),
        "CORRECTED_OBSERVER_IS_READ_ONLY_PRODUCTION_ONLY",
    )
    output = folder / "observations" / operation
    output.mkdir(mode=0o700, exist_ok=False)
    observed = local_identity(binding, env, plan)
    runtime_before = inventory()[TARGET[env]]
    require(
        healthy(runtime_before) and summary(runtime_before) == observed,
        "HOSTED_PROBE_RUNTIME_CHANGED_BEFORE_ACCEPTANCE",
    )
    runtime = backend_runtime(runtime_before)
    request = {
        "binding": binding,
        "environment": env,
        "operation_id": operation,
        "origin": plan["origins"][env],
        "api_origin": plan["api_origins"][env],
        "credentials": credentials(plan, binding)[env]
        if observer is None
        else observer.production_credentials(),
        "read_only": read_only,
        "physical_hardware_authorized": False,
        "probe_image": plan["probe_image"],
        "output": str(output),
        "cpu_limit": probe_cpu_limit(),
    }
    import oracle_robot_frontend as frontend

    provider = frontend.observe_provider(
        binding,
        env,
        plan,
        operation,
        **({"observer": observer} if observer is not None else {}),
    )
    request["provider"] = provider
    if plan.get("identity") is not None:
        import oracle_robot_identity as identity

        request["identity"] = identity.request(plan, binding, env)
        request["identity_api_only"] = (
            env == "production" and base_component(binding) == "api"
        )
    request["expected_frontend_source"] = (
        binding["candidate_commit"]
        if env == "staging" or base_component(binding) == "frontend"
        else plan["retained_frontend"]["source"]
    )
    request["existing_history"] = history(env)
    module_path = private(Path(__file__).with_name("oracle_robot_probe.py"))
    spec = spec_from_file_location("commissioned_robot_probe", module_path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    value = module.execute(request)
    if env == "staging":
        api_binding = plan_binding(plan, "api")
        recovery = schema_recovery(api_binding, component_folder(api_binding))
        schema = journal(
            env,
            "SELECT sql FROM sqlite_master WHERE name IN ('robot_simulation_tasks_v1','robot_simulation_lease_v1') ORDER BY name",
        )
        require(
            hashlib.sha256(canonical(schema)).hexdigest()
            == plan["journal_schema_sha256"][env],
            "REMOTE_JOURNAL_SCHEMA_CHANGED",
        )
        value["schema_compatibility"] = "PASS"
        value["isolated_recovery"] = recovery
    require(
        value["binding"] == binding
        and value["environment"] == env
        and value["operation_id"] == operation
        and value["status"] == "PASS",
        "HOSTED_ROBOT_ACCEPTANCE_FAILED",
    )
    preserved(request["existing_history"], history(env))
    runtime_after = inventory()[TARGET[env]]
    require(
        healthy(runtime_after)
        and summary(runtime_after) == observed
        and backend_runtime(runtime_after) == runtime,
        "HOSTED_PROBE_RUNTIME_CHANGED_DURING_ACCEPTANCE",
    )
    value["actual_runtime"] = observed
    value["backend_runtime"] = runtime
    value["frontend"] = {
        "deployment_id": provider["provider"]["deployment_id"],
        "source_commit": provider["provider"]["source"],
        "origin": provider["origin"],
    }
    if base_component(binding) == "frontend":
        require(
            re.fullmatch(r"dpl_[A-Za-z0-9]{8,128}", value["frontend"]["deployment_id"]),
            "ACTUAL_FRONTEND_DEPLOYMENT_ID_REQUIRED",
        )
        value["deployment_id"] = value["frontend"]["deployment_id"]
    value["existing_records_preserved"] = True
    write(output / "receipt.json", value)
    return value


def provider_receipt(binding, operation, folder, plan):
    op = folder / "operations" / operation
    intent = read(op / "intent.json")
    require(
        intent["binding"] == binding
        and intent["operation_id"] == operation
        and intent["environment"] == "production"
        and intent["provider_request_budget"] == 1
        and intent["automatic_retry"] is False,
        "RECORDED_PROVIDER_INTENT_REQUIRED",
    )
    path = op / "provider-receipt.json"
    actual = inventory()[TARGET["production"]]
    if path.exists():
        receipt = read(path)
        if receipt["deployment_id"] == actual["Id"]:
            require(
                receipt["binding"] == binding and receipt["operation_id"] == operation,
                "DURABLE_PROVIDER_RECEIPT_REQUIRED",
            )
            local_identity(binding, "production", plan)
            return receipt
    # Only observation is allowed after an uncertain request. The original
    # receipt, including a timeout/old instance, is retained without rewriting.
    observed = local_identity(binding, "production", plan)
    require(
        actual["Id"] != intent["before"][TARGET["production"]]["container_id"],
        "REQUEST_HAS_NOT_REPLACED_TARGET",
    )
    created = dt.datetime.fromisoformat(actual["Created"].replace("Z", "+00:00"))
    requested = dt.datetime.fromisoformat(intent["requested_at"])
    require(requested <= created <= now(), "ACTUAL_DEPLOYMENT_PREDATES_INTENT")
    others = {
        n: summary(x) for n, x in inventory().items() if n != TARGET["production"]
    }
    require(
        all(
            others.get(n) == row
            for n, row in intent["before"].items()
            if n != TARGET["production"]
        ),
        "INDEPENDENT_RUNTIME_CHANGED_DURING_INTERRUPTION",
    )
    receipt = {
        "binding": binding,
        "operation_id": operation,
        "environment": "production",
        "deployment_id": actual["Id"],
        "actual": observed,
        "reconciled_read_only": True,
        "observed_at": now().isoformat(),
    }
    reconciliation = op / "read-only-reconciled-receipt.json"
    if reconciliation.exists():
        old = read(reconciliation)
        require(
            old["deployment_id"] == actual["Id"] and old["binding"] == binding,
            "RECONCILED_DEPLOYMENT_CHANGED",
        )
        return old
    write(reconciliation, receipt)
    return receipt


def runner(phase, binding, context, *, observer=None):
    if observer is not None:
        from oracle_robot_recovery import Observer

        require(
            isinstance(observer, Observer) and phase == "production",
            "READ_ONLY_COMMISSIONED_OBSERVER_ONLY",
        )
        observer.validate_invocation(binding, context)
    folder, plan = validate_plan(
        binding, **({"observer": observer} if observer is not None else {})
    )
    chain = verify_invocation(binding, context, phase, plan)
    context_id = next(x["id"] for x in chain if x["kind"] == "context")
    before = inventory()
    import oracle_robot_frontend as frontend

    if phase == "staging":
        preflight(binding)
        preview = frontend.prepare_preview(binding, context, plan)
        plan["preview_receipt_path"] = str(preview)
        frontend.verify_static(
            plan_binding(plan, "frontend"),
            "staging",
            component_folder(plan_binding(plan, "frontend")),
            plan,
            context_id,
        )
        receipt = activate(binding, "staging", context_id, folder, plan)
        value = probe(binding, "staging", context_id, folder, plan)
        value["checks"] = {k: "PASS" for k in CHECKS["staging"]}
        value["deployment_id"] = receipt["deployment_id"]
    elif phase == "operations":
        value = operational_observations(
            binding,
            plan,
            folder,
            context_id,
            qualification=next(
                (item for item in chain if item["kind"] == "qualification"), None
            ),
        )
    elif phase == "deploy":
        receipt = activate(binding, "production", context["operation_id"], folder, plan)
        value = {**receipt, "checks": {k: "PASS" for k in CHECKS["deploy"]}}
    else:
        operation = context["operation_id"]
        receipt = provider_receipt(binding, operation, folder, plan)
        require(
            receipt["binding"] == binding and receipt["operation_id"] == operation,
            "DURABLE_PROVIDER_RECEIPT_REQUIRED",
        )
        # Repeated inspection has a distinct observation directory and no provider
        # request. Never reuse or remove a failed observation to permit a retry.
        inspection = operation + "-" + str(time.time_ns())
        value = probe(
            binding,
            "production",
            inspection,
            folder,
            plan,
            read_only=True,
            **({"observer": observer} if observer is not None else {}),
        )
        value["operation_id"] = operation
        value["deployment_id"] = receipt["deployment_id"]
        require(
            inventory()[TARGET["production"]]["Id"] == receipt["deployment_id"],
            "RUNNING_DEPLOYMENT_DIFFERS_FROM_RECEIPT",
        )
        value["checks"] = {k: "PASS" for k in CHECKS["production"]}
    unchanged(
        before,
        inventory(),
        [TARGET["staging"]]
        if phase == "staging"
        else [TARGET["production"]]
        if phase == "deploy"
        else [],
    )
    return {
        **value,
        "binding": binding,
        "environment": "staging" if phase == "staging" else "production",
        "workflow_run_id": 0,
        "observed_at": now().isoformat(),
    }


def record_failure(phase, request, error):
    """Persist only bounded redacted diagnostics; never reuse an operation."""
    code = (
        str(error)
        if isinstance(error, Refused) and re.fullmatch("[A-Z0-9_]{1,120}", str(error))
        else type(error).__name__
    )
    try:
        binding = request["binding"]
        folder = component_folder(binding)
        target = folder / "failures"
        target.mkdir(mode=0o700, exist_ok=True)
        private(target, directory=True)
        ids = {
            k: v
            for k, v in request["context"].items()
            if k
            in ["context_id", "operation_id", "authorization_id", "qualification_id"]
            and isinstance(v, str)
            and re.fullmatch("[a-f0-9-]{36}", v)
        }
        write(
            target / (phase + "-" + str(time.time_ns()) + ".json"),
            {
                "candidate_commit": binding["candidate_commit"],
                "component": binding["component"],
                "phase": phase,
                "record_ids": ids,
                "failure_code": code,
                "observed_at": now().isoformat(),
                "provider_retry_allowed": False,
            },
        )
    except (Refused, KeyError, ValueError, TypeError, OSError):
        pass
    return code


def main():
    require(
        os.geteuid() == 0 and not os.getenv("GITHUB_ACTIONS"),
        "EXTERNAL_OWNER_RUNNER_ONLY",
    )
    require(
        len(sys.argv) == 2 and sys.argv[1] in [*CHECKS, "preflight"],
        "RUNNER_PHASE_SCOPE",
    )
    raw = sys.stdin.buffer.read(131073)
    require(len(raw) <= 131072, "RUNNER_REQUEST_BOUND")
    request = json.loads(raw)
    require(set(request) == {"binding", "context"}, "RUNNER_REQUEST_SCHEMA")
    try:
        if sys.argv[1] == "preflight":
            require(request["context"] == {}, "PREFLIGHT_CANNOT_ACCEPT_AUTHORITY")
            result = preflight(request["binding"])
        else:
            result = runner(sys.argv[1], request["binding"], request["context"])
    except (
        Refused,
        KeyError,
        ValueError,
        TypeError,
        OSError,
        ImportError,
        subprocess.SubprocessError,
    ) as error:
        code = record_failure(sys.argv[1], request, error)
        raise Refused(code) from None
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        main()
    except (
        Refused,
        KeyError,
        ValueError,
        OSError,
        ImportError,
        subprocess.SubprocessError,
    ):
        raise SystemExit(
            "Robot owner operation refused; inspect private pinned evidence without repeating provider requests."
        ) from None
