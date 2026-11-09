"""Local, credential-free fixtures for historical consolidated source proofs.

SourceFixture is copied from scripts/ci/test_source_consumer.py at
DruHustle/onesa-ug commit 0341f51dfc6bf41ee84bedb0dbac4de40f2725b5.
It receives this repository\'s verifier explicitly and never imports sibling
products or performs live network calls. Historical source identities remain
unchanged so the release tests continue exercising the original trust boundary.
The fixture implementation is unchanged apart from formatting.
"""

import base64
import copy
import hashlib
import io
import json
import stat
import zipfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch


class SourceFixture:
    def __init__(self, module, product="portal", branch="staging"):
        self.module = module
        self.now = datetime.now(timezone.utc)
        self.source = {
            "schema_version": 2,
            "repository": module.CONSOLIDATED_REPOSITORY,
            "repository_id": module.CONSOLIDATED_REPOSITORY_ID,
            "product": product,
            "source_path": module.CONSOLIDATED_PRODUCTS[product],
            "candidate_commit": "a" * 40,
            "source_tree": "b" * 40,
            "product_source_tree": "c" * 40,
            "branch": branch,
        }
        self.policy = {
            "consolidated_sources": {
                product: {
                    "repository": self.source["repository"],
                    "repository_id": self.source["repository_id"],
                    "source_path": self.source["source_path"],
                    "branches": ["staging", "main"],
                }
            }
        }
        repository = {
            "id": self.source["repository_id"],
            "full_name": self.source["repository"],
        }
        sha = self.source["candidate_commit"]
        self.prefix = "repos/" + self.source["repository"] + "/"
        self.run = {
            "id": 123,
            "repository": repository,
            "head_repository": repository,
            "head_sha": sha,
            "head_branch": branch,
            "event": "push",
            "actor": {"login": module.OWNER},
            "triggering_actor": {"login": module.OWNER},
            "path": ".github/workflows/verify.yml",
            "run_attempt": 1,
            "created_at": (self.now - timedelta(hours=1)).isoformat(),
            "status": "completed",
            "conclusion": "success",
            "updated_at": self.now.isoformat(),
            "referenced_workflows": [
                {
                    "path": self.source["repository"]
                    + "/.github/workflows/"
                    + name
                    + "@"
                    + sha,
                    "sha": sha,
                    "ref": "refs/heads/" + branch,
                }
                for name in sorted(module.CONSOLIDATED_WORKFLOWS)
            ],
        }
        self.review = {
            "number": 1,
            "merged": True,
            "state": "closed",
            "draft": False,
            "merged_at": self.now.isoformat(),
            "merge_commit_sha": sha,
            "merged_by": {"login": module.OWNER},
            "base": {"ref": branch, "repo": repository},
            "head": {
                "ref": "dev" if branch == "staging" else "staging",
                "repo": repository,
            },
        }
        jobs = []
        for index, (name, required) in enumerate(
            module.CONSOLIDATED_JOB_STEPS.items(), 1
        ):
            jobs.append(
                {
                    "id": index,
                    "name": name,
                    "run_id": self.run["id"],
                    "head_sha": sha,
                    "head_branch": branch,
                    "run_attempt": 1,
                    "status": "completed",
                    "conclusion": "success",
                    "steps": [
                        {"name": step, "status": "completed", "conclusion": "success"}
                        for step in (required or ["Observed hosted job"])
                    ],
                }
            )
        self.jobs = {"total_count": len(jobs), "jobs": jobs}
        self.runs = {"total_count": 1, "workflow_runs": [copy.deepcopy(self.run)]}
        self.tree = {
            "sha": self.source["source_tree"],
            "truncated": False,
            "tree": [
                {
                    "path": self.source["source_path"],
                    "mode": "040000",
                    "type": "tree",
                    "sha": self.source["product_source_tree"],
                }
            ],
        }
        self.archives = {}
        self.manifests = {}
        self.artifacts = {"total_count": 0, "artifacts": []}
        for index, (name, member) in enumerate(
            module.CONSOLIDATED_ARTIFACTS[product].items(), 1
        ):
            record = {
                key: self.source[key]
                for key in ("repository", "source_path", "product_source_tree")
            }
            record.update(verification_only=True, release_authorized=False)
            if product == "drone":
                component = member.removesuffix(".json")
                record.update(
                    source_commit=sha,
                    source_tree=self.source["source_tree"],
                    component=component,
                    mode="simulation",
                )
                if component == "backend":
                    record.update(
                        image_id="sha256:" + "d" * 64,
                        runtime_image_id="sha256:" + "e" * 64,
                        architecture="amd64",
                    )
                else:
                    record["files"] = {
                        "index.html": "f" * 64,
                        "assets/app.js": "0" * 64,
                    }
            else:
                record.update(
                    schema_version=2,
                    candidate_commit=sha,
                    image_digest="sha256:" + "d" * 64,
                    security="PASS",
                )
            self.manifests[name] = record
            self.artifacts["artifacts"].append(
                {
                    "id": index,
                    "name": name,
                    "expired": False,
                    "workflow_run": {
                        "id": self.run["id"],
                        "repository_id": self.source["repository_id"],
                        "head_repository_id": self.source["repository_id"],
                        "head_branch": branch,
                        "head_sha": sha,
                    },
                }
            )
            self.repack(name)
        self.artifacts["total_count"] = len(self.artifacts["artifacts"])
        self.api_values = {
            "user": {"login": module.OWNER},
            self.prefix.rstrip("/"): repository,
            self.prefix + "branches/" + branch: {"commit": {"sha": sha}},
            self.prefix + "git/commits/" + sha: {
                "sha": sha,
                "tree": {"sha": self.source["source_tree"]},
            },
            self.prefix + "git/trees/" + self.source["source_tree"]: self.tree,
            self.prefix + "commits/" + sha + "/pulls?per_page=100": [
                copy.deepcopy(self.review)
            ],
            self.prefix + "pulls/1": self.review,
            self.prefix
            + "actions/workflows/verify.yml/runs?head_sha="
            + sha
            + "&per_page=100": self.runs,
            self.prefix + "actions/runs/123": self.run,
            self.prefix + "actions/runs/123/jobs?filter=latest&per_page=100": self.jobs,
            self.prefix + "actions/runs/123/artifacts?per_page=100": self.artifacts,
        }
        self.calls = []
        self.github = module.GitHub("synthetic-source-fixture")
        self.github.api = self.api

    def api(self, path):
        self.calls.append(path)
        if path not in self.api_values:
            raise AssertionError(
                "Unexpected network request in synthetic source fixture: " + path
            )
        return copy.deepcopy(self.api_values[path])

    def repack(self, name, *, member=None, raw=None, extra=None, mode=None):
        target = next(row for row in self.artifacts["artifacts"] if row["name"] == name)
        member = (
            member or self.module.CONSOLIDATED_ARTIFACTS[self.source["product"]][name]
        )
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo(member)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (
                (stat.S_IFREG | 0o600) if mode is None else mode
            ) << 16
            archive.writestr(
                info, json.dumps(self.manifests[name]).encode() if raw is None else raw
            )
            if extra is not None:
                archive.writestr(extra, b"untrusted extra artifact member")
        contents = buffer.getvalue()
        self.archives[name] = base64.b64encode(contents).decode()
        target.update(
            size_in_bytes=len(contents),
            digest="sha256:" + hashlib.sha256(contents).hexdigest(),
        )

    def verify(self):
        self.module.consolidated_source_profile(self.source, self.policy)
        with patch.object(self.module, "timestamp", side_effect=lambda: self.now):
            return self.github.verify_source(self.source, self.archives)
