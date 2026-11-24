"""Release evidence from GitHub's API, never from caller-supplied PASS values.

Independent copies live in each repository. No provider credentials are read here.
Artifacts are JSON data only: archives are verified and never extracted/executed.
"""

import argparse
import hashlib
import importlib.util
import io
import json
import os
import re
import subprocess
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Repository identity must not change with an arbitrary clone or worktree directory.
PRODUCT = "onesa-robot"
# Component paths and hosted image/artifact identities predate the repository rename.
APPLICATION_PREFIX = "onesa-ai-robot" if PRODUCT == "onesa-robot" else PRODUCT
OWNER = "DruHustle"
REPOSITORY = OWNER + "/" + PRODUCT
GATES = (
    "CODE_VERIFIED",
    "STAGING_VERIFIED",
    "OPERATIONS_VERIFIED",
    "CONTENT_LEGAL_APPROVED",
)
POLICIES = {
    "onesa-company-portal": {
        "ci": "verify.yml",
        "jobs": [
            "verify",
            "pipeline-security / workflow-security",
            "ci-summary",
            "image-security (onesa-company-portal-backend)",
            "image-security (onesa-company-portal-frontend)",
        ],
        "staging_branch": "staging",
    },
    "onesa-ai-platform": {
        "ci": "ci.yml",
        "jobs": [
            "backend",
            "pipeline-security / workflow-security",
            "ci-summary",
            "frontend",
            "browser",
            "documentation",
            "containers",
            "tls-acceptance",
            "image-security (onesa-ai-platform-backend)",
            "image-security (onesa-ai-platform-frontend)",
        ],
        "staging_branch": "staging",
    },
    "onesa-robot": {
        "ci": "scaffold.yml",
        "jobs": [
            "backend",
            "pipeline-security / workflow-security",
            "ci-summary",
            "frontend",
            "containers (frontend)",
            "containers (backend)",
            "ros-contracts",
        ],
        "staging_branch": "staging",
    },
}
POLICY = POLICIES[PRODUCT]


class Rejected(ValueError):
    """A release requirement has no valid evidence."""


def require(condition, message):
    if not condition:
        raise Rejected(message)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def source_hash():
    # Preserve the existing qualify.py algorithm, including its ignored-file rules.
    original = ROOT / "scripts/release/qualify.py"
    if original.exists():
        spec = importlib.util.spec_from_file_location("legacy_qualification", original)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.source_hash()
    # Robot did not previously have a source-hash routine; use the same algorithm.
    paths = subprocess.check_output(
        ["git", "ls-files", "-c", "-o", "--exclude-standard"], cwd=ROOT, text=True
    ).splitlines()
    result = hashlib.sha256()
    for name in sorted(set(paths)):
        file = ROOT / name
        if file.is_file():
            result.update(
                name.encode() + b"\0" + hashlib.sha256(file.read_bytes()).digest()
            )
    return result.hexdigest()


def fresh(timestamp, now, hours=24):
    observed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return observed <= now < observed + timedelta(hours=hours)


class GitHub:
    def api(self, path, pages=False, binary=False):
        require(path.startswith("repos/" + REPOSITORY + "/"), "Foreign API path")
        args = ["gh", "api", path]
        if pages:
            args += ["--paginate", "--slurp"]
        result = subprocess.run(args, capture_output=True, check=False)
        require(result.returncode == 0, "GitHub API unavailable: release stays blocked")
        return result.stdout if binary else json.loads(result.stdout)

    def run(self, identifier):
        require(str(identifier).isdigit(), "Workflow run ID must be numeric")
        return self.api(f"repos/{REPOSITORY}/actions/runs/{identifier}")

    def runs(self, workflow, sha):
        pages = self.api(
            f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100",
            pages=True,
        )
        return sorted(
            [run for page in pages for run in page["workflow_runs"]],
            key=lambda run: run["id"],
            reverse=True,
        )

    def jobs(self, run):
        pages = self.api(
            f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/1/jobs?per_page=100",
            pages=True,
        )
        return [job for page in pages for job in page["jobs"]]

    def artifact(self, run, name, filename="record.json", raw=False):
        pages = self.api(
            f"repos/{REPOSITORY}/actions/runs/{run['id']}/artifacts?per_page=100",
            pages=True,
        )
        matches = [
            item for page in pages for item in page["artifacts"] if item["name"] == name
        ]
        require(len(matches) == 1, "Expected one immutable " + name + " artifact")
        metadata = self.api(f"repos/{REPOSITORY}/actions/artifacts/{matches[0]['id']}")
        require(not metadata["expired"], "Artifact expired")
        require(
            metadata["size_in_bytes"] <= (512 * 1024 * 1024 if raw else 1024 * 1024),
            "Artifact too large",
        )
        origin = metadata["workflow_run"]
        require(
            origin["id"] == run["id"]
            and origin["head_sha"] == run["head_sha"]
            and origin["head_repository_id"] == run["repository"]["id"],
            "Artifact belongs to another candidate/repository",
        )
        archive = self.api(
            f"repos/{REPOSITORY}/actions/artifacts/{metadata['id']}/zip", binary=True
        )
        data = decode_artifact(archive, metadata["digest"], filename=filename, raw=raw)
        return data, {
            "id": metadata["id"],
            "digest": metadata["digest"],
            "name": name,
            "run_id": run["id"],
            "record_sha256": hashlib.sha256(data).hexdigest() if raw else digest(data),
        }

    def protected_main(self, sha):
        branch = self.api(f"repos/{REPOSITORY}/branches/main")
        require(branch["commit"]["sha"] == sha, "Candidate is not current main")
        require(
            branch["protected"],
            "Main is not protected; activate and verify repository governance first",
        )
        # Require the prepared classic policy. Ruleset-only setups need a reviewed adapter.
        policy = self.api(f"repos/{REPOSITORY}/branches/main/protection")
        require(
            policy.get("required_pull_request_reviews") is not None
            and policy.get("required_status_checks") is not None,
            "Main must require PRs and status checks",
        )
        require(
            policy["required_status_checks"].get("strict")
            and policy.get("enforce_admins", {}).get("enabled"),
            "Main must enforce up-to-date checks and administrator restrictions",
        )
        require(
            not policy.get("allow_force_pushes", {}).get("enabled")
            and not policy.get("allow_deletions", {}).get("enabled"),
            "Main permits force pushes or deletion",
        )

        expected = json.loads(
            (
                ROOT / "docs/development/branch-protection/main.branch-protection.json"
            ).read_text()
        )
        expected_checks = {
            (entry["context"], entry["app_id"])
            for entry in expected["required_status_checks"]["checks"]
        }
        actual_checks = {
            (entry["context"], entry.get("app_id"))
            for entry in policy["required_status_checks"].get("checks", [])
        }
        require(
            expected_checks <= actual_checks,
            "Required CI/security contexts or GitHub Actions app bindings missing",
        )
        bypass = policy["required_pull_request_reviews"].get(
            "bypass_pull_request_allowances", {}
        )
        require(
            not any(bypass.get(kind) for kind in ["users", "teams", "apps"]),
            "PR protection has bypass actors",
        )


def decode_artifact(archive, expected_digest, filename="record.json", raw=False):
    require(
        "sha256:" + hashlib.sha256(archive).hexdigest() == expected_digest,
        "Evidence archive was altered",
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        entries = bundle.infolist()
        require(
            len(entries) == 1 and entries[0].filename == filename,
            "Artifact must contain only record.json",
        )
        require(
            entries[0].file_size <= (512 * 1024 * 1024 if raw else 1024 * 1024),
            "Evidence too large",
        )
        return bundle.read(entries[0]) if raw else json.loads(bundle.read(entries[0]))


def trusted_run(
    run, workflow, sha, branches, event="workflow_dispatch", completed=True
):
    require(
        run["repository"]["full_name"] == REPOSITORY
        and run["head_repository"]["full_name"] == REPOSITORY,
        "Foreign workflow repository",
    )
    require(run["path"] == ".github/workflows/" + workflow, "Untrusted workflow path")
    require(
        run["head_sha"] == sha
        and run["head_branch"] in branches
        and run["event"] == event,
        "Wrong workflow commit, branch or event",
    )
    require(run.get("run_attempt") == 1, "Re-runs require a fresh qualification/review")
    if event == "workflow_dispatch":
        require(
            run["actor"]["login"] == OWNER
            and run["triggering_actor"]["login"] == OWNER,
            "Unauthorized release operator",
        )
    if completed:
        require(
            run["status"] == "completed" and run["conclusion"] == "success",
            "Workflow did not succeed",
        )


def trusted_staging_run(github, run, sha, completed=True):
    """Manual authorized branch, or opt-in owner CI completion on the same main SHA."""
    event = run["event"]
    require(event in {"workflow_dispatch", "workflow_run"}, "Untrusted staging event")
    branches = {POLICY["staging_branch"]} if event == "workflow_dispatch" else {"main"}
    workflow = "release-staging.yml"
    if run.get("path") == ".github/workflows/release-candidate.yml":
        require(
            event == "workflow_dispatch",
            "Registered staging bridge requires owner dispatch",
        )
        workflow = "release-candidate.yml"
    trusted_run(run, workflow, sha, branches, event=event, completed=completed)
    require(
        run["actor"]["login"] == OWNER and run["triggering_actor"]["login"] == OWNER,
        "Staging requires owner provenance",
    )
    if event == "workflow_run":
        main = github.api(f"repos/{REPOSITORY}/branches/main")
        stage = github.api(f"repos/{REPOSITORY}/branches/{POLICY['staging_branch']}")
        require(
            main["commit"]["sha"] == sha and stage["commit"]["sha"] == sha,
            "Automatic staging requires main and the authorized staging branch at the exact candidate",
        )


def checked_ci(github, sha):
    references = []
    failed = False
    missing = False
    for workflow, required in [
        (POLICY["ci"], POLICY["jobs"]),
        ("repository-hygiene.yml", ["publication-hygiene"]),
    ]:
        runs = [
            run
            for run in github.runs(workflow, sha)
            if run["event"] == "push"
            and run["head_branch"] in {"main", POLICY["staging_branch"]}
        ]
        main_runs = [item for item in runs if item["head_branch"] == "main"]
        if main_runs:
            runs = main_runs
        if not runs:
            missing = True
            continue
        run = runs[0]
        trusted_run(
            run,
            workflow,
            sha,
            {"main", POLICY["staging_branch"]},
            event="push",
            completed=False,
        )
        jobs = github.jobs(run)
        by_name = {job["name"]: job for job in jobs}
        statuses = []
        for name in required:
            job = by_name.get(name)
            if job is None or job["conclusion"] in {None, "skipped", "cancelled"}:
                missing = True
            elif job["conclusion"] != "success":
                failed = True
            else:
                # A successful job with a failing/skipped security/test step is insufficient.
                require(
                    all(
                        step["conclusion"] == "success"
                        for step in job.get("steps", [])
                        if step["name"].startswith("Release requirement:")
                    ),
                    "Mandatory check was skipped/failed",
                )
            statuses.append(
                {
                    "name": name,
                    "conclusion": job["conclusion"] if job else "NOT TESTED",
                    "url": job["html_url"] if job else None,
                }
            )
        if run["conclusion"] not in {None, "success", "cancelled"}:
            failed = True
        if run["status"] != "completed" or run["conclusion"] != "success":
            missing = True
        references.append(
            {
                "workflow": workflow,
                "run_id": run["id"],
                "commit": sha,
                "jobs": statuses,
                "verified_at": run.get("updated_at"),
            }
        )
    return ("FAIL" if failed else "BLOCKED" if missing else "PASS"), references


def artifacts_for_ci(github, references):
    builds = []
    for reference in references:
        if reference["workflow"] != POLICY["ci"]:
            continue
        run = github.run(reference["run_id"])
        for application in [
            APPLICATION_PREFIX + "-backend",
            APPLICATION_PREFIX + "-frontend",
        ]:
            manifest, origin = github.artifact(run, "runtime-" + application)
            require(
                manifest["repository"] == REPOSITORY
                and manifest["candidate_commit"] == run["head_sha"],
                "Build manifest candidate mismatch",
            )
            require(
                re.fullmatch(r"sha256:[a-f0-9]{64}", manifest["image_digest"])
                is not None,
                "Missing tested image digest",
            )
            require(manifest["security"] == "PASS", "Security blocker unresolved")
            builds.append(
                {
                    "application": application,
                    "image_digest": manifest["image_digest"],
                    "artifact": origin,
                }
            )
    require(len(builds) == 2, "Tested build artifacts missing")
    return builds


def observed_context(github):
    sha = os.environ["GITHUB_SHA"]
    require(
        subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()
        == sha,
        "Checkout commit mismatch",
    )
    require(
        subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"], cwd=ROOT, check=False
        ).returncode
        == 0,
        "Tracked source was altered",
    )
    require(re.fullmatch(r"[a-f0-9]{40}", sha) is not None, "Invalid candidate SHA")
    require(os.environ["GITHUB_REPOSITORY"] == REPOSITORY, "Repository mismatch")
    run = github.run(os.environ["GITHUB_RUN_ID"])
    require(
        run["head_sha"] == sha and run["repository"]["full_name"] == REPOSITORY,
        "Current workflow identity mismatch",
    )
    require(
        run["actor"]["login"] == OWNER
        and run["triggering_actor"]["login"] == OWNER
        and run["run_attempt"] == 1,
        "Only a new owner-dispatched run is authorized",
    )
    return sha, run


def generate(github, sha, run, staging_run=None, require_frontend_build=True):
    code, references = checked_ci(github, sha)
    builds = []
    reason = None
    if code == "PASS":
        try:
            builds = artifacts_for_ci(github, references)
        except Rejected as error:
            code, reason = "BLOCKED", str(error)
    simulation_code = code
    record = {
        "schema_version": 1,
        "repository": REPOSITORY,
        "workflow_run_id": run["id"],
        "candidate_commit": sha,
        "reviewed_commit": sha,
        "valid_for_source_sha256": source_hash(),
        "target_environment": "Production",
        "content_version": source_hash(),
        "verification_timestamp": datetime.now(timezone.utc).isoformat(),
        "test_results": references,
        "builds": builds,
        "security_scan_evidence": [
            ref for ref in references if ref["workflow"] == POLICY["ci"]
        ],
        "frontend_build": None,
        "staging": None,
        "approval_provenance": None,
        "CODE_VERIFIED": code,
        "STAGING_VERIFIED": "BLOCKED",
        "OPERATIONS_VERIFIED": "BLOCKED",
        "CONTENT_LEGAL_APPROVED": "BLOCKED",
        "qualification_status": "BLOCKED",
        "missing_evidence": [reason] if reason else [],
        "provider_build_policy": "Render rebuilds the exact source commit; Vercel deploys the reviewed prebuilt artifact; Render binary equivalence is not claimed",
    }
    if code == "PASS" and require_frontend_build:
        try:
            candidates = github.runs("release-build.yml", sha)
            require(candidates, "Reviewed production frontend build missing")
            build_run = candidates[0]
            trusted_run(build_run, "release-build.yml", sha, {"main"})
            manifest, origin = github.artifact(build_run, "production-build-manifest")
            require(
                manifest["repository"] == REPOSITORY
                and manifest["candidate_commit"] == sha
                and manifest["workflow_run_id"] == build_run["id"]
                and manifest["target_environment"] == "Production"
                and manifest.get("source_sha256") == record["valid_for_source_sha256"],
                "Production build candidate mismatch",
            )
            require(
                re.fullmatch(r"[a-f0-9]{64}", manifest["build_sha256"]) is not None
                and re.fullmatch(r"[a-f0-9]{64}", manifest["encrypted_sha256"])
                is not None,
                "Production build digests missing",
            )
            _, bundle_origin = github.artifact(
                build_run, "production-build-encrypted", filename="bundle.enc", raw=True
            )
            require(
                bundle_origin["record_sha256"] == manifest["encrypted_sha256"],
                "Encrypted build digest mismatch",
            )
            record["frontend_build"] = {
                "manifest": manifest,
                "artifact": origin,
                "bundle_artifact": bundle_origin,
            }
        except Rejected as error:
            record["CODE_VERIFIED"] = code = "BLOCKED"
            record["missing_evidence"].append(str(error))
    if PRODUCT == "onesa-robot":
        record.update(
            {
                "scope": "software-and-simulation-only",
                "SOFTWARE_SIMULATION_VERIFIED": simulation_code,
                "SIMULATION_VERIFIED": simulation_code,
                "ROBOT_SAFETY_SOFTWARE_VERIFIED": simulation_code,
                "EDGE_DEPLOYMENT_VERIFIED": "NOT TESTED",
                "PHYSICAL_HARDWARE_APPROVED": "BLOCKED",
                "ROS2_VERIFIED": "NOT TESTED",
                "ROS2_INTERFACE_VERIFIED": "PASS"
                if simulation_code == "PASS"
                else "NOT TESTED",
                "PHYSICAL_HARDWARE_READY": "BLOCKED",
            }
        )
    if staging_run:
        stage_run = github.run(staging_run)
        trusted_staging_run(github, stage_run, sha)
        stage, origin = github.artifact(stage_run, "staging-verification")
        verify_stage(stage, sha, record["valid_for_source_sha256"], builds)
        if PRODUCT == "onesa-robot" and stage.get("component_plan"):
            from components import verify

            record["component_plan"] = verify(github, sha, stage["component_plan"])
        record["staging"] = {"record": stage, "artifact": origin}
        record["STAGING_VERIFIED"] = stage["status"]
    if code == "FAIL" or record["STAGING_VERIFIED"] == "FAIL":
        record["qualification_status"] = "FAIL"
    elif code == "PASS" and record["STAGING_VERIFIED"] == "PASS":
        record["qualification_status"] = "AWAITING_REVIEW"
    return record


def verify_stage(stage, sha, fingerprint, builds, now=None):
    require(
        stage["repository"] == REPOSITORY
        and stage["candidate_commit"] == sha
        and stage["source_sha256"] == fingerprint,
        "Staging belongs to another candidate",
    )
    require(
        stage["environment"] == "Staging" and stage["builds_sha256"] == digest(builds),
        "Staging tested a different build manifest",
    )
    require(
        fresh(stage["verified_at"], now or datetime.now(timezone.utc)),
        "Staging evidence stale",
    )
    required = {
        "deployed_commit",
        "backend_readiness",
        "frontend_availability",
        "authentication",
        "database_connectivity",
        "critical_workflows",
        "user_data_isolation",
        "storage_integrations",
    }
    if PRODUCT == "onesa-robot":
        required = {
            "deployed_commit",
            "backend_readiness",
            "frontend_availability",
            "authentication",
            "authorization",
            "simulation",
            "safety_denial",
            "cancellation",
            "connection_loss",
            "stale_command",
            "replay_rejection",
            "audit_history",
            "browser_acceptance",
            "storage_isolation",
            "edge_telemetry",
            "administrator_operation_permission",
        }
    if PRODUCT == "onesa-ai-platform":
        required |= {"streaming", "inference", "embeddings"}
    require(required <= stage["checks"].keys(), "Mandatory staging checks absent")
    require(
        stage.get("render_deployment_id") and stage.get("vercel_deployment_id"),
        "Staging deployment identifiers missing",
    )
    if PRODUCT == "onesa-robot" and stage.get("component_plan"):
        selected = stage["component_plan"]
        require(
            selected.get("mode") == "frontend-only"
            and selected.get("frontend_commit") == sha
            and selected.get("physical_hardware_authorized") is False
            and stage.get("components", {}).get("frontend", {}).get("commit") == sha
            and stage.get("components", {}).get("frontend", {}).get("deployment_id")
            == stage["vercel_deployment_id"]
            and stage.get("components", {}).get("backend", {}).get("commit")
            == selected.get("backend_commit")
            and stage.get("components", {}).get("backend", {}).get("deployment_id")
            == stage["render_deployment_id"],
            "Staging component versions differ from the selected plan",
        )
    if stage["status"] == "PASS":
        require(
            all(stage["checks"][name] == "PASS" for name in required),
            "Staging checks unresolved",
        )
    return stage["status"]


def qualified_record(github, identifier, sha):
    run = github.run(identifier)
    trusted_run(run, "release-qualification.yml", sha, {"main"}, event=run["event"])
    require(
        run["event"] in {"workflow_dispatch", "workflow_run"},
        "Untrusted qualification event",
    )
    evidence, origin = github.artifact(run, "release-qualification")
    require(
        evidence["schema_version"] == 1
        and evidence["repository"] == REPOSITORY
        and evidence["workflow_run_id"] == run["id"],
        "Qualification origin mismatch",
    )
    require(
        evidence["reviewed_commit"] == sha and evidence["candidate_commit"] == sha,
        "Reviewed SHA mismatch",
    )
    require(
        evidence["valid_for_source_sha256"] == source_hash(), "Source hash mismatch"
    )
    require(
        fresh(evidence["verification_timestamp"], datetime.now(timezone.utc)),
        "Qualification stale",
    )
    return evidence, origin


def technical_gate(github, evidence, sha):
    require(
        evidence["CODE_VERIFIED"] == "PASS" and evidence["STAGING_VERIFIED"] == "PASS",
        "Technical or staging qualification missing",
    )
    if PRODUCT == "onesa-robot":
        require(
            evidence.get("scope") == "software-and-simulation-only"
            and evidence.get("SIMULATION_VERIFIED") == "PASS"
            and evidence.get("ROBOT_SAFETY_SOFTWARE_VERIFIED") == "PASS"
            and evidence.get("PHYSICAL_HARDWARE_APPROVED") == "BLOCKED",
            "Cloud approval cannot authorize physical operation",
        )
    code, references = checked_ci(github, sha)
    require(code == "PASS", "Latest exact-commit CI/security did not pass")
    require(
        artifacts_for_ci(github, references) == evidence["builds"],
        "Tested build artifact changed",
    )
    build = evidence.get("frontend_build")
    require(build, "Reviewed frontend build absent")
    build_run = github.run(build["artifact"]["run_id"])
    trusted_run(build_run, "release-build.yml", sha, {"main"})
    manifest, manifest_origin = github.artifact(build_run, "production-build-manifest")
    require(
        manifest == build["manifest"]
        and manifest_origin == build["artifact"]
        and manifest.get("source_sha256") == evidence["valid_for_source_sha256"],
        "Reviewed frontend build altered",
    )
    _, bundle_origin = github.artifact(
        build_run, "production-build-encrypted", filename="bundle.enc", raw=True
    )
    require(
        bundle_origin == build["bundle_artifact"]
        and bundle_origin["record_sha256"] == manifest["encrypted_sha256"],
        "Frontend artifact differs from reviewed build",
    )
    stage = evidence["staging"]
    stage_run = github.run(stage["artifact"]["run_id"])
    trusted_staging_run(github, stage_run, sha)
    stage_record, origin = github.artifact(stage_run, "staging-verification")
    require(
        origin == stage["artifact"] and stage_record == stage["record"],
        "Staging artifact altered",
    )
    require(
        verify_stage(
            stage_record, sha, evidence["valid_for_source_sha256"], evidence["builds"]
        )
        == "PASS",
        "Staging unavailable or blocked",
    )


def review(github, sha, run, args):
    trusted_run(run, "release-review.yml", sha, {"main"}, completed=False)
    github.protected_main(sha)
    evidence, origin = qualified_record(github, args.qualification_run, sha)
    require(
        digest(evidence) == args.evidence_digest,
        "Owner must approve the exact reviewed evidence digest",
    )
    if args.decision == "approve":
        technical_gate(github, evidence, sha)
        require(
            args.operations_review == "approved" and args.legal_review == "approved",
            "Explicit operator and legal/content approvals required",
        )
        require(
            re.fullmatch(
                r"https://github\.com/DruHustle/"
                + re.escape(PRODUCT)
                + r"/(issues|pull)/[0-9]+",
                args.review_url,
            )
            is not None,
            "Review needs a repository issue/PR evidence reference",
        )
        issue_id = args.review_url.rsplit("/", 1)[-1]
        issue = github.api(f"repos/{REPOSITORY}/issues/{issue_id}")
        require(
            issue["user"]["login"] == OWNER and issue["state"] == "open",
            "Review record is not an open owner-authored record",
        )
    return {
        "schema_version": 1,
        "repository": REPOSITORY,
        "candidate_commit": sha,
        "source_sha256": evidence["valid_for_source_sha256"],
        "builds_sha256": digest(evidence["builds"]),
        "target_environment": "Production",
        "content_version": evidence["content_version"],
        "qualification": origin,
        "evidence_sha256": digest(evidence),
        "decision": args.decision,
        "operations_review": args.operations_review,
        "legal_review": args.legal_review,
        "review_url": args.review_url,
        "approver": run["actor"]["login"],
        "approved_at": run["created_at"],
        "workflow_run_id": run["id"],
    }


def validate_approval(approval, evidence, origin, run, sha, now=None):
    require(approval["decision"] == "approve", "Approval revoked or absent")
    require(
        approval["approver"] == OWNER
        and approval["workflow_run_id"] == run["id"]
        and approval["approved_at"] == run["created_at"],
        "Unauthorized approval identity/time",
    )
    require(
        approval["repository"] == REPOSITORY
        and approval["candidate_commit"] == sha
        and approval["target_environment"] == "Production",
        "Approval candidate/environment mismatch",
    )
    require(
        approval["qualification"] == origin
        and approval["evidence_sha256"] == digest(evidence),
        "Approved evidence altered",
    )
    require(
        approval["source_sha256"] == evidence["valid_for_source_sha256"]
        and approval["content_version"] == evidence["content_version"]
        and approval["builds_sha256"] == digest(evidence["builds"]),
        "Approved source/content/build mismatch",
    )
    require(
        approval["operations_review"] == "approved"
        and approval["legal_review"] == "approved",
        "Operations or legal approval missing",
    )
    require(
        fresh(approval["approved_at"], now or datetime.now(timezone.utc)),
        "Approval stale",
    )


def promote(github, sha, identifier):
    github.protected_main(sha)
    reviews = [
        run
        for run in github.runs("release-review.yml", sha)
        if run["event"] == "workflow_dispatch" and run["head_branch"] == "main"
    ]
    require(
        reviews and str(reviews[0]["id"]) == str(identifier),
        "Approval superseded/revoked; latest review run required",
    )
    run = reviews[0]
    trusted_run(run, "release-review.yml", sha, {"main"})
    approval, _ = github.artifact(run, "release-approval")
    evidence, origin = qualified_record(
        github, approval["qualification"]["run_id"], sha
    )
    validate_approval(approval, evidence, origin, run, sha)
    technical_gate(github, evidence, sha)
    issue = github.api(
        f"repos/{REPOSITORY}/issues/{approval['review_url'].rsplit('/', 1)[-1]}"
    )
    require(
        issue["state"] == "open" and issue["user"]["login"] == OWNER,
        "Owner review closed/revoked",
    )
    result = dict(evidence)
    result.update(
        {
            "OPERATIONS_VERIFIED": "PASS",
            "CONTENT_LEGAL_APPROVED": "PASS",
            "qualification_status": "APPROVED",
            "approval_provenance": approval,
        }
    )
    return result


def save(record, output):
    path = Path(output)
    require(path.name == "record.json", "Output filename must be record.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as stream:
            stream.write(
                "\nRelease evidence SHA256: `"
                + digest(record)
                + "`\n\nStatus: "
                + record.get("qualification_status", record.get("decision", "observed"))
                + "\n"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "generate",
            "review",
            "promote",
            "manifest",
            "source-hash",
            "stage-preflight",
            "edge-preflight",
            "build-preflight",
            "fetch-build",
        ],
    )
    parser.add_argument("--output", default="data/release/record.json")
    parser.add_argument("--staging-run")
    parser.add_argument("--qualification-run", default=os.getenv("QUALIFICATION_RUN"))
    parser.add_argument("--approval-run", default=os.getenv("APPROVAL_RUN"))
    parser.add_argument("--evidence-digest", default=os.getenv("EVIDENCE_DIGEST"))
    parser.add_argument(
        "--decision",
        choices=["approve", "revoke"],
        default=os.getenv("REVIEW_DECISION"),
    )
    parser.add_argument("--operations-review", default=os.getenv("OPERATIONS_REVIEW"))
    parser.add_argument("--legal-review", default=os.getenv("LEGAL_REVIEW"))
    parser.add_argument("--review-url", default=os.getenv("REVIEW_URL"))
    parser.add_argument("--image")
    args = parser.parse_args()
    if args.mode == "source-hash":
        print(source_hash())
        return
    if args.mode == "manifest":
        image = subprocess.check_output(
            ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"], text=True
        ).strip()
        save(
            {
                "repository": REPOSITORY,
                "candidate_commit": os.environ["GITHUB_SHA"],
                "image_digest": image,
                "security": "PASS",
            },
            args.output,
        )
        return
    github = GitHub()
    sha, run = observed_context(github)
    if args.mode == "build-preflight":
        trusted_run(run, "release-build.yml", sha, {"main"}, completed=False)
        github.protected_main(sha)
        code, references = checked_ci(github, sha)
        require(code == "PASS", "Build requires successful exact-candidate CI")
        record = {
            "repository": REPOSITORY,
            "candidate_commit": sha,
            "builds": artifacts_for_ci(github, references),
            "source_sha256": source_hash(),
        }
    elif args.mode == "fetch-build":
        require(
            os.environ["GITHUB_REF"] == "refs/heads/main",
            "Production builds require main",
        )
        approved = promote(github, sha, args.approval_run)
        build = approved["frontend_build"]
        build_run = github.run(build["artifact"]["run_id"])
        encrypted, origin = github.artifact(
            build_run, "production-build-encrypted", filename="bundle.enc", raw=True
        )
        require(origin == build["bundle_artifact"], "Build artifact mismatch")
        directory = Path(args.output).parent
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "bundle.enc").write_bytes(encrypted)
        record = build["manifest"]
    elif args.mode in {"stage-preflight", "edge-preflight"}:
        if args.mode == "stage-preflight":
            trusted_staging_run(github, run, sha, completed=False)
            if run["event"] == "workflow_run":
                event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
                upstream = github.run(event["workflow_run"]["id"])
                trusted_run(upstream, POLICY["ci"], sha, {"main"}, event="push")
                require(
                    upstream["actor"]["login"] == OWNER
                    and upstream["triggering_actor"]["login"] == OWNER,
                    "Automatic staging requires owner-triggered main CI",
                )
        else:
            trusted_run(
                run,
                "robot-edge-release.yml",
                sha,
                {POLICY["staging_branch"]},
                completed=False,
            )
        if args.mode == "edge-preflight":
            github.protected_main(sha)
            require(
                os.environ.get("EDGE_LICENSE_REVIEW") == "approved",
                "Owner distribution review required",
            )
            issue_id = os.environ.get("EDGE_REVIEW_ISSUE", "")
            require(issue_id.isdigit(), "Owner distribution review issue required")
            issue = github.api(f"repos/{REPOSITORY}/issues/{issue_id}")
            require(
                issue["user"]["login"] == OWNER and issue["state"] == "open",
                "Distribution review unavailable/revoked",
            )
        record = generate(github, sha, run, require_frontend_build=False)
        require(
            record["CODE_VERIFIED"] == "PASS",
            "Exact staging CI/build/security must pass",
        )
        if args.mode == "stage-preflight" and PRODUCT == "onesa-robot":
            mode = os.environ.get("ROBOT_RELEASE_MODE", "full")
            require(mode in {"full", "frontend-only"}, "Unknown Robot release mode")
            if mode == "frontend-only":
                require(
                    run["event"] == "workflow_dispatch",
                    "Component reuse requires explicit owner staging dispatch",
                )
                from components import plan

                record["component_plan"] = plan(
                    github, sha, os.environ.get("ROBOT_BASELINE_AUTHORIZATION_RUN", "")
                )
            else:
                require(
                    not os.environ.get("ROBOT_BASELINE_AUTHORIZATION_RUN"),
                    "A full release cannot silently select a retained component",
                )
    elif args.mode == "generate":
        require(
            run["event"] in {"workflow_dispatch", "workflow_run"},
            "Untrusted qualification event",
        )
        trusted_run(
            run,
            "release-qualification.yml",
            sha,
            {"main", POLICY["staging_branch"]},
            event=run["event"],
            completed=False,
        )
        record = generate(
            github, sha, run, args.staging_run or os.getenv("STAGING_RUN")
        )
    elif args.mode == "review":
        record = review(github, sha, run, args)
    else:
        require(
            os.environ["GITHUB_REF"] == "refs/heads/main"
            and run["event"] == "workflow_dispatch",
            "Production requires owner dispatch on main",
        )
        record = promote(github, sha, args.approval_run)
    save(record, args.output)


if __name__ == "__main__":
    try:
        main()
    except (
        Rejected,
        KeyError,
        TypeError,
        ValueError,
        OSError,
        zipfile.BadZipFile,
    ) as error:
        raise SystemExit("Release blocked: " + str(error)) from None
