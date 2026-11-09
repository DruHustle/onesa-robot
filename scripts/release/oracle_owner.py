"""Owner-run Oracle evidence bridge. Install a reviewed immutable copy outside Git.

This command never accepts a caller's PASS or an Actions run as owner authority.
The commissioned root store signs records with its separate Ed25519 key. Pinned
owner-installed runners perform observations and provider operations. The command
reserves contexts and deployment intents durably before executing those runners.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

OWNER = "DruHustle"
POLICIES = {
    "onesa-company-portal": (
        "verify.yml",
        [
            "verify",
            "pipeline-security / workflow-security",
            "ci-summary",
            "image-security (onesa-company-portal-backend)",
            "image-security (onesa-company-portal-frontend)",
            "image-security (clamav)",
        ],
    ),
    "onesa-ai-platform": (
        "ci.yml",
        [
            "backend",
            "frontend",
            "browser",
            "documentation",
            "containers",
            "tls-acceptance",
            "pipeline-security / workflow-security",
            "ci-summary",
            "image-security (onesa-ai-platform-backend)",
            "image-security (onesa-ai-platform-frontend)",
        ],
    ),
    "onesa-robot": (
        "scaffold.yml",
        [
            "backend",
            "frontend",
            "containers (frontend)",
            "containers (backend)",
            "ros-contracts",
            "pipeline-security / workflow-security",
            "ci-summary",
        ],
    ),
}
PHASES = ("staging", "operations", "deploy", "production")
# Repository identity and independently named application artifacts are separate.
# A repository rename cannot relabel genuine previously produced CI artifacts.
RUNTIME_ARTIFACT_REPOSITORIES = {
    "onesa-company-portal": "onesa-company-portal",
    "onesa-ai-platform": "onesa-ai-platform",
    "onesa-robot": "onesa-ai-robot",
}
REQUIRED_CHECKS = {
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
HEX = re.compile(r"[a-f0-9]{64}")
OPENSSL = "/usr/bin/openssl" if sys.platform == "linux" else "/opt/homebrew/bin/openssl"
ANCHOR = Path("/etc/onesa/oracle-release-trust.json")


# Source preflight remains non-authorizing. Only the explicit Robot schema-2
# application consumer below may use it in a separately commissioned Store.
CONSOLIDATED_REPOSITORY = "DruHustle/onesa-ug"
CONSOLIDATED_REPOSITORY_ID = 1444746154
CONSOLIDATED_PRODUCTS = {
    "portal": "onesa-company-portal",
    "platform": "onesa-ai-platform",
    "robot": "onesa-robot",
    "drone": "onesa-drone",
}
CONSOLIDATED_ARTIFACTS = {
    "portal": {
        "portal-runtime-onesa-company-portal-backend": "record.json",
        "portal-runtime-onesa-company-portal-frontend": "record.json",
        "portal-runtime-clamav": "record.json",
    },
    "platform": {
        "platform-runtime-onesa-ai-platform-backend": "record.json",
        "platform-runtime-onesa-ai-platform-frontend": "record.json",
    },
    "robot": {
        "robot-runtime-onesa-ai-robot-backend": "record.json",
        "robot-runtime-onesa-ai-robot-frontend": "record.json",
    },
    "drone": {
        "drone-runtime-onesa-drone-backend": "backend.json",
        "drone-runtime-onesa-drone-frontend": "frontend.json",
    },
}
CONSOLIDATED_JOB_STEPS = {
    "prepare": ["Check generated verification and regression boundaries"],
    "pipeline-security": ["Check active root workflows and repository secrets"],
    "publication-hygiene": ["Scan every product and root path through preserved source history"],
    "Onesa required verification": ["Reject failed, missing, cancelled or unexpectedly skipped gates"],
    "drone / verification": [
        "Verify built frontend package", "Publish backend provenance", "Publish frontend provenance",
    ],
    "drone / mobile": ["Bundle mobile app and test simulation-only mission contract"],
    "drone / mobile-android": [
        "Compile arm64 Android debug application", "Qualify Android native output and source proof",
        "Retain Android native compile evidence",
    ],
    "drone / mobile-ios": [
        "Compile arm64 iOS simulator without distribution signing", "Qualify iOS native output and source proof",
        "Retain iOS native compile evidence",
    ],
    "portal / changes": [],
    "portal / verify": [
        "qualification failure-path tests", "shared identity hosted observer boundaries",
        "owned worker fixture and TLS mail guards", "shared identity migration boundaries",
        "locked development dependency compatibility", "NVIDIA asset integrity and parser safety",
        "NVIDIA reference gallery browser journeys", "owner probe readiness and NVIDIA observations",
    ],
    "portal / image-security (onesa-company-portal-backend)": [
        "default backend startup and database readiness", "owned worker fixture PostgreSQL and TLS",
        "actual worker process with disposable protocol fixtures",
        "bounded recovery with actual Docker process faults", "record tested runtime digest",
    ],
    "portal / image-security (onesa-company-portal-frontend)": ["record tested runtime digest"],
    "portal / image-security (clamav)": ["record tested runtime digest"],
    "platform / changes": [],
    "platform / backend": ["qualification failure-path tests"],
    "platform / frontend": [
        "private Docs publisher dependency lock", "private Docs Linux single-dispatch protocol",
    ],
    "platform / browser": [],
    "platform / documentation": ["private Docs native asset refusals"],
    "platform / containers": [],
    "platform / tls-acceptance": [],
    "platform / image-security (onesa-ai-platform-backend)": ["record tested runtime digest"],
    "platform / image-security (onesa-ai-platform-frontend)": ["record tested runtime digest"],
    "robot / changes": [],
    "robot / backend": ["qualification failure-path tests"],
    "robot / frontend": [],
    "robot / containers (backend)": ["record tested runtime digest"],
    "robot / containers (frontend)": ["record tested runtime digest"],
    "robot / ros-contracts": [],
}
for _product in ("portal", "platform", "robot"):
    CONSOLIDATED_JOB_STEPS[_product + " / pipeline-security / workflow-security"] = [
        "workflow and secret security", "pipeline negative tests",
    ]
    CONSOLIDATED_JOB_STEPS[_product + " / ci-summary"] = ["required checks summary"]
CONSOLIDATED_JOB_STEPS = {
    job: [("Release requirement: " + step if " / " in job else step) for step in steps]
    for job, steps in CONSOLIDATED_JOB_STEPS.items()
}
# Billing is a required verification component, never an imported release product.
# Its generated workflow uses these literal names without the legacy prefix.
CONSOLIDATED_JOB_STEPS["billing / verify"] = [
    "Verify hosted execution, visibility and included-allowance policy",
    "Configure the fresh local billing test database",
    "Install locked company billing dependencies",
    "Typecheck and build the independent billing backend",
    "Verify auth, provider mocks, PostgreSQL isolation, concurrency and recovery",
    "Verify browser client boundaries and exact currency presentation",
    "Verify the actual browser journey with synthetic same-origin APIs",
]
CONSOLIDATED_WORKFLOWS = {
    "_verify-" + product + ".yml" for product in CONSOLIDATED_PRODUCTS
} | {"_pipeline-" + product + ".yml" for product in ("portal", "platform", "robot")} | {"_verify-billing.yml"}
SOURCE_ARCHIVE_LIMIT = 24 * 1024
SOURCE_MANIFEST_LIMIT = 64 * 1024


class Rejected(ValueError):
    pass


def require(value, reason):
    if not value:
        raise Rejected(reason)


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc)


def identifier(value):
    require(
        isinstance(value, str) and str(UUID(value)) == value,
        "Canonical record UUID required",
    )
    return value


def private(path, *, directory=False, uid=0):
    path = Path(path)
    require(
        path.is_absolute() and not any(p.is_symlink() for p in [path, *path.parents]),
        "Custody path alias",
    )
    s = path.stat()
    require(
        s.st_uid == uid and s.st_mode & 0o077 == 0, "Private owner custody required"
    )
    require(
        stat.S_ISDIR(s.st_mode)
        if directory
        else stat.S_ISREG(s.st_mode) and s.st_nlink == 1,
        "Custody file type",
    )
    if uid == 0:
        require(
            all(
                p.stat().st_uid == 0 and p.stat().st_mode & 0o022 == 0
                for p in path.parents
            ),
            "Production custody ancestors must be root-owned and non-writable",
        )


def signed_bytes(key, public, payload=None, signature=None):
    """OpenSSL Ed25519; key material is never returned or passed on the command line."""
    with tempfile.TemporaryDirectory(prefix=".signature-", dir=public.parent) as folder:
        folder = Path(folder)
        source, sig = folder / "record", folder / "signature"
        source.write_bytes(payload)
        source.chmod(0o600)
        if signature is None:
            args = [OPENSSL, "pkeyutl", "-sign", "-rawin", "-inkey", str(key)]
        else:
            sig.write_bytes(signature)
            sig.chmod(0o600)
            args = [
                OPENSSL,
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(public),
                "-sigfile",
                str(sig),
            ]
        r = subprocess.run(
            [*args, "-in", str(source)], capture_output=True, timeout=15, check=False
        )
        require(r.returncode == 0, "Record signature invalid or unavailable")
        return r.stdout


def consolidated_source_profile(source, policy):
    """Require a separately pinned product policy; never infer a legacy scope."""
    fields = {
        "schema_version", "repository", "repository_id", "product", "source_path",
        "candidate_commit", "source_tree", "product_source_tree", "branch",
    }
    require(isinstance(source, dict) and set(source) == fields, "Complete source-only binding required")
    product = source["product"]
    require(isinstance(product, str) and product in CONSOLIDATED_PRODUCTS, "Unknown consolidated product")
    require(type(source["schema_version"]) is int and source["schema_version"] == 2
            and source["repository"] == CONSOLIDATED_REPOSITORY
            and type(source["repository_id"]) is int
            and source["repository_id"] == CONSOLIDATED_REPOSITORY_ID
            and source["source_path"] == CONSOLIDATED_PRODUCTS[product]
            and isinstance(source["branch"], str)
            and source["branch"] in {"staging", "main"},
            "Consolidated source identity refused")
    require(all(isinstance(source[key], str) and re.fullmatch(r"[a-f0-9]{40}", source[key])
                for key in ("candidate_commit", "source_tree", "product_source_tree")),
            "Exact consolidated Git objects required")
    profiles = policy.get("consolidated_sources")
    require(isinstance(profiles, dict) and product in profiles, "Consolidated product is not commissioned")
    profile = profiles[product]
    require(isinstance(profile, dict)
            and set(profile) == {"repository", "repository_id", "source_path", "branches"}
            and profile["repository"] == source["repository"]
            and type(profile["repository_id"]) is int
            and profile["repository_id"] == source["repository_id"]
            and profile["source_path"] == source["source_path"],
            "Commissioned source scope differs")
    branches = profile["branches"]
    require(isinstance(branches, list) and 1 <= len(branches) <= 2
            and all(isinstance(branch, str) and branch in {"staging", "main"} for branch in branches)
            and len(set(branches)) == len(branches) and source["branch"] in branches,
            "Source branch is not commissioned")
    return profile


def source_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate source manifest field")
            result[key] = value
        return result

    def finite(_value):
        raise Rejected("Nonfinite source manifest value")

    return json.loads(raw, object_pairs_hook=unique, parse_constant=finite)


def source_manifest(raw, source, member):
    """Inspect bounded JSON only. Never extract or execute Actions artifacts."""
    record = source_json(raw)
    require(isinstance(record, dict)
            and record.get("repository") == source["repository"]
            and record.get("source_path") == source["source_path"]
            and record.get("product_source_tree") == source["product_source_tree"]
            and record.get("verification_only") is True
            and record.get("release_authorized") is False,
            "Source manifest scope or authority differs")
    common = {
        "repository", "source_path", "product_source_tree", "verification_only", "release_authorized",
    }
    image = None
    if source["product"] != "drone":
        require(set(record) == common | {"schema_version", "candidate_commit", "image_digest", "security"}
                and type(record["schema_version"]) is int and record["schema_version"] == 2
                and record["candidate_commit"] == source["candidate_commit"]
                and record["security"] == "PASS", "Tested image source manifest differs")
        image = record["image_digest"]
    else:
        common |= {"source_commit", "source_tree", "component", "mode"}
        component = member.removesuffix(".json")
        extra = {"image_id", "runtime_image_id", "architecture"} if component == "backend" else {"files"}
        require(set(record) == common | extra
                and record["source_commit"] == source["candidate_commit"]
                and record["source_tree"] == source["source_tree"]
                and record["component"] == component and record["mode"] == "simulation",
                "Drone source manifest differs")
        if component == "backend":
            image = record["image_id"]
            require(record["architecture"] in {"amd64", "arm64"}
                    and isinstance(record["runtime_image_id"], str)
                    and re.fullmatch(r"sha256:[a-f0-9]{64}", record["runtime_image_id"]),
                    "Drone runtime image identity differs")
        else:
            files = record["files"]
            require(isinstance(files, dict) and 1 <= len(files) <= 2048 and "index.html" in files,
                    "Drone frontend manifest incomplete")
            for path, value in files.items():
                require(isinstance(path, str) and 0 < len(path) <= 512
                        and all(32 <= ord(character) < 127 for character in path)
                        and "\\" not in path and ":" not in path
                        and all(part not in {"", ".", ".."} for part in path.split("/"))
                        and isinstance(value, str) and HEX.fullmatch(value),
                        "Drone frontend file identity refused")
    require(image is None or isinstance(image, str) and re.fullmatch(r"sha256:[a-f0-9]{64}", image),
            "Tested image digest missing")
    result = {"manifest_sha256": hashlib.sha256(raw).hexdigest()}
    if image is not None:
        result["image_digest"] = image
    return result


def source_artifact(encoded_archive, metadata, source, member):
    """Bind supplied archive bytes to fresh GitHub metadata before parsing them."""
    require(isinstance(encoded_archive, str) and 0 < len(encoded_archive) <= SOURCE_ARCHIVE_LIMIT * 4 // 3,
            "Bounded source artifact bytes required")
    try:
        raw = base64.b64decode(encoded_archive, validate=True)
        require(0 < len(raw) <= SOURCE_ARCHIVE_LIMIT
                and len(raw) == metadata["size_in_bytes"]
                and "sha256:" + hashlib.sha256(raw).hexdigest() == metadata["digest"],
                "Source artifact bytes differ from GitHub")
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            require(len(entries) == 1, "Exactly one source manifest member required")
            item = entries[0]
            require(item.filename == member and not item.flag_bits & 1
                    and item.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                    and stat.S_IFMT(item.external_attr >> 16) in {0, stat.S_IFREG}
                    and 0 < item.file_size <= SOURCE_MANIFEST_LIMIT,
                    "Source artifact member refused")
            with archive.open(item) as stream:
                manifest = stream.read(SOURCE_MANIFEST_LIMIT + 1)
            require(len(manifest) == item.file_size, "Source manifest size differs")
        result = source_manifest(manifest, source, member)
    except (ValueError, TypeError, KeyError, OSError, UnicodeError, zipfile.BadZipFile, RuntimeError):
        raise Rejected("Source artifact verification failed") from None
    return {
        "artifact_id": metadata["id"], "name": metadata["name"],
        "artifact_digest": metadata["digest"], **result,
    }


def complete_source_listing(value, key):
    rows = value.get(key)
    require(isinstance(rows, list) and type(value.get("total_count")) is int
            and value["total_count"] == len(rows) <= 100
            and all(isinstance(row, dict) and type(row.get("id")) is int and row["id"] > 0 for row in rows)
            and len({row["id"] for row in rows}) == len(rows),
            "Complete unambiguous source evidence listing required")
    return rows


class GitHub:
    def __init__(self, token):
        require(
            isinstance(token, str) and token and "\n" not in token,
            "Existing owner token required in private stdin",
        )
        self.token = token

    def api(self, path):
        require(
            path == "user" or path.startswith("repos/" + OWNER + "/"),
            "Foreign GitHub path",
        )
        request = urllib.request.Request(
            "https://api.github.com/" + path,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "User-Agent": "OnesaOracleOwnerBridge/1",
            },
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=25
        ) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        require(len(raw) <= 8 * 1024 * 1024, "GitHub response bound")
        return json.loads(raw)

    def owner(self):
        require(not os.getenv("GITHUB_ACTIONS"), "Owner custody cannot run in Actions")
        require(
            self.api("user").get("login") == OWNER,
            "Current authenticated owner required",
        )
        return OWNER

    def verify_source(self, source, archives):
        """Read source/CI evidence only; this cannot create an operational receipt."""
        repo, sha, branch = source["repository"], source["candidate_commit"], source["branch"]
        prefix = "repos/" + repo + "/"

        def same_repo(value):
            return (isinstance(value, dict) and type(value.get("id")) is int
                    and value["id"] == source["repository_id"] and value.get("full_name") == repo)

        require(same_repo(self.api(prefix.rstrip("/"))), "Consolidated repository identity changed")
        require(self.api(prefix + "branches/" + branch)["commit"]["sha"] == sha,
                "Current exact source branch required")
        commit = self.api(prefix + "git/commits/" + sha)
        require(commit["sha"] == sha and commit["tree"]["sha"] == source["source_tree"],
                "Consolidated root tree differs")
        tree = self.api(prefix + "git/trees/" + source["source_tree"])
        entries = [row for row in tree["tree"] if row.get("path") == source["source_path"]]
        require(tree.get("truncated") is False and tree.get("sha") == source["source_tree"]
                and len(entries) == 1 and entries[0].get("mode") == "040000"
                and entries[0].get("type") == "tree"
                and entries[0].get("sha") == source["product_source_tree"],
                "Consolidated product subtree differs")
        candidates = self.api(prefix + "commits/" + sha + "/pulls?per_page=100")
        require(isinstance(candidates, list) and len(candidates) < 100,
                "Complete source review listing required")
        reviewed = []
        for candidate in candidates:
            if candidate.get("merge_commit_sha") != sha or candidate.get("base", {}).get("ref") != branch:
                continue
            number = candidate.get("number")
            require(type(number) is int and number > 0, "Source review identity invalid")
            review = self.api(prefix + "pulls/" + str(number))
            if (review.get("number") == number and review.get("merged") is True
                    and review.get("state") == "closed" and review.get("draft") is False
                    and review.get("merged_at") and review.get("merge_commit_sha") == sha
                    and review.get("merged_by", {}).get("login") == OWNER
                    and review.get("base", {}).get("ref") == branch
                    and review.get("head", {}).get("ref") == ("dev" if branch == "staging" else "staging")
                    and same_repo(review.get("base", {}).get("repo"))
                    and same_repo(review.get("head", {}).get("repo"))):
                reviewed.append(number)
        require(len(reviewed) == 1, "Exact owner-merged normal source promotion required")
        def latest_source_run():
            rows = complete_source_listing(
                self.api(prefix + "actions/workflows/verify.yml/runs?head_sha=" + sha + "&per_page=100"),
                "workflow_runs",
            )
            eligible = [row for row in rows if row.get("event") in {"push", "workflow_dispatch"}
                        and row.get("head_branch") == branch and row.get("head_sha") == sha]
            require(eligible, "Exact consolidated branch CI missing")
            observed = timestamp()
            times = {}
            for row in eligible:
                try:
                    created = datetime.fromisoformat(row["created_at"])
                    updated = datetime.fromisoformat(row["updated_at"])
                    require(created.utcoffset() is not None and updated.utcoffset() is not None
                            and created <= updated <= observed
                            and type(row.get("run_attempt")) is int and row["run_attempt"] >= 1,
                            "Consolidated CI attempt chronology invalid")
                except (TypeError, ValueError, KeyError):
                    raise Rejected("Consolidated CI attempt chronology invalid") from None
                times[row["id"]] = (created, updated)
            selected = max(eligible, key=lambda row: row["id"])
            # GitHub retains a run ID when rerunning it. Older IDs with later
            # activity must not hide behind a newer first-attempt success.
            require(all(row["id"] == selected["id"] or row.get("status") == "completed"
                        and times[row["id"]][1] < times[selected["id"]][0] for row in eligible),
                    "Overlapping or repeated CI activity requires a fresh first-attempt run")
            return selected

        latest = latest_source_run()
        run = self.api(prefix + "actions/runs/" + str(latest["id"]))
        run_state = ("id", "event", "head_sha", "head_branch", "run_attempt", "status",
                     "conclusion", "created_at", "updated_at")
        require(all(run.get(key) == latest.get(key) for key in run_state)
                and same_repo(run.get("repository"))
                and same_repo(run.get("head_repository"))
                and run.get("head_sha") == sha and run.get("head_branch") == branch
                and run.get("event") == latest["event"]
                and run.get("created_at") == latest["created_at"]
                and run.get("actor", {}).get("login") == OWNER
                and run.get("triggering_actor", {}).get("login") == OWNER
                and run.get("path") == ".github/workflows/verify.yml"
                and type(run.get("run_attempt")) is int and run["run_attempt"] == 1
                and run.get("status") == "completed" and run.get("conclusion") == "success",
                "Latest consolidated first-attempt owner CI failed or incomplete")
        try:
            require(timedelta(0) <= timestamp() - datetime.fromisoformat(run["updated_at"]) < timedelta(hours=24),
                    "Consolidated source evidence expired")
        except (TypeError, ValueError):
            raise Rejected("Consolidated source evidence expired") from None
        expected_workflows = {
            (repo + "/.github/workflows/" + name + "@" + sha, sha, "refs/heads/" + branch)
            for name in CONSOLIDATED_WORKFLOWS
        }
        referenced = run.get("referenced_workflows")
        require(isinstance(referenced, list) and len(referenced) == len(expected_workflows)
                and {(row.get("path"), row.get("sha"), row.get("ref")) for row in referenced} == expected_workflows,
                "Reusable workflow source identities differ")
        jobs = complete_source_listing(
            self.api(prefix + "actions/runs/" + str(run["id"]) + "/jobs?filter=latest&per_page=100"), "jobs",
        )
        require(len(jobs) == len(CONSOLIDATED_JOB_STEPS)
                and {job.get("name") for job in jobs} == set(CONSOLIDATED_JOB_STEPS),
                "Mandatory consolidated CI jobs differ")
        for job in jobs:
            require(job.get("run_id") == run["id"] and job.get("head_sha") == sha
                    and job.get("head_branch") == branch and job.get("run_attempt") == 1
                    and job.get("status") == "completed" and job.get("conclusion") == "success",
                    "Mandatory consolidated CI job failed/skipped")
            steps = job.get("steps")
            require(isinstance(steps, list) and steps
                    and all(isinstance(step, dict) and isinstance(step.get("name"), str) for step in steps),
                    "Consolidated CI step evidence missing")
            for name in CONSOLIDATED_JOB_STEPS[job["name"]]:
                matched = [step for step in steps if step["name"] == name]
                require(len(matched) == 1 and matched[0].get("status") == "completed"
                        and matched[0].get("conclusion") == "success",
                        "Mandatory consolidated CI step failed/skipped/missing")
            require(all(step.get("status") == "completed"
                        and step.get("conclusion") in {"success", "skipped"}
                        and (not step["name"].startswith("Release requirement:")
                             or step["conclusion"] == "success") for step in steps),
                    "Consolidated CI contains a failed or incomplete step")
        artifacts = complete_source_listing(
            self.api(prefix + "actions/runs/" + str(run["id"]) + "/artifacts?per_page=100"), "artifacts",
        )
        expected = CONSOLIDATED_ARTIFACTS[source["product"]]
        require(isinstance(archives, dict) and set(archives) == set(expected),
                "Exact product artifact archives required")
        evidence = []
        for name, member in expected.items():
            matching = [item for item in artifacts if item.get("name") == name]
            require(len(matching) == 1, "Source artifact missing or duplicated")
            item = matching[0]
            require(item.get("expired") is False and type(item.get("size_in_bytes")) is int
                    and 0 < item["size_in_bytes"] <= SOURCE_ARCHIVE_LIMIT
                    and isinstance(item.get("digest"), str) and re.fullmatch(r"sha256:[a-f0-9]{64}", item["digest"])
                    and item.get("workflow_run") == {
                        "id": run["id"], "repository_id": source["repository_id"],
                        "head_repository_id": source["repository_id"], "head_branch": branch, "head_sha": sha,
                    }, "Source artifact metadata differs or expired")
            evidence.append(source_artifact(archives[name], item, source, member))
        # Observations span several API reads. A branch advance, newer run or
        # rerun during artifact verification must invalidate this snapshot.
        current_latest = latest_source_run()
        require(all(current_latest.get(key) == run.get(key) for key in run_state),
                "Newer or changed consolidated CI requires fresh evidence")
        current_run = self.api(prefix + "actions/runs/" + str(run["id"]))
        require(same_repo(current_run.get("repository")) and same_repo(current_run.get("head_repository"))
                and current_run.get("actor", {}).get("login") == OWNER
                and current_run.get("triggering_actor", {}).get("login") == OWNER
                and type(current_run.get("run_attempt")) is int
                and all(current_run.get(key) == run.get(key) for key in (
            "id", "head_sha", "head_branch", "event", "path", "run_attempt", "status", "conclusion",
            "created_at", "updated_at", "referenced_workflows",
        )), "Consolidated CI changed during source verification")
        require(timedelta(0) <= timestamp() - datetime.fromisoformat(run["updated_at"]) < timedelta(hours=24),
                "Consolidated source evidence expired during verification")
        require(self.api(prefix + "branches/" + branch)["commit"]["sha"] == sha,
                "Source branch changed during verification")
        return {
            "source": source, "review_number": reviewed[0], "run_id": run["id"], "run_attempt": 1,
            "workflow": ".github/workflows/verify.yml", "artifacts": evidence,
            "verification_only": True, "release_authorized": False,
        }

    def qualify(self, binding, archives=None):
        if binding.get("repository") == CONSOLIDATED_REPOSITORY:
            source = robot_operational_source(binding)
            # Unsigned source evidence; the commissioned Store alone issues context.
            return [self.verify_source(source, archives)]
        require(
            archives is None, "Legacy qualification does not accept source archives"
        )
        repo, sha = binding["repository"], binding["candidate_commit"]
        prefix = "repos/" + repo + "/"
        require(
            self.api(prefix + "branches/main")["commit"]["sha"] == sha,
            "Current exact main required",
        )
        require(
            self.api(prefix + "git/commits/" + sha)["tree"]["sha"]
            == binding["source_tree"],
            "Source tree differs from qualified commit",
        )
        prs = self.api(prefix + "commits/" + sha + "/pulls?per_page=100")
        require(
            any(
                p.get("merged_at")
                and p.get("merge_commit_sha") == sha
                and p.get("base", {}).get("ref") == "main"
                and p.get("merged_by", {}).get("login") == OWNER
                for p in [self.api(prefix + "pulls/" + str(x["number"])) for x in prs]
            ),
            "Owner-merged main PR required",
        )
        workflow, jobs = POLICIES[repo.split("/")[1]]
        references = []
        for name, required in [
            (workflow, jobs),
            ("repository-hygiene.yml", ["publication-hygiene"]),
        ]:
            listing = self.api(
                prefix
                + "actions/workflows/"
                + name
                + "/runs?head_sha="
                + sha
                + "&per_page=100"
            )
            runs = listing["workflow_runs"]
            require(
                type(listing.get("total_count")) is int
                and listing["total_count"] == len(runs)
                and len(runs) <= 100,
                "Complete exact-source CI run listing required",
            )
            pushes = [
                r for r in runs if r["event"] == "push" and r["head_branch"] == "main"
            ]
            runs = sorted(
                [
                    r
                    for r in (pushes or runs)
                    if r["event"] in {"push", "workflow_dispatch"}
                    and r["head_branch"] == "main"
                ],
                key=lambda r: r["id"],
                reverse=True,
            )
            require(runs, "Exact-main CI missing")
            run = self.api(prefix + "actions/runs/" + str(runs[0]["id"]))
            require(
                run["repository"]["full_name"] == repo
                and run["head_repository"]["full_name"] == repo
                and run["head_sha"] == sha
                and run["head_branch"] == "main"
                and run["event"] == runs[0]["event"]
                and (
                    run["event"] == "push"
                    or (
                        (run.get("actor") or {}).get("login") == OWNER
                        and (run.get("triggering_actor") or {}).get("login") == OWNER
                    )
                )
                and run["path"] == ".github/workflows/" + name
                and run["run_attempt"] == 1
                and run["status"] == "completed"
                and run["conclusion"] == "success",
                "Exact-main first-attempt owner CI failed or incomplete",
            )
            require(
                timedelta(0)
                <= timestamp() - datetime.fromisoformat(run["updated_at"])
                < timedelta(hours=24),
                "Exact-main CI evidence expired",
            )
            result = self.api(
                prefix
                + "actions/runs/"
                + str(run["id"])
                + "/jobs?filter=latest&per_page=100"
            )
            require(
                result["total_count"] <= 100,
                "CI job pagination requires explicit review",
            )
            by_name = {j["name"]: j for j in result["jobs"]}
            for job in required:
                require(
                    job in by_name
                    and by_name[job]["status"] == "completed"
                    and by_name[job]["conclusion"] == "success",
                    "Mandatory CI job failed/skipped/missing",
                )
                require(
                    all(
                        s["conclusion"] == "success"
                        for s in by_name[job].get("steps", [])
                        if s["name"].startswith("Release requirement:")
                    ),
                    "Mandatory CI step failed/skipped",
                )
            artifacts = self.api(
                prefix + "actions/runs/" + str(run["id"]) + "/artifacts?per_page=100"
            )
            require(
                artifacts["total_count"] <= 100,
                "CI artifact pagination requires explicit review",
            )
            manifests = [
                {k: a[k] for k in ["id", "name", "digest"]}
                for a in artifacts["artifacts"]
                if a["name"].startswith("runtime-")
                and not a["expired"]
                and re.fullmatch(r"sha256:[a-f0-9]{64}", a.get("digest", ""))
            ]
            if name == workflow:
                require(
                    {
                        "runtime-"
                        + RUNTIME_ARTIFACT_REPOSITORIES[repo.split("/")[1]]
                        + "-"
                        + x
                        for x in ["backend", "frontend"]
                    }
                    <= {a["name"] for a in manifests},
                    "Genuine tested image manifests required",
                )
            references.append(
                {
                    "run_id": run["id"],
                    "event": run["event"],
                    "workflow": name,
                    "candidate_commit": sha,
                    "run_attempt": 1,
                    "artifacts": manifests,
                }
            )
        return references


ROBOT_SOURCE_FIELDS = {
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
ROBOT_NATIVE_COMPONENTS = {"api", "frontend"}
ROBOT_COMPONENTS = ROBOT_NATIVE_COMPONENTS | {"identity-api", "identity-frontend"}


def robot_operational_source(value):
    require(
        isinstance(value, dict) and ROBOT_SOURCE_FIELDS <= set(value),
        "Complete canonical Robot source identity required",
    )
    source = {key: value[key] for key in ROBOT_SOURCE_FIELDS}
    require(
        type(source["schema_version"]) is int
        and source["schema_version"] == 2
        and source["repository"] == CONSOLIDATED_REPOSITORY
        and type(source["repository_id"]) is int
        and source["repository_id"] == CONSOLIDATED_REPOSITORY_ID
        and source["product"] == "robot"
        and source["source_path"] == CONSOLIDATED_PRODUCTS["robot"]
        and source["branch"] == "main"
        and value.get("component", "api") in ROBOT_COMPONENTS
        and all(
            isinstance(source[key], str) and re.fullmatch(r"[a-f0-9]{40}", source[key])
            for key in ("candidate_commit", "source_tree", "product_source_tree")
        ),
        "Canonical exact-main Robot application scope refused",
    )
    return source


def component_scope(binding):
    if binding.get("repository") == CONSOLIDATED_REPOSITORY:
        robot_operational_source(binding)
        return CONSOLIDATED_REPOSITORY + ":robot:" + binding["component"]
    return binding["repository"] + ":" + binding["component"]


def robot_archive_pins(tool):
    pins = tool.get("source_archives")
    require(
        isinstance(pins, dict)
        and set(pins) == set(CONSOLIDATED_ARTIFACTS["robot"])
        and all(isinstance(pin, str) and HEX.fullmatch(pin) for pin in pins.values()),
        "All exact Robot source artifact archives must be pinned",
    )
    return pins


def robot_source_archives(directory, pins, *, uid=0):
    """Read fixed installed public evidence; no fetch, extraction or authority."""
    directory = Path(directory)
    private(directory, directory=True, uid=uid)
    require(
        set(pins) == set(CONSOLIDATED_ARTIFACTS["robot"]),
        "Exact Robot source archive names required",
    )
    archives = {}
    for name, pin in pins.items():
        path = directory / (name + ".zip")
        private(path, uid=uid)
        require(
            0 < path.stat().st_size <= SOURCE_ARCHIVE_LIMIT,
            "Installed source archive exceeds bound",
        )
        data = path.read_bytes()
        require(
            isinstance(pin, str)
            and HEX.fullmatch(pin)
            and hashlib.sha256(data).hexdigest() == pin,
            "Installed source archive changed",
        )
        archives[name] = base64.b64encode(data).decode()
    return archives


def binding_valid(value, policy):
    consolidated = (
        isinstance(value, dict) and value.get("repository") == CONSOLIDATED_REPOSITORY
    )
    fields = {
        "repository",
        "component",
        "candidate_commit",
        "source_tree",
        "source_sha256",
        "build_sha256",
        "artifact_digest",
        "configuration_sha256",
        "target_environment",
    }
    require(
        isinstance(value, dict)
        and set(value) == fields | (ROBOT_SOURCE_FIELDS if consolidated else set()),
        "Complete exact component binding required",
    )
    require(
        (consolidated or value["repository"] in {OWNER + "/" + r for r in POLICIES})
        and value["target_environment"] == "production",
        "Repository/target scope refused",
    )
    require(
        re.fullmatch(r"[a-f0-9]{40}", value["candidate_commit"])
        and re.fullmatch(r"[a-f0-9]{40}", value["source_tree"]),
        "Exact source identity required",
    )
    require(
        all(HEX.fullmatch(value[k]) for k in ["source_sha256", "build_sha256"]),
        "Source/build digest required",
    )
    require(
        re.fullmatch(r"sha256:[a-f0-9]{64}", value["artifact_digest"]),
        "Immutable artifact digest required",
    )
    require(
        set(value["configuration_sha256"]) == {"staging", "production"}
        and all(HEX.fullmatch(x) for x in value["configuration_sha256"].values()),
        "Separate configuration bindings required",
    )
    if consolidated:
        source = robot_operational_source(value)
        tool = policy.get("toolchain", {})
        require(
            type(policy.get("schema_version")) is int
            and policy["schema_version"] == 2
            and tool.get("source_binding") == source
            and tool.get("source_commit") == value["candidate_commit"]
            and tool.get("source_tree") == value["source_tree"]
            and tool.get("source_sha256") == value["source_sha256"],
            "Canonical Robot component source has not been commissioned",
        )
        robot_archive_pins(tool)
        selected = next(
            (
                names
                for names in (ROBOT_NATIVE_COMPONENTS, ROBOT_COMPONENTS)
                if set(policy.get("components", {}))
                == {CONSOLIDATED_REPOSITORY + ":robot:" + name for name in names}
            ),
            None,
        )
        require(selected is not None, "Canonical Robot literal profile set required")
        for name in selected:
            item = policy["components"][CONSOLIDATED_REPOSITORY + ":robot:" + name]
            require(
                type(item.get("qualification_attempt_limit")) is int
                and item["qualification_attempt_limit"] == 1,
                "One canonical context per profile and source required",
            )

    profile = policy["components"].get(component_scope(value))
    require(
        profile and set(profile["runners"]) == set(PHASES),
        "Component is not commissioned",
    )
    require(
        isinstance(profile.get("qualification_attempt_limit"), int)
        and 1 <= profile["qualification_attempt_limit"] <= 100,
        "Explicit finite owner qualification allowance required",
    )
    require(
        all(profile["required_checks"].get(p) for p in PHASES),
        "Complete qualification policy required",
    )
    for phase in PHASES:
        checks, runner = profile["required_checks"][phase], profile["runners"][phase]
        require(
            isinstance(checks, list)
            and len(checks) == len(set(checks))
            and REQUIRED_CHECKS[phase] <= set(checks),
            "Mandatory release checks cannot be removed",
        )
        require(
            HEX.fullmatch(runner["sha256"]) and 1 <= runner["timeout_seconds"] <= 3600,
            "Pinned bounded runner required",
        )
    return profile


def commissioned(registry, root, trust):
    anchor = registry.get("installations", {}).get(str(root))
    require(
        registry.get("schema_version") == 1
        and anchor
        and anchor.get("active") is True
        and all(
            anchor.get(k) == trust[k]
            for k in ["installation_id", "key_id", "verifier_sha256", "policy_sha256"]
        ),
        "Installation has not been explicitly commissioned",
    )


class Store:
    def __init__(self, root, github, *, uid=0, clock=timestamp):
        self.root, self.github, self.clock = Path(root), github, clock
        self.uid = uid
        private(self.root, directory=True, uid=uid)
        for name in [
            "trust.json",
            "policy.json",
            "signing.pem",
            "public.pem",
            "records.sqlite",
        ]:
            private(self.root / name, uid=uid)
        self.trust = json.loads((self.root / "trust.json").read_text())
        require(
            self.trust["verifier_sha256"] == file_digest(__file__)
            and self.trust["policy_sha256"] == file_digest(self.root / "policy.json")
            and self.trust["key_id"] == file_digest(self.root / "public.pem")
            and self.trust["openssl_sha256"] == file_digest(OPENSSL),
            "Commissioned verifier/policy/key pin changed",
        )
        self.policy = json.loads((self.root / "policy.json").read_text())
        if uid == 0:
            private(ANCHOR)
            commissioned(json.loads(ANCHOR.read_text()), self.root, self.trust)
        self.db = sqlite3.connect(
            self.root / "records.sqlite", timeout=20, isolation_level=None
        )
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.github.owner()

    def verify_source(self, source, archives):
        """Use commissioned custody for a read-only proof; issue no signed record."""
        consolidated_source_profile(source, self.policy)
        result = self.github.verify_source(source, archives)
        return {
            **result, "checked_at": self.clock().isoformat(),
            "installation_id": self.trust["installation_id"],
            "verifier_sha256": self.trust["verifier_sha256"],
            "policy_sha256": self.trust["policy_sha256"],
        }

    def qualify_source(self, binding):
        if binding.get("repository") != CONSOLIDATED_REPOSITORY:
            return self.github.qualify(binding)
        binding_valid(binding, self.policy)
        archives = robot_source_archives(
            Path(__file__).parent / "source-artifacts",
            robot_archive_pins(self.policy["toolchain"]),
            uid=self.uid,
        )
        return self.github.qualify(binding, archives)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def put(self, kind, binding, details, *, parent=None, ttl=3600):
        require(60 <= ttl <= 86400, "Bounded record lifetime required")
        record = {
            "schema_version": 1,
            "id": str(uuid4()),
            "kind": kind,
            "binding": binding,
            "workflow_run_id": 0,
            "execution_context": "owner-operated-oracle-staging"
            if kind in ["context", "qualification"]
            else "owner-operated-oracle-release",
            "issued_at": self.clock().isoformat(),
            "expires_at": (self.clock() + timedelta(seconds=ttl)).isoformat(),
            "parent": parent,
            "owner_provenance": {
                "mechanism": "commissioned-pinned-OpenSSH-and-authenticated-owner",
                "owner": OWNER,
                "installation_id": self.trust["installation_id"],
                "verifier_sha256": self.trust["verifier_sha256"],
            },
            "details": details,
        }
        signature = signed_bytes(
            self.root / "signing.pem", self.root / "public.pem", canonical(record)
        )
        envelope = {
            "record": record,
            "signature": {
                "algorithm": "Ed25519",
                "key_id": self.trust["key_id"],
                "value": base64.b64encode(signature).decode(),
            },
        }
        self.db.execute(
            "INSERT INTO records(id,kind,scope,envelope,parent,created) VALUES(?,?,?,?,?,?)",
            (
                record["id"],
                kind,
                component_scope(binding),
                canonical(envelope).decode(),
                parent,
                record["issued_at"],
            ),
        )
        return envelope

    def get(self, id_, kind=None, binding=None, *, historical=False):
        identifier(id_)
        row = self.db.execute(
            "SELECT envelope,revoked,parent,kind,scope,created FROM records WHERE id=?",
            (id_,),
        ).fetchone()
        require(
            row is not None and (historical or not row[1]), "Record missing/revoked"
        )
        envelope = json.loads(row[0])
        r, s = envelope["record"], envelope["signature"]
        require(
            s["algorithm"] == "Ed25519" and s["key_id"] == self.trust["key_id"],
            "Foreign signer",
        )
        signed_bytes(
            None,
            self.root / "public.pem",
            canonical(r),
            base64.b64decode(s["value"], validate=True),
        )
        require(
            r["schema_version"] == 1
            and r["id"] == id_
            and r["kind"] == row[3]
            and r["parent"] == row[2]
            and r["issued_at"] == row[5]
            and component_scope(r["binding"]) == row[4],
            "Signed record and custody index differ",
        )
        issued, expiry = (
            datetime.fromisoformat(r["issued_at"]),
            datetime.fromisoformat(r["expires_at"]),
        )
        require(
            expiry - issued <= timedelta(hours=24)
            and (historical or issued <= self.clock() < expiry),
            "Record expired/not yet valid",
        )
        require(
            r["workflow_run_id"] == 0
            and r["owner_provenance"]["installation_id"]
            == self.trust["installation_id"]
            and r["owner_provenance"]["verifier_sha256"]
            == self.trust["verifier_sha256"]
            and r["owner_provenance"]["owner"] == OWNER
            and r["owner_provenance"]["mechanism"]
            == "commissioned-pinned-OpenSSH-and-authenticated-owner",
            "Owner-run provenance missing",
        )
        require(
            (kind is None or r["kind"] == kind)
            and (binding is None or r["binding"] == binding),
            "Wrong record kind/source/component/environment/artifact",
        )
        binding_valid(r["binding"], self.policy)
        if row[2]:
            self.get(row[2], binding=r["binding"], historical=historical)
        if r["kind"] == "authorization":
            require(
                r["details"]["decision"] == "approve"
                and r["details"]["physical_hardware_authorized"] is False,
                "Scoped software authorization required",
            )
            self.get(
                r["details"]["attempt_id"],
                "authorization-attempt",
                r["binding"],
                historical=historical,
            )
        return envelope

    def runner(self, phase, binding, context):
        profile = binding_valid(binding, self.policy)
        runner = profile["runners"][phase]
        path = Path(runner["path"])
        private(path, uid=self.root.stat().st_uid)
        require(
            path.resolve().is_relative_to(Path(self.trust["runner_root"]))
            and file_digest(path) == runner["sha256"],
            "Unreviewed runner refused",
        )
        r = subprocess.run(
            ["/usr/bin/python3", "-I", str(path), phase],
            input=canonical({"binding": binding, "context": context}),
            capture_output=True,
            timeout=runner["timeout_seconds"],
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            check=False,
        )
        require(
            r.returncode == 0 and len(r.stdout) <= 1024 * 1024,
            "Pinned runner failed/interrupted; inspect only",
        )
        value = json.loads(r.stdout)
        require(
            value.get("binding") == binding
            and value.get("environment")
            == ("staging" if phase == "staging" else "production"),
            "Observed source/artifact/environment mismatch",
        )
        require(
            value.get("workflow_run_id") == 0,
            "Owner observation must retain run ID zero",
        )
        if phase in ["deploy", "production"]:
            require(
                value.get("operation_id") == context["operation_id"],
                "Provider observation belongs to another operation",
            )
        require(
            isinstance(value.get("checks"), dict)
            and all(
                value["checks"].get(k) == "PASS"
                for k in profile["required_checks"][phase]
            ),
            "Mandatory observed check missing/failed/skipped",
        )
        require(
            value.get("observed_at")
            and timedelta(0)
            <= self.clock() - datetime.fromisoformat(value["observed_at"])
            < timedelta(minutes=5),
            "Fresh observations required",
        )
        return {"runner_sha256": runner["sha256"], "observations": value}

    def issue_context(self, binding):
        profile = binding_valid(binding, self.policy)
        ci = self.qualify_source(binding)
        with self.transaction():
            previous = self.db.execute(
                "SELECT envelope FROM records WHERE kind='context' AND scope=?",
                (component_scope(binding),),
            ).fetchall()
            count = sum(
                json.loads(row[0])["record"]["binding"]["candidate_commit"]
                == binding["candidate_commit"]
                for row in previous
            )
            require(
                count < profile["qualification_attempt_limit"],
                "Source-specific qualification allowance exhausted; no implicit renewal",
            )
            result = self.put(
                "context",
                binding,
                {
                    "ci": ci,
                    "state": "ISSUED_UNUSED",
                    "qualification_environment": "staging",
                },
            )
            self.db.execute(
                "INSERT INTO contexts(id,state) VALUES(?,?)",
                (result["record"]["id"], "issued"),
            )
        return result

    def qualify(self, context):
        r = self.get(context, "context")["record"]
        binding = r["binding"]
        with self.transaction():
            self.get(context, "context", binding)
            changed = self.db.execute(
                "UPDATE contexts SET state='reserved' WHERE id=? AND state='issued'",
                (context,),
            ).rowcount
            require(
                changed == 1, "Context already reserved/consumed; no automatic retry"
            )
        # Reservation commits before the first hosted request, including failures.
        ci = self.qualify_source(binding)
        stage = self.runner("staging", binding, {"context_id": context})
        operations = self.runner("operations", binding, {"context_id": context})
        with self.transaction():
            self.get(context, "context", binding)
            result = self.put(
                "qualification",
                binding,
                {"ci": ci, "staging": stage, "operations": operations},
                parent=context,
            )
            self.db.execute(
                "UPDATE contexts SET state='consumed' WHERE id=? AND state='reserved'",
                (context,),
            )
        return result

    def authorize(self, qualification):
        r = self.get(qualification, "qualification")["record"]
        binding = r["binding"]
        with self.transaction():
            attempt = self.put(
                "authorization-attempt",
                binding,
                {"decision": "PENDING"},
                parent=qualification,
            )
        ci = self.qualify_source(binding)
        operations = self.runner(
            "operations",
            binding,
            {"qualification_id": qualification, "owner_decision": "approve"},
        )
        # Owner invocation is a signed decision, never a caller's JSON approval.
        with self.transaction():
            self.get(qualification, "qualification", binding)
            return self.put(
                "authorization",
                binding,
                {
                    "decision": "approve",
                    "ci": ci,
                    "operations": operations,
                    "attempt_id": attempt["record"]["id"],
                    "physical_hardware_authorized": False,
                },
                parent=qualification,
            )

    def deploy(self, authorization):
        r = self.get(authorization, "authorization")["record"]
        binding = r["binding"]
        existing = self.db.execute(
            "SELECT id,state FROM deployments WHERE authorization=? OR qualification=?",
            (authorization, r["parent"]),
        ).fetchone()
        if existing:
            return {
                "operation_id": existing[0],
                "state": existing[1],
                "action": "INSPECT_ONLY_NO_DUPLICATE_REQUEST",
            }
        self.qualify_source(binding)
        self.runner(
            "operations",
            binding,
            {"authorization_id": authorization, "before_deployment": True},
        )
        scope = component_scope(binding)
        with self.transaction():
            self.get(authorization, "authorization", binding)
            uncertain = self.db.execute(
                "SELECT d.id FROM deployments d JOIN records r ON r.id=d.id WHERE r.scope=? AND d.state!='accepted' LIMIT 1",
                (scope,),
            ).fetchone()
            require(
                not uncertain,
                "Earlier component request is unresolved; inspect it before another deployment",
            )
            latest = self.db.execute(
                "SELECT id FROM records WHERE scope=? AND kind='authorization-attempt' ORDER BY sequence DESC LIMIT 1",
                (scope,),
            ).fetchone()
            require(
                latest and latest[0] == r["details"]["attempt_id"],
                "Authorization superseded by a newer owner decision",
            )
            operation = self.put(
                "deployment-intent",
                binding,
                {
                    "authorization": authorization,
                    "provider_request_budget": 1,
                    "automatic_retry": False,
                },
                parent=authorization,
            )
            id_ = operation["record"]["id"]
            try:
                self.db.execute(
                    "INSERT INTO deployments(id,authorization,qualification,state) VALUES(?,?,?,'request-recorded')",
                    (id_, authorization, r["parent"]),
                )
            except sqlite3.IntegrityError:
                raise Rejected("Concurrent deployment reserved; inspect only") from None
        receipt = self.runner(
            "deploy", binding, {"operation_id": id_, "authorization_id": authorization}
        )
        require(
            isinstance(receipt["observations"].get("deployment_id"), str)
            and receipt["observations"]["deployment_id"],
            "Provider receipt missing; inspect existing operation",
        )
        with self.transaction():
            self.put("deployment-receipt", binding, receipt, parent=id_)
            self.db.execute(
                "UPDATE deployments SET state='deployed-unverified' WHERE id=?", (id_,)
            )
        return self.inspect(id_, observe=True)

    def inspect(self, operation, *, observe=False):
        r = self.get(operation, "deployment-intent", historical=True)["record"]
        row = self.db.execute(
            "SELECT state FROM deployments WHERE id=?", (operation,)
        ).fetchone()
        require(row, "Deployment operation missing")
        if not observe:
            return {
                "operation_id": operation,
                "state": row[0],
                "provider_write": False,
                "intent": r,
            }
        observed = self.runner(
            "production", r["binding"], {"operation_id": operation, "read_only": True}
        )
        require(
            observed["observations"].get("deployment_id"),
            "Actual running deployment identity required",
        )
        receipts = self.db.execute(
            "SELECT id FROM records WHERE kind='deployment-receipt' AND parent=?",
            (operation,),
        ).fetchall()
        if receipts:
            receipt = self.get(receipts[-1][0], historical=True)["record"]["details"]
            require(
                observed["observations"]["deployment_id"]
                == receipt["observations"]["deployment_id"],
                "Canonical deployment differs from provider receipt",
            )
        with self.transaction():
            result = self.put(
                "production-verification", r["binding"], observed, parent=operation
            )
            self.db.execute(
                "UPDATE deployments SET state='accepted' WHERE id=?", (operation,)
            )
        return result

    def revoke(self, record):
        r = self.get(record, historical=True)["record"]
        with self.transaction():
            result = self.put("revocation", r["binding"], {"revoked_id": record})
            self.db.execute("UPDATE records SET revoked=1 WHERE id=?", (record,))
        return result


def bootstrap(root, policy, source_pin, policy_pin, runner_root, principal):
    require(
        os.geteuid() == 0 and not os.getenv("GITHUB_ACTIONS"),
        "Explicit external root-owner bootstrap required",
    )
    require(
        HEX.fullmatch(source_pin)
        and file_digest(__file__) == source_pin
        and HEX.fullmatch(policy_pin)
        and file_digest(policy) == policy_pin,
        "Owner-reviewed installation pins required",
    )
    private(Path(__file__))
    private(policy)
    private(runner_root, directory=True)
    require(
        principal.startswith("SHA256:") and len(principal) > 20,
        "Pinned owner SSH principal fingerprint required",
    )
    require(
        os.getenv("SUDO_USER") == "ubuntu",
        "Existing Oracle owner sudo principal required",
    )
    fingerprints = subprocess.check_output(
        [
            "/usr/bin/ssh-keygen",
            "-lf",
            "/home/ubuntu/.ssh/authorized_keys",
            "-E",
            "sha256",
        ],
        timeout=15,
        text=True,
    )
    require(
        principal in [line.split()[1] for line in fingerprints.splitlines()],
        "Owner SSH key is not authorized on this host",
    )
    root = Path(root)
    require(
        not root.exists(),
        "Existing custody preserved; explicit versioned installation required",
    )
    private(root.parent, directory=True)
    root.mkdir(mode=0o700, parents=False)
    old_umask = os.umask(0o077)
    try:
        (root / "policy.json").write_bytes(Path(policy).read_bytes())
        subprocess.run(
            [
                OPENSSL,
                "genpkey",
                "-algorithm",
                "ED25519",
                "-out",
                str(root / "signing.pem"),
            ],
            check=True,
            capture_output=True,
            timeout=15,
        )
        result = subprocess.run(
            [OPENSSL, "pkey", "-in", str(root / "signing.pem"), "-pubout"],
            check=True,
            capture_output=True,
            timeout=15,
        )
        (root / "public.pem").write_bytes(result.stdout)
        trust = {
            "schema_version": 1,
            "installation_id": str(uuid4()),
            "installed_at": timestamp().isoformat(),
            "verifier_sha256": source_pin,
            "policy_sha256": policy_pin,
            "key_id": file_digest(root / "public.pem"),
            "openssl_sha256": file_digest(OPENSSL),
            "runner_root": str(Path(runner_root).resolve()),
            "owner_ssh_principal": principal,
            "authority": "bootstrap-pending-explicit-owner-commission",
        }
        (root / "trust.json").write_bytes(canonical(trust))
        db = sqlite3.connect(root / "records.sqlite")
        db.executescript(
            "PRAGMA journal_mode=DELETE; PRAGMA synchronous=FULL; CREATE TABLE records(sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,scope TEXT NOT NULL,envelope TEXT NOT NULL,parent TEXT REFERENCES records(id),created TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0); CREATE TABLE contexts(id TEXT PRIMARY KEY REFERENCES records(id),state TEXT NOT NULL CHECK(state IN ('issued','reserved','consumed'))); CREATE TABLE deployments(id TEXT PRIMARY KEY REFERENCES records(id),authorization TEXT NOT NULL UNIQUE REFERENCES records(id),qualification TEXT NOT NULL UNIQUE REFERENCES records(id),state TEXT NOT NULL CHECK(state IN ('request-recorded','deployed-unverified','accepted')));"
        )
        db.close()
    finally:
        os.umask(old_umask)
    return trust


def commission(root, source_pin, policy_pin, key_id):
    """Separate owner bootstrap action anchors a reviewed installation; never implicit."""
    require(
        os.geteuid() == 0
        and os.getenv("SUDO_USER") == "ubuntu"
        and not os.getenv("GITHUB_ACTIONS"),
        "Explicit existing owner commissioning required",
    )
    root = Path(root)
    private(root, directory=True)
    for name in [
        "trust.json",
        "policy.json",
        "public.pem",
        "signing.pem",
        "records.sqlite",
    ]:
        private(root / name)
    trust = json.loads((root / "trust.json").read_text())
    require(
        source_pin == trust["verifier_sha256"] == file_digest(__file__)
        and policy_pin == trust["policy_sha256"] == file_digest(root / "policy.json")
        and key_id == trust["key_id"] == file_digest(root / "public.pem"),
        "Explicit reviewed source/policy/public-key pins required",
    )
    private(ANCHOR.parent, directory=True)
    if ANCHOR.exists():
        private(ANCHOR)
        registry = json.loads(ANCHOR.read_text())
    else:
        registry = {"schema_version": 1, "installations": {}}
    require(
        str(root) not in registry["installations"],
        "Existing commission preserved; no implicit overwrite",
    )
    registry["installations"][str(root)] = {
        **{
            k: trust[k]
            for k in ["installation_id", "key_id", "verifier_sha256", "policy_sha256"]
        },
        "active": True,
        "commissioned_at": timestamp().isoformat(),
    }
    with tempfile.NamedTemporaryFile(
        dir=ANCHOR.parent, prefix=".oracle-trust-", delete=False
    ) as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(canonical(registry))
        stream.flush()
        os.fsync(stream.fileno())
        path = stream.name
    os.replace(path, ANCHOR)
    descriptor = os.open(ANCHOR.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {
        "status": "COMMISSIONED",
        **registry["installations"][str(root)],
        "store": str(root),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "bootstrap",
            "commission",
            "verify-source",
            "issue-context",
            "qualify",
            "authorize",
            "deploy",
            "inspect",
            "observe",
            "revoke",
        ],
    )
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--record")
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--source-pin")
    parser.add_argument("--policy-pin")
    parser.add_argument("--runner-root", type=Path)
    parser.add_argument("--owner-ssh-principal")
    parser.add_argument("--key-id")
    args = parser.parse_args()
    require(
        os.geteuid() == 0 and not os.getenv("GITHUB_ACTIONS"),
        "External owner custody required",
    )
    if args.action == "bootstrap":
        value = bootstrap(
            args.store,
            args.policy,
            args.source_pin,
            args.policy_pin,
            args.runner_root,
            args.owner_ssh_principal,
        )
    elif args.action == "commission":
        value = commission(args.store, args.source_pin, args.policy_pin, args.key_id)
    else:
        raw = __import__("sys").stdin.buffer.read(128 * 1024 + 1)
        require(len(raw) <= 128 * 1024, "Private request size bound")
        request = source_json(raw) if args.action == "verify-source" else json.loads(raw)
        if args.action == "verify-source":
            require(isinstance(request, dict), "Source verification request must be an object")
        store = Store(args.store, GitHub(request.pop("owner_token", None)))
        if args.action == "verify-source":
            require(set(request) == {"source", "artifact_archives"} and args.record is None,
                    "Exact source verification input required; operational record refused")
            value = store.verify_source(request["source"], request["artifact_archives"])
        elif args.action == "issue-context":
            require(set(request) == {"binding"}, "Unexpected context input")
            value = store.issue_context(request["binding"])
        else:
            require(
                not request and args.record,
                "Record ID required; caller approval/evidence JSON refused",
            )
            if args.action in ["inspect", "observe"]:
                value = store.inspect(args.record, observe=args.action == "observe")
            else:
                value = getattr(store, args.action)(args.record)
    print(json.dumps(value, allow_nan=False))


if __name__ == "__main__":
    main()
