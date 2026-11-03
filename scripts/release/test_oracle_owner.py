"""Disposable owner-custody/CI fixtures; no hosted or production acceptance claim."""

import base64
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import oracle_owner as o

SCHEMA = """CREATE TABLE records(sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,scope TEXT NOT NULL,envelope TEXT NOT NULL,parent TEXT REFERENCES records(id),created TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0); CREATE TABLE contexts(id TEXT PRIMARY KEY REFERENCES records(id),state TEXT NOT NULL); CREATE TABLE deployments(id TEXT PRIMARY KEY REFERENCES records(id),authorization TEXT NOT NULL UNIQUE REFERENCES records(id),qualification TEXT NOT NULL UNIQUE REFERENCES records(id),state TEXT NOT NULL);"""


class CI:
    def __init__(self):
        self.source = "a" * 40
        self.fail = False

    def owner(self):
        return o.OWNER

    def qualify(self, binding):
        o.require(
            not self.fail and binding["candidate_commit"] == self.source,
            "CI/source changed",
        )
        return [{"run_id": 123, "run_attempt": 1, "workflow": "real-CI-fixture"}]


class OwnerStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(
            dir="/private/tmp" if Path("/private/tmp").exists() else None
        ) as folder:
            key = Path(folder) / "key"
            subprocess.run(
                [o.OPENSSL, "genpkey", "-algorithm", "ED25519", "-out", str(key)],
                check=True,
                capture_output=True,
            )
            cls.key = key.read_bytes()
            cls.public = subprocess.check_output(
                [o.OPENSSL, "pkey", "-in", str(key), "-pubout"]
            )

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            dir=Path.home()
            if os.getuid() == 0
            else "/private/tmp"
            if Path("/private/tmp").exists()
            else None
        )
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.runner = self.root / "runner.py"
        self.runner.write_text(
            """import datetime,json,pathlib,sys
p=pathlib.Path(__file__).parent
x=json.load(sys.stdin);phase=sys.argv[1]
s=json.loads((p/'settings.json').read_text())
if phase=='deploy':
 c=p/'provider-count';c.write_text(str(int(c.read_text())+1)if c.exists()else'1')
 if s.get('interrupt'):raise SystemExit(3)
b=x['binding'].copy()
if s.get('wrong_binding'):b[s['wrong_binding']]='wrong'
v={'binding':b,'environment':'staging'if phase=='staging'else'production','checks':{k:'PASS'for k in CHECKS[phase]+['workflow']},'workflow_run_id':0,'operation_id':x['context'].get('operation_id'),'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'deployment_id':s.get('deployment_id','actual-provider-fixture-1')}
if s.get('check'):v['checks'][s['check']]=s.get('status','FAIL')
if s.get('run_id')is not None:v['workflow_run_id']=s['run_id']
print(json.dumps(v))
""".replace("CHECKS", repr({k: sorted(v) for k, v in o.REQUIRED_CHECKS.items()}))
        )
        self.settings({})
        self.binding = {
            "repository": "DruHustle/onesa-company-portal",
            "component": "api",
            "candidate_commit": "a" * 40,
            "source_tree": "b" * 40,
            "source_sha256": "c" * 64,
            "build_sha256": "d" * 64,
            "artifact_digest": "sha256:" + "e" * 64,
            "configuration_sha256": {"staging": "f" * 64, "production": "1" * 64},
            "target_environment": "production",
        }
        self.policy = {
            "components": {
                self.binding["repository"] + ":api": {
                    "qualification_attempt_limit": 100,
                    "runners": {
                        p: {
                            "path": str(self.runner),
                            "sha256": o.file_digest(self.runner),
                            "timeout_seconds": 10,
                        }
                        for p in o.PHASES
                    },
                    "required_checks": {
                        p: [*sorted(o.REQUIRED_CHECKS[p]), "workflow"] for p in o.PHASES
                    },
                }
            }
        }
        for name, body in [
            ("signing.pem", self.key),
            ("public.pem", self.public),
            ("policy.json", o.canonical(self.policy)),
        ]:
            (self.root / name).write_bytes(body)
        trust = {
            "installation_id": str(uuid4()),
            "verifier_sha256": o.file_digest(o.__file__),
            "policy_sha256": o.file_digest(self.root / "policy.json"),
            "key_id": o.file_digest(self.root / "public.pem"),
            "openssl_sha256": o.file_digest(o.OPENSSL),
            "runner_root": str(self.root),
        }
        (self.root / "trust.json").write_bytes(o.canonical(trust))
        # Never consult the host's production commissioning registry, including
        # when these disposable fixtures run as root inside a test container.
        self.anchor = self.root / "fixture-anchor.json"
        self.commission_fixture()
        self.enterContext(patch.object(o, "ANCHOR", self.anchor))
        db = sqlite3.connect(self.root / "records.sqlite")
        db.executescript(SCHEMA)
        db.close()
        for file in self.root.iterdir():
            file.chmod(0o600)
        self.ci = CI()
        self.store = self.open()
        self.addCleanup(self.store.db.close)

    def open(self):
        return o.Store(self.root, self.ci, uid=os.getuid())

    def commission_fixture(self):
        trust = json.loads((self.root / "trust.json").read_bytes())
        self.anchor.write_bytes(
            o.canonical(
                {
                    "schema_version": 1,
                    "installations": {str(self.root): {**trust, "active": True}},
                }
            )
        )
        self.anchor.chmod(0o600)

    def settings(self, value):
        (self.root / "settings.json").write_bytes(o.canonical(value))
        (self.root / "settings.json").chmod(0o600)

    def context(self):
        return self.store.issue_context(self.binding)["record"]["id"]

    def qualified(self):
        return self.store.qualify(self.context())["record"]["id"]

    def authorization(self):
        return self.store.authorize(self.qualified())["record"]["id"]

    def test_valid_owner_run_chain_keeps_actual_CI_and_observed_identity(self):
        context = self.context()
        q = self.store.qualify(context)
        self.assertEqual(q["record"]["workflow_run_id"], 0)
        self.assertEqual(q["record"]["details"]["ci"][0]["run_id"], 123)
        self.assertEqual(q["signature"]["algorithm"], "Ed25519")
        auth = self.store.authorize(q["record"]["id"])
        result = self.store.deploy(auth["record"]["id"])
        self.assertEqual(result["record"]["kind"], "production-verification")
        self.assertEqual(
            result["record"]["details"]["observations"]["deployment_id"],
            "actual-provider-fixture-1",
        )
        self.assertFalse(auth["record"]["details"]["physical_hardware_authorized"])

    def test_signature_tampering_and_foreign_key_refused(self):
        id_ = self.context()
        value = self.store.get(id_)
        for key in [
            "candidate_commit",
            "component",
            "target_environment",
            "artifact_digest",
        ]:
            with self.subTest(field=key):
                bad = json.loads(json.dumps(value))
                bad["record"]["binding"][key] = "modified"
                self.store.db.execute(
                    "UPDATE records SET envelope=? WHERE id=?",
                    (o.canonical(bad).decode(), id_),
                )
                with self.assertRaises(o.Rejected):
                    self.store.get(id_)
        bad = json.loads(json.dumps(value))
        bad["signature"]["key_id"] = "0" * 64
        self.store.db.execute(
            "UPDATE records SET envelope=? WHERE id=?", (o.canonical(bad).decode(), id_)
        )
        with self.assertRaises(o.Rejected):
            self.store.get(id_)

    def test_unsigned_caller_record_is_not_authorization(self):
        id_ = self.context()
        self.store.db.execute(
            "UPDATE records SET envelope=? WHERE id=?",
            (o.canonical({"record": {"decision": "approve"}}).decode(), id_),
        )
        with self.assertRaises((KeyError, o.Rejected)):
            self.store.get(id_)

    def test_record_zero_without_owner_provenance_or_actions_authority_refused(self):
        for change in [
            {"workflow_run_id": 999},
            {"owner_provenance": {}},
            {"kind": "actions-authorization"},
        ]:
            id_ = self.context()
            envelope = self.store.get(id_)
            envelope["record"].update(change)
            envelope["signature"]["value"] = base64.b64encode(
                o.signed_bytes(
                    self.root / "signing.pem",
                    self.root / "public.pem",
                    o.canonical(envelope["record"]),
                )
            ).decode()
            self.store.db.execute(
                "UPDATE records SET envelope=? WHERE id=?",
                (o.canonical(envelope).decode(), id_),
            )
            with self.assertRaises((KeyError, o.Rejected)):
                self.store.get(id_, "context")

    def test_expiry_and_revoked_ancestor_refuse_promotion(self):
        q = self.qualified()
        self.store.clock = lambda: o.timestamp() + timedelta(hours=2)
        with self.assertRaises(o.Rejected):
            self.store.authorize(q)
        self.store.clock = o.timestamp
        parent = self.store.get(q)["record"]["parent"]
        self.store.revoke(parent)
        with self.assertRaises(o.Rejected):
            self.store.authorize(q)

    def test_wrong_component_environment_artifact_binding_refused(self):
        id_ = self.context()
        for key in [
            "candidate_commit",
            "component",
            "target_environment",
            "artifact_digest",
            "build_sha256",
            "configuration_sha256",
        ]:
            bad = dict(self.binding)
            bad[key] = "foreign"
            with self.subTest(field=key), self.assertRaises(o.Rejected):
                self.store.get(id_, binding=bad)

    def test_incomplete_policy_refuses_context(self):
        bad = dict(self.binding)
        bad.pop("source_sha256")
        with self.assertRaises(o.Rejected):
            self.store.issue_context(bad)
        bad = dict(self.binding)
        bad["component"] = "uncommissioned"
        with self.assertRaises(o.Rejected):
            self.store.issue_context(bad)

    def test_failed_skipped_or_missing_hosted_checks_consume_context(self):
        for status in ["FAIL", "SKIPPED", None]:
            with self.subTest(status=status):
                self.settings({"check": "workflow", "status": status})
                id_ = self.context()
                with self.assertRaises(o.Rejected):
                    self.store.qualify(id_)
                self.settings({})
                with self.assertRaises(o.Rejected):
                    self.store.qualify(id_)

    def test_wrong_observed_binding_refuses_qualification(self):
        for key in [
            "candidate_commit",
            "component",
            "artifact_digest",
            "configuration_sha256",
        ]:
            with self.subTest(field=key):
                self.settings({"wrong_binding": key})
                id_ = self.context()
                with self.assertRaises(o.Rejected):
                    self.store.qualify(id_)

    def test_context_cannot_be_reused(self):
        id_ = self.context()
        self.store.qualify(id_)
        with self.assertRaises(o.Rejected):
            self.store.qualify(id_)

    def test_concurrent_context_consumers_only_one_runs(self):
        id_ = self.context()
        barrier = threading.Barrier(2)

        def consume():
            store = self.open()
            try:
                barrier.wait()
                try:
                    store.qualify(id_)
                    return True
                except o.Rejected:
                    return False
            finally:
                store.db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(
                sorted(pool.map(lambda _: consume(), range(2))), [False, True]
            )

    def test_changed_source_or_CI_after_qualification_refuses_authorization(self):
        q = self.qualified()
        self.ci.source = "9" * 40
        with self.assertRaises(o.Rejected):
            self.store.authorize(q)

    def test_new_failed_owner_attempt_invalidates_previous_authorization(self):
        q = self.qualified()
        auth = self.store.authorize(q)["record"]["id"]
        self.ci.fail = True
        with self.assertRaises(o.Rejected):
            self.store.authorize(q)
        self.ci.fail = False
        with self.assertRaises(o.Rejected):
            self.store.deploy(auth)

    def test_interrupted_deployment_is_inspect_only_without_second_provider_request(
        self,
    ):
        auth = self.authorization()
        self.settings({"interrupt": True})
        with self.assertRaises(o.Rejected):
            self.store.deploy(auth)
        self.settings({})
        result = self.store.deploy(auth)
        self.assertEqual(result["action"], "INSPECT_ONLY_NO_DUPLICATE_REQUEST")
        self.assertEqual((self.root / "provider-count").read_text(), "1")
        self.assertEqual(
            self.store.inspect(result["operation_id"])["state"], "request-recorded"
        )
        self.store.inspect(result["operation_id"], observe=True)
        self.assertEqual((self.root / "provider-count").read_text(), "1")

    def test_concurrent_deployments_only_one_provider_request(self):
        auth = self.authorization()
        barrier = threading.Barrier(2)

        def deploy():
            store = self.open()
            try:
                barrier.wait()
                try:
                    return store.deploy(auth)
                except o.Rejected:
                    return {"state": "reserved-by-other-request"}
            finally:
                store.db.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: deploy(), range(2)))
        self.assertEqual(len(results), 2)
        self.assertEqual((self.root / "provider-count").read_text(), "1")

    def test_reauthorization_does_not_reuse_a_consumed_qualification(self):
        qualification = self.qualified()
        first = self.store.authorize(qualification)["record"]["id"]
        self.store.deploy(first)
        second = self.store.authorize(qualification)["record"]["id"]
        result = self.store.deploy(second)
        self.assertEqual(result["action"], "INSPECT_ONLY_NO_DUPLICATE_REQUEST")
        self.assertEqual((self.root / "provider-count").read_text(), "1")

    def test_new_qualification_cannot_duplicate_an_uncertain_component_request(self):
        first = self.authorization()
        self.settings({"interrupt": True})
        with self.assertRaises(o.Rejected):
            self.store.deploy(first)
        self.settings({})
        second = self.authorization()
        with self.assertRaises(o.Rejected):
            self.store.deploy(second)
        self.assertEqual((self.root / "provider-count").read_text(), "1")

    def test_changed_runner_policy_or_key_pin_refused(self):
        id_ = self.context()
        self.runner.write_text("raise SystemExit(0)")
        with self.assertRaises(o.Rejected):
            self.store.qualify(id_)
        (self.root / "policy.json").write_text("{}")
        with self.assertRaises(o.Rejected):
            self.open()

    def test_observed_deployment_must_match_immediate_provider_receipt(self):
        auth = self.authorization()
        result = self.store.deploy(auth)
        operation = result["record"]["parent"]
        self.settings({"deployment_id": "different-provider-id"})
        with self.assertRaises(o.Rejected):
            self.store.inspect(operation, observe=True)

    def test_expired_operation_remains_inspectable_without_provider_write(self):
        auth = self.authorization()
        self.settings({"interrupt": True})
        with self.assertRaises(o.Rejected):
            self.store.deploy(auth)
        id_ = self.store.db.execute("SELECT id FROM deployments").fetchone()[0]
        self.store.clock = lambda: o.timestamp() + timedelta(days=2)
        self.assertFalse(self.store.inspect(id_)["provider_write"])
        self.assertEqual((self.root / "provider-count").read_text(), "1")

    def test_uncommissioned_key_generation_does_not_establish_trust(self):
        with self.assertRaises(o.Rejected):
            o.commissioned(
                {"schema_version": 1, "installations": {}}, self.root, self.store.trust
            )
        installed = {
            **{
                k: self.store.trust[k]
                for k in [
                    "installation_id",
                    "key_id",
                    "verifier_sha256",
                    "policy_sha256",
                ]
            },
            "active": False,
        }
        with self.assertRaises(o.Rejected):
            o.commissioned(
                {"schema_version": 1, "installations": {str(self.root): installed}},
                self.root,
                self.store.trust,
            )
        (self.root / "trust.json").unlink()
        with self.assertRaises(FileNotFoundError):
            self.open()

    def test_actions_cannot_invoke_owner_identity(self):
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            self.assertRaises(o.Rejected),
        ):
            o.GitHub("not-a-secret-fixture").owner()

    def test_foreign_owner_token_is_refused(self):
        github = o.GitHub("not-a-secret-fixture")
        with (
            patch.object(github, "api", return_value={"login": "not-owner"}),
            self.assertRaises(o.Rejected),
        ):
            github.owner()

    def test_policy_cannot_drop_mandatory_checks(self):
        for phase in o.PHASES:
            policy = json.loads(json.dumps(self.policy))
            policy["components"][self.binding["repository"] + ":api"][
                "required_checks"
            ][phase] = ["workflow"]
            with self.subTest(phase=phase), self.assertRaises(o.Rejected):
                o.binding_valid(self.binding, policy)

    def test_actions_observation_refused_even_with_pinned_runner(self):
        self.settings({"run_id": 999})
        with self.assertRaises(o.Rejected):
            self.store.qualify(self.context())

    def test_source_specific_context_allowance_cannot_renew_implicitly(self):
        self.store.policy["components"][self.binding["repository"] + ":api"][
            "qualification_attempt_limit"
        ] = 1
        context = self.context()
        self.store.qualify(context)
        with self.assertRaises(o.Rejected):
            self.context()

    def test_robot_native_and_identity_scopes_each_keep_one_context_for_same_source(
        self,
    ):
        template = self.store.policy["components"][self.binding["repository"] + ":api"]
        identifiers = set()
        for component in ["api", "frontend", "identity-api", "identity-frontend"]:
            binding = {
                **self.binding,
                "repository": "DruHustle/onesa-robot",
                "component": component,
            }
            self.store.policy["components"][binding["repository"] + ":" + component] = {
                **template,
                "qualification_attempt_limit": 1,
            }
            context = self.store.issue_context(binding)["record"]
            self.assertEqual(context["binding"], binding)
            identifiers.add(context["id"])
            with self.assertRaisesRegex(o.Rejected, "allowance exhausted"):
                self.store.issue_context(binding)
        self.assertEqual(len(identifiers), 4)

    def test_commission_requires_explicit_owner_bootstrap(self):
        with (
            patch.dict(os.environ, {"SUDO_USER": "", "GITHUB_ACTIONS": "true"}),
            self.assertRaises(o.Rejected),
        ):
            o.commission(
                self.root,
                o.file_digest(o.__file__),
                o.file_digest(self.root / "policy.json"),
                o.file_digest(self.root / "public.pem"),
            )


class GitHubEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.binding = {
            "repository": "DruHustle/onesa-company-portal",
            "candidate_commit": "a" * 40,
            "source_tree": "b" * 40,
        }
        self.bad = {}
        self.github = o.GitHub("not-a-secret-fixture")
        self.github.api = self.api

    def api(self, path):
        sha = self.binding["candidate_commit"]
        repo = self.binding["repository"]
        native_workflow = o.POLICIES[repo.split("/")[1]][0]
        leaf = path.removeprefix("repos/" + repo + "/")
        if leaf == "branches/main":
            return {"commit": {"sha": self.bad.get("main", sha)}}
        if leaf == "git/commits/" + sha:
            return {"tree": {"sha": self.bad.get("tree", self.binding["source_tree"])}}
        if leaf.startswith("commits/"):
            return [{"number": 1}]
        if leaf == "pulls/1":
            return {
                "merged_at": "now",
                "merge_commit_sha": sha,
                "base": {"ref": "main"},
                "merged_by": {"login": self.bad.get("merger", o.OWNER)},
            }
        if leaf.startswith("actions/workflows/"):
            return {
                "total_count": 0 if self.bad.get("missing_run") else 1,
                "workflow_runs": []
                if self.bad.get("missing_run")
                else [
                    {
                        "id": 1 if native_workflow in leaf else 2,
                        "event": "push",
                        "head_branch": "main",
                    }
                ],
            }
        if leaf in ["actions/runs/1", "actions/runs/2"]:
            run = {
                "id": int(leaf.rsplit("/", 1)[1]),
                "repository": {"full_name": repo},
                "head_repository": {"full_name": repo},
                "head_sha": sha,
                "head_branch": "main",
                "event": "push",
                "path": ".github/workflows/"
                + (
                    native_workflow if leaf.endswith("/1") else "repository-hygiene.yml"
                ),
                "run_attempt": 1,
                "status": "completed",
                "conclusion": "success",
                "updated_at": o.timestamp().isoformat(),
            }
            run.update(self.bad.get("run", {}))
            return run
        if "/jobs?" in leaf:
            names = (
                o.POLICIES[repo.split("/")[1]][1]
                if leaf.startswith("actions/runs/1/")
                else ["publication-hygiene"]
            )
            jobs = [
                {
                    "name": n,
                    "status": "completed",
                    "conclusion": "success",
                    "steps": [
                        {
                            "name": "Release requirement: real checks",
                            "conclusion": self.bad.get("step", "success"),
                        }
                    ],
                }
                for n in names
            ]
            if self.bad.get("job"):
                jobs[0]["conclusion"] = self.bad["job"]
            if self.bad.get("missing_job"):
                jobs.pop(0)
            return {"total_count": len(jobs), "jobs": jobs}
        if "/artifacts?" in leaf:
            artifacts = [
                {
                    "id": i + 10,
                    "name": self.bad.get(
                        "artifact_prefix", "runtime-onesa-company-portal-"
                    )
                    + n,
                    "digest": "sha256:" + "f" * 64,
                    "expired": self.bad.get("expired_artifact", False),
                }
                for i, n in enumerate(["backend", "frontend"])
            ]
            return {"total_count": len(artifacts), "artifacts": artifacts}
        raise AssertionError("Unexpected fixture API path")

    def test_valid_CI_keeps_real_run_and_artifact_identity(self):
        refs = self.github.qualify(self.binding)
        self.assertEqual([r["run_id"] for r in refs], [1, 2])
        self.assertEqual(refs[0]["artifacts"][0]["id"], 10)

    def test_canonical_robot_identity_keeps_real_component_artifact_names(self):
        self.binding["repository"] = "DruHustle/onesa-robot"
        self.bad = {"artifact_prefix": "runtime-onesa-ai-robot-"}
        refs = self.github.qualify(self.binding)
        self.assertEqual(refs[0]["workflow"], "scaffold.yml")
        self.assertEqual(
            {a["name"] for a in refs[0]["artifacts"]},
            {
                "runtime-onesa-ai-robot-backend",
                "runtime-onesa-ai-robot-frontend",
            },
        )
        workflow = (
            Path(__file__).resolve().parents[2] / ".github/workflows/scaffold.yml"
        ).read_text()
        uploaded = re.findall(r"(?m)^\s+name: (runtime-[^\r\n]+)$", workflow)
        self.assertEqual(len(uploaded), 1)
        self.assertEqual(
            {
                uploaded[0].replace("${{ matrix.application }}", component)
                for component in ["backend", "frontend"]
            },
            {a["name"] for a in refs[0]["artifacts"]},
        )
        for prefix in ["runtime-onesa-robot-", "runtime-onesa-company-portal-"]:
            self.bad = {"artifact_prefix": prefix}
            with self.subTest(prefix=prefix), self.assertRaises(o.Rejected):
                self.github.qualify(self.binding)

    def test_component_artifact_names_never_authorize_a_foreign_run_repository(self):
        self.binding["repository"] = "DruHustle/onesa-robot"
        for field in ["repository", "head_repository"]:
            self.bad = {
                "artifact_prefix": "runtime-onesa-ai-robot-",
                "run": {field: {"full_name": "DruHustle/onesa-ai-robot"}},
            }
            with self.subTest(field=field), self.assertRaises(o.Rejected):
                self.github.qualify(self.binding)

    def test_source_review_and_failed_skipped_missing_CI_fail_closed(self):
        cases = [
            {"main": "9" * 40},
            {"tree": "9" * 40},
            {"merger": "foreign"},
            {"missing_run": True},
            {"missing_job": True},
            {"job": "skipped"},
            {"job": "failure"},
            {"step": "skipped"},
            {"step": "failure"},
            {"expired_artifact": True},
            {"run": {"run_attempt": 2}},
            {"run": {"head_sha": "9" * 40}},
            {"run": {"event": "workflow_dispatch"}},
            {"run": {"head_branch": "staging"}},
            {"run": {"conclusion": "failure"}},
            {"run": {"status": "in_progress"}},
            {"run": {"updated_at": (o.timestamp() - timedelta(hours=25)).isoformat()}},
        ]
        for bad in cases:
            self.bad = bad
            with self.subTest(bad=bad), self.assertRaises(o.Rejected):
                self.github.qualify(self.binding)


if __name__ == "__main__":
    unittest.main()
