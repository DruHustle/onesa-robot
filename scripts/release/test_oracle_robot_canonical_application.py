"""Real local Git/archive/Ed25519 boundaries; fixtures confer no runtime authority."""

import base64
import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oracle_frontend_build as builder
import oracle_install as installer
import oracle_owner as owner
import oracle_robot as runner
import oracle_robot_frontend as frontend
import test_oracle_install as install_fixture
import test_oracle_owner as store_fixture

# Existing canonical protocol fixtures, without a sibling checkout dependency.
from source_protocol_fixtures import SourceFixture


def pins(source):
    return {
        name: hashlib.sha256(base64.b64decode(data)).hexdigest()
        for name, data in source.archives.items()
    }


def private_fixture_setup(fixture):
    """Reuse the synthetic store with a real, private temporary parent."""
    original = tempfile.TemporaryDirectory

    def temporary(*args, **kwargs):
        if kwargs.get("dir") is None:
            kwargs["dir"] = Path.home()
        return original(*args, **kwargs)

    with patch.object(tempfile, "TemporaryDirectory", side_effect=temporary):
        fixture.setUp()


class CanonicalInstallTests(unittest.TestCase):
    def setUp(self):
        self.fixture = f = install_fixture.InstallTests()
        private_fixture_setup(f)
        self.addCleanup(f.doCleanups)
        self.source = source = SourceFixture(owner, product="robot", branch="main")
        s = source.source
        s["product_source_tree"] = f.manifest["source_tree"]
        source.tree["tree"][0]["sha"] = s["product_source_tree"]
        for name, manifest in source.manifests.items():
            manifest["product_source_tree"] = s["product_source_tree"]
            source.repack(name)
        legacy = f.github

        def api(path):
            if (
                path
                == source.prefix
                + "git/trees/"
                + s["product_source_tree"]
                + "?recursive=1"
            ):
                return {**legacy.api(path), "sha": s["product_source_tree"]}
            if path.startswith(source.prefix + "git/blobs/"):
                return legacy.api(path)
            return source.api(path)

        source.github.api = api
        source.github.owner = legacy.owner
        f.github = source.github
        f.manifest.update(s)
        f.policy["schema_version"] = 2
        f.policy["components"] = {
            key.replace(
                runner.LEGACY_REPOSITORY + ":", runner.CANONICAL_REPOSITORY + ":robot:"
            ): value
            for key, value in f.policy["components"].items()
        }
        target = installer.INSTALL_PARENT / ("robot-canonical-" + s["source_tree"])
        for name, value in f.policy["components"].items():
            script = (
                "oracle_robot.py"
                if name.endswith(":api")
                else "oracle_robot_frontend.py"
            )
            for phase in value["runners"].values():
                phase["path"] = str(target / "runners" / script)
        f.policy["toolchain"].update(
            source_binding=copy.deepcopy(s),
            source_tree=s["source_tree"],
            source_archives=pins(source),
        )
        for name, data in source.archives.items():
            f.put("source-artifacts/" + name + ".zip", base64.b64decode(data))
        f.seal()

    def test_exact_main_root_and_product_tar_remain_separate_and_non_authorizing(self):
        manifest, target, payloads, ci = self.fixture.validate()
        self.assertEqual(target.name, "robot-canonical-" + manifest["source_tree"])
        self.assertNotEqual(manifest["source_tree"], manifest["product_source_tree"])
        self.assertEqual(ci[0]["source"], self.source.source)
        self.assertTrue(ci[0]["verification_only"])
        self.assertFalse(ci[0]["release_authorized"])
        self.assertEqual(
            len([key for key in payloads if key.startswith("source-artifacts/")]), 2
        )
        self.assertFalse(
            any(
                (self.fixture.root / name).exists()
                for name in ("records.sqlite", "signing.pem", "installation.json")
            )
        )

    def test_source_policy_alone_is_not_component_commissioning(self):
        self.fixture.policy = copy.deepcopy(self.source.policy)
        self.fixture.seal()
        with self.assertRaises(owner.Rejected):
            self.fixture.validate()

    def test_foreign_main_root_subtree_and_fixed_zip_tampering_fail_closed(self):
        for key, value in [
            ("repository_id", 1),
            ("product", "portal"),
            ("source_path", "onesa-ai-platform"),
            ("branch", "staging"),
            ("source_tree", "f" * 40),
            ("product_source_tree", "e" * 40),
        ]:
            original = self.fixture.manifest[key]
            self.fixture.manifest[key] = value
            self.fixture.seal()
            with self.subTest(key=key), self.assertRaises(owner.Rejected):
                self.fixture.validate()
            self.fixture.manifest[key] = original
        self.fixture.seal()
        archive = (
            self.fixture.root
            / "source-artifacts"
            / (next(iter(self.source.archives)) + ".zip")
        )
        archive.write_bytes(archive.read_bytes() + b"changed")
        with self.assertRaises(owner.Rejected):
            self.fixture.validate()

    def test_literal_native_or_four_component_policy_and_unknown_components(self):
        f = self.fixture
        native = copy.deepcopy(f.policy["components"])
        for name in ("api", "frontend"):
            f.policy["components"][
                runner.CANONICAL_REPOSITORY + ":robot:identity-" + name
            ] = copy.deepcopy(native[runner.CANONICAL_REPOSITORY + ":robot:" + name])
        f.seal()
        f.validate()
        for mutation in ("partial", "foreign", "allowance"):
            original = copy.deepcopy(f.policy)
            if mutation == "partial":
                del f.policy["components"][
                    runner.CANONICAL_REPOSITORY + ":robot:identity-api"
                ]
            elif mutation == "foreign":
                f.policy["components"][
                    runner.CANONICAL_REPOSITORY + ":platform:api"
                ] = next(iter(native.values()))
            else:
                next(iter(f.policy["components"].values()))[
                    "qualification_attempt_limit"
                ] = 2
            f.seal()
            with self.subTest(mutation=mutation), self.assertRaises(owner.Rejected):
                f.validate()
            f.policy = original
        for component in (
            "docs-public",
            "docs-internal",
            "worker",
            "portal",
            "unknown",
        ):
            with self.subTest(component=component), self.assertRaises(owner.Rejected):
                self.source.github.qualify(
                    {**self.source.source, "component": component}, self.source.archives
                )

    def test_failed_first_attempt_and_missing_required_checks_cannot_install(self):
        self.source.run["run_attempt"] = 2
        with self.assertRaises(owner.Rejected):
            self.fixture.validate()
        self.source.run["run_attempt"] = 1
        item = next(iter(self.fixture.policy["components"].values()))
        item["required_checks"]["staging"].remove("persistence")
        self.fixture.seal()
        with self.assertRaises(owner.Rejected):
            self.fixture.validate()

    def test_real_actions_custody_refusal_remains_before_api(self):
        github = owner.GitHub("synthetic-only")
        with (
            patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}),
            patch.object(github, "api") as api,
        ):
            with self.assertRaisesRegex(
                owner.Rejected, "Owner custody cannot run in Actions"
            ):
                github.owner()
            api.assert_not_called()


class CanonicalStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store_fixture.OwnerStoreTests.setUpClass()

    def setUp(self):
        self.fixture = f = store_fixture.OwnerStoreTests()
        private_fixture_setup(f)
        self.addCleanup(f.doCleanups)
        f.store.db.close()
        self.source = SourceFixture(owner, product="robot", branch="main")
        self.source.github.owner = f.ci.owner
        f.ci = self.source.github
        self.binding = {**f.binding, **self.source.source, "component": "api"}
        template = copy.deepcopy(next(iter(f.policy["components"].values())))
        template["qualification_attempt_limit"] = 1
        f.policy = {
            "schema_version": 2,
            "components": {
                runner.CANONICAL_REPOSITORY + ":robot:" + name: copy.deepcopy(template)
                for name in owner.ROBOT_NATIVE_COMPONENTS
            },
            "toolchain": {
                "source_binding": copy.deepcopy(self.source.source),
                "source_commit": self.binding["candidate_commit"],
                "source_tree": self.binding["source_tree"],
                "source_sha256": self.binding["source_sha256"],
                "source_archives": pins(self.source),
            },
        }
        (f.root / "policy.json").write_bytes(owner.canonical(f.policy))
        trust = json.loads((f.root / "trust.json").read_bytes())
        trust["policy_sha256"] = owner.file_digest(f.root / "policy.json")
        (f.root / "trust.json").write_bytes(owner.canonical(trust))
        f.commission_fixture()
        installed = f.root / "installed"
        installed.mkdir(mode=0o700)
        (installed / "oracle_owner.py").write_bytes(Path(owner.__file__).read_bytes())
        (installed / "oracle_owner.py").chmod(0o600)
        (installed / "source-artifacts").mkdir(mode=0o700)
        for name, data in self.source.archives.items():
            path = installed / "source-artifacts" / (name + ".zip")
            path.write_bytes(base64.b64decode(data))
            path.chmod(0o600)
        self.enterContext(
            patch.object(owner, "__file__", str(installed / "oracle_owner.py"))
        )
        self.store = f.open()
        self.addCleanup(self.store.db.close)
        self.installed = installed

    def test_real_signed_chain_rechecks_fixed_source_and_does_not_reuse_context(self):
        context = self.store.issue_context(self.binding)["record"]["id"]
        with self.assertRaises(owner.Rejected):
            self.store.issue_context(
                {
                    **self.binding,
                    "configuration_sha256": {
                        "staging": "2" * 64,
                        "production": "3" * 64,
                    },
                }
            )
        qualification = self.store.qualify(context)
        self.assertEqual(qualification["record"]["workflow_run_id"], 0)
        self.assertTrue(
            qualification["record"]["details"]["ci"][0]["verification_only"]
        )
        authorization = self.store.authorize(qualification["record"]["id"])
        accepted = self.store.deploy(authorization["record"]["id"])
        self.assertEqual(accepted["record"]["binding"], self.binding)
        self.assertEqual(
            self.store.db.execute(
                "SELECT scope FROM records WHERE id=?", (context,)
            ).fetchone()[0],
            runner.CANONICAL_REPOSITORY + ":robot:api",
        )
        self.assertEqual((self.fixture.root / "provider-count").read_text(), "1")
        self.assertGreaterEqual(
            self.source.calls.count(self.source.prefix + "branches/main"), 4
        )

    def test_changed_installed_zip_never_qualifies(self):
        context = self.store.issue_context(self.binding)["record"]["id"]
        archive = next((self.installed / "source-artifacts").glob("*.zip"))
        archive.write_bytes(archive.read_bytes() + b"changed")
        with self.assertRaises(owner.Rejected):
            self.store.qualify(context)
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM records WHERE kind='qualification'"
            ).fetchone()[0],
            0,
        )
        self.assertFalse((self.fixture.root / "provider-count").exists())

    def test_changed_main_after_qualification_refuses_authorization(self):
        context = self.store.issue_context(self.binding)["record"]["id"]
        qualification = self.store.qualify(context)["record"]["id"]
        self.source.api_values[self.source.prefix + "branches/main"]["commit"][
            "sha"
        ] = "e" * 40
        with self.assertRaises(owner.Rejected):
            self.store.authorize(qualification)
        self.assertFalse((self.fixture.root / "provider-count").exists())
        self.assertEqual(
            self.store.db.execute(
                "SELECT COUNT(*) FROM records WHERE kind='authorization'"
            ).fetchone()[0],
            0,
        )

    def test_changed_main_after_authorization_refuses_provider_creation(self):
        context = self.store.issue_context(self.binding)["record"]["id"]
        qualification = self.store.qualify(context)["record"]["id"]
        authorization = self.store.authorize(qualification)["record"]["id"]
        self.source.api_values[self.source.prefix + "branches/main"]["commit"][
            "sha"
        ] = "e" * 40
        with self.assertRaises(owner.Rejected):
            self.store.deploy(authorization)
        self.assertFalse((self.fixture.root / "provider-count").exists())
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM deployments").fetchone()[0], 0
        )

    def test_actual_runner_custody_refuses_a_writable_ancestor(self):
        shared = self.fixture.root / "writable"
        shared.mkdir(mode=0o700)
        child = shared / "private-record.json"
        child.write_bytes(b"{}")
        child.chmod(0o600)
        shared.chmod(0o777)
        try:
            with self.assertRaisesRegex(runner.Refused, "CUSTODY_ANCESTOR"):
                runner.private(child, uid=os.getuid())
        finally:
            shared.chmod(0o700)


class RetainedVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        store_fixture.OwnerStoreTests.setUpClass()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Distinct real verifier bytes and module identity, not a mocked digest.
        self.original = self.root / "original_owner.py"
        self.original.write_bytes(
            Path(owner.__file__).read_bytes()
            + b"\n# Original synthetic installed verifier version.\n"
        )
        self.original.chmod(0o600)
        spec = importlib.util.spec_from_file_location(
            "original_robot_verifier_fixture", self.original
        )
        self.original_owner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.original_owner)
        self.fixture = f = store_fixture.OwnerStoreTests()
        with patch.object(store_fixture, "o", self.original_owner):
            private_fixture_setup(f)
        self.addCleanup(f.doCleanups)
        f.store.db.close()
        f.binding["repository"] = runner.LEGACY_REPOSITORY
        profile = next(iter(f.policy["components"].values()))
        f.policy["components"] = {runner.LEGACY_REPOSITORY + ":api": profile}
        (f.root / "policy.json").write_bytes(owner.canonical(f.policy))
        trust = json.loads((f.root / "trust.json").read_bytes())
        trust["policy_sha256"] = owner.file_digest(f.root / "policy.json")
        (f.root / "trust.json").write_bytes(owner.canonical(trust))
        with patch.object(store_fixture, "o", self.original_owner):
            f.commission_fixture()
            f.store = f.open()
        self.addCleanup(f.store.db.close)
        context = f.store.issue_context(f.binding)["record"]["id"]
        qualification = f.store.qualify(context)["record"]["id"]
        authorization = f.store.authorize(qualification)["record"]["id"]
        accepted = f.store.deploy(authorization)
        self.operation = accepted["record"]["parent"]
        self.context = context
        self.consumer = {
            **f.binding,
            **SourceFixture(owner, product="robot", branch="main").source,
            "candidate_commit": "d" * 40,
        }
        self.retained = {
            "binding": f.binding,
            "operation_id": self.operation,
            "verifier_store": str(f.root),
            "verifier_path": str(self.original),
        }
        self.registry = {"installations": {str(f.root): {**trust, "active": True}}}
        self.enterContext(
            patch.object(
                runner,
                "private",
                side_effect=lambda p, **kw: (
                    owner.private(
                        Path(p), uid=os.getuid(), directory=kw.get("directory", False)
                    ),
                    Path(p),
                )[1],
            )
        )
        self.enterContext(
            patch.object(
                runner,
                "read",
                side_effect=lambda p: (
                    self.registry
                    if str(p) == "/etc/onesa/oracle-release-trust.json"
                    else json.loads(Path(p).read_bytes())
                ),
            )
        )

    def inspect(self):
        return runner.verify_retained_dependency(
            self.consumer,
            self.retained,
            {"verifier_store": "not-the-original-store"},
            "api",
        )

    def test_original_verifier_key_and_chain_are_read_without_new_authority_or_store_writes(
        self,
    ):
        before = {
            p.name: p.read_bytes() for p in self.fixture.root.iterdir() if p.is_file()
        }
        names = set(self.fixture.root.iterdir())
        self.assertNotEqual(runner.sha(runner.VERIFIER), runner.sha(self.original))
        with self.assertRaisesRegex(runner.Refused, "ISSUER_CUSTODY_PIN_CHANGED"):
            runner.verify_invocation(
                self.retained["binding"],
                {"operation_id": self.operation, "read_only": True},
                "production",
                {"verifier_store": str(self.fixture.root)},
                dependency=True,
            )
        records = self.inspect()
        self.assertEqual(records[0]["kind"], "production-verification")
        self.assertTrue(all(r["binding"] == self.retained["binding"] for r in records))
        self.assertEqual(
            before,
            {
                p.name: p.read_bytes()
                for p in self.fixture.root.iterdir()
                if p.is_file()
            },
        )
        self.assertEqual(names, set(self.fixture.root.iterdir()))

    def test_foreign_or_relabelled_record_uncommissioned_anchor_and_changed_original_fail(
        self,
    ):
        original_binding = self.retained["binding"]
        for key, value in [
            ("repository", runner.CANONICAL_REPOSITORY),
            ("candidate_commit", "f" * 40),
            ("component", "frontend"),
            ("target_environment", "staging"),
        ]:
            self.retained["binding"] = {**original_binding, key: value}
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.inspect()
        self.retained["binding"] = original_binding
        self.registry["installations"][str(self.fixture.root)]["active"] = False
        with self.assertRaises(runner.Refused):
            self.inspect()
        self.registry["installations"][str(self.fixture.root)]["active"] = True
        self.original.write_bytes(self.original.read_bytes() + b"# changed\n")
        with self.assertRaisesRegex(runner.Refused, "ISSUER_CUSTODY_PIN_CHANGED"):
            self.inspect()

    def test_retained_reader_cannot_authorize_mutation_or_ignore_revocation(self):
        for phase in ("staging", "operations", "deploy"):
            with self.subTest(phase=phase), self.assertRaises(runner.Refused):
                runner.verify_invocation(
                    self.retained["binding"],
                    {"operation_id": self.operation, "read_only": True},
                    phase,
                    {"verifier_store": str(self.fixture.root)},
                    dependency=True,
                    retained_verifier=str(self.original),
                    consumer_binding=self.consumer,
                )
        self.fixture.store.revoke(self.context)
        with self.assertRaises(runner.Refused):
            self.inspect()


class CanonicalProviderTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceFixture(owner, product="robot", branch="main").source
        self.binding = {
            **self.source,
            "component": "frontend",
            "source_sha256": "d" * 64,
        }

    def test_full_provider_source_and_main_ref_are_required_for_preview(self):
        row = {
            "id": "dpl_fixture",
            "projectId": "prj_fixture",
            "target": None,
            "regions": ["iad1"],
            "meta": {
                "githubCommitSha": self.binding["candidate_commit"],
                "githubCommitRef": "main",
                **runner.source_metadata(self.binding),
            },
        }
        with patch.object(frontend, "provider", return_value=row):
            frontend.check_deployment(
                row["id"],
                self.binding,
                "staging",
                {"project_id": "prj_fixture", "serverless_function_region": "iad1"},
            )
            for key in list(runner.source_metadata(self.binding)) + ["githubCommitRef"]:
                old = row["meta"].pop(key)
                with self.subTest(key=key), self.assertRaises(runner.Refused):
                    frontend.check_deployment(
                        row["id"],
                        self.binding,
                        "staging",
                        {
                            "project_id": "prj_fixture",
                            "serverless_function_region": "iad1",
                        },
                    )
                row["meta"][key] = old

    def test_canonical_paths_and_identity_bridges_cannot_borrow_legacy_scope(self):
        for name in runner.COMPONENTS:
            current = {**self.binding, "component": name}
            old = {
                "repository": runner.LEGACY_REPOSITORY,
                "component": name,
                "candidate_commit": "a" * 40,
            }
            self.assertNotEqual(
                runner.component_scope(current), runner.component_scope(old)
            )
            self.assertNotEqual(
                runner.candidate_environment(current, "staging"),
                runner.candidate_environment(old, "staging"),
            )
            self.assertNotEqual(
                runner.frontend_configuration(current, "production"),
                runner.frontend_configuration(old, "production"),
            )
            self.assertNotEqual(runner.plan_path(current), runner.plan_path(old))
        with self.assertRaisesRegex(runner.Refused, "IDENTITY_BRIDGE"):
            runner.verify_retained_dependency(
                {**self.binding, "component": "identity-api"}, {}, {}, "frontend"
            )


class CanonicalBuildMetadataTests(unittest.TestCase):
    """Real source tar/metadata hashes; a synthetic Docker response, not a build."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir(mode=0o700)
        files = {
            "onesa-ai-robot-backend/robot_api/main.py": b"# synthetic backend\n",
            "onesa-ai-robot-backend/robot_api/inspection.py": b"# synthetic inspection\n",
            "onesa-ai-robot-frontend/vercel.json": b'{"version":2}',
        }
        for name, data in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        for command in (
            ["git", "init", "--quiet", str(self.source)],
            ["git", "-C", str(self.source), "add", "."],
        ):
            subprocess.run(command, check=True, capture_output=True)
        product_tree = (
            subprocess.check_output(["git", "-C", str(self.source), "write-tree"])
            .decode()
            .strip()
        )
        with tarfile.open(self.root / "source.tar", "w") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))
        self.binding = {
            **SourceFixture(owner, product="robot", branch="main").source,
            "product_source_tree": product_tree,
            "component": "frontend",
            "source_sha256": runner.sha(self.root / "source.tar"),
        }
        self.tool = "sha256:" + "d" * 64
        self.metadata = {
            "projectId": "prj_fixture",
            "orgId": "team_fixture",
            "projectName": "synthetic-only",
            "settings": {
                "framework": None,
                "rootDirectory": runner.frontend_root(self.binding),
                "buildCommand": None,
                "installCommand": None,
                "outputDirectory": None,
                "nodeVersion": "24.x",
            },
            "public_environment": {},
        }
        self.builds = []

    def docker(self, args, **kwargs):
        if args[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(
                args,
                0,
                json.dumps(
                    [
                        {
                            "Id": self.tool,
                            "Architecture": "amd64",
                            "Config": {"Labels": {"org.onesa.vercel-cli": builder.CLI}},
                        }
                    ]
                ).encode(),
            )
        self.assertEqual(args[1], "run")
        self.assertIn("--read-only", args)
        self.assertNotIn("--token", args)
        work = Path(
            next(
                arg.removesuffix(":/workspace:rw")
                for arg in args
                if arg.endswith(":/workspace:rw")
            )
        )
        project = json.loads((work / ".vercel/project.json").read_bytes())
        self.assertEqual(project["settings"]["rootDirectory"], frontend.APP_ROOT)
        self.assertEqual(
            json.loads((work / ".vercel/deployment-project.json").read_bytes())[
                "settings"
            ],
            self.metadata["settings"],
        )
        self.builds.append(args)
        output = work / ".vercel/output"
        (output / "static").mkdir(parents=True)
        (output / "config.json").write_text('{"version":3}')
        (output / "static/app.js").write_bytes(b"// synthetic output\n")
        return subprocess.CompletedProcess(args, 0)

    def test_product_relative_build_keeps_canonical_provider_metadata_and_hashes(self):
        for environment in runner.ENV:
            with patch.object(builder.subprocess, "run", side_effect=self.docker):
                receipt = builder.build(
                    self.root,
                    self.binding,
                    self.metadata,
                    self.tool,
                    environment=environment,
                )
            self.assertEqual(receipt["state"], "BUILT_NOT_QUALIFIED_OR_AUTHORIZED")
            self.assertEqual(receipt["binding"], self.binding)
            self.assertFalse(receipt["provider_credentials_mounted"])
            self.assertEqual("--prod" in self.builds[-1], environment == "production")
            with tarfile.open(receipt["artifact_path"]) as archive:
                project = archive.extractfile(".vercel/project.json").read()
                self.assertEqual(
                    json.loads(project)["settings"], self.metadata["settings"]
                )
                self.assertEqual(
                    hashlib.sha256(project).hexdigest(),
                    receipt["deployment_metadata_files"][".vercel/project.json"],
                )
            self.assertEqual(
                receipt["artifact_sha256"], runner.sha(receipt["artifact_path"])
            )

    def test_old_or_foreign_provider_root_fails_before_build_and_product_tree_cannot_be_root(
        self,
    ):
        for root in (frontend.APP_ROOT, "onesa-robot/frontend", "../outside"):
            metadata = copy.deepcopy(self.metadata)
            metadata["settings"]["rootDirectory"] = root
            with (
                patch.object(builder.subprocess, "run") as docker,
                self.assertRaises(runner.Refused),
            ):
                builder.build(self.root, self.binding, metadata, self.tool)
            docker.assert_not_called()
        with (
            patch.object(builder.subprocess, "run") as docker,
            self.assertRaises(runner.Refused),
        ):
            builder.build(
                self.root,
                {**self.binding, "product_source_tree": self.binding["source_tree"]},
                self.metadata,
                self.tool,
            )
        docker.assert_not_called()


class CanonicalIntegrationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.source = SourceFixture(owner, product="robot", branch="main").source
        self.api = {
            **self.source,
            "component": "api",
            "source_sha256": "d" * 64,
            "target_environment": "production",
        }
        self.front = {**self.api, "component": "frontend"}

    def test_first_paired_preview_does_not_require_new_api_acceptance(self):
        plan = {"bindings": {"api": self.api, "frontend": self.front}}
        with (
            patch.object(frontend, "validate", return_value=(Path("/fixture"), plan)),
            patch.object(
                frontend, "create", return_value=({}, Path("/preview"))
            ) as create,
            patch.object(
                frontend,
                "accepted_api",
                side_effect=AssertionError("cyclic acceptance"),
            ),
        ):
            self.assertEqual(
                frontend.prepare_preview(self.api, {"context_id": "fixture"}, plan),
                Path("/preview"),
            )
            create.assert_called_once_with(
                self.front, "staging", "fixture", Path("/fixture"), plan
            )
            for field, value in (
                ("product_source_tree", "f" * 40),
                ("repository_id", 1),
                ("branch", "staging"),
            ):
                wrong = {
                    "bindings": {
                        "api": self.api,
                        "frontend": {**self.front, field: value},
                    }
                }
                with self.subTest(field=field), self.assertRaises(runner.Refused):
                    frontend.prepare_preview(self.api, {"context_id": "fixture"}, wrong)
            self.assertEqual(create.call_count, 1)

    def test_special_recovery_and_repair_routes_cannot_be_used_by_canonical_source(
        self,
    ):
        from uuid import uuid4

        import test_oracle_robot_independent_frontend as dependency_fixture

        with self.assertRaisesRegex(runner.Refused, "CANONICAL_RECOVERY_HANDOFF"):
            runner.validate_plan(self.api, observer=object())
        with self.assertRaisesRegex(runner.Refused, "REPAIR_DEPENDENCY_SCOPE"):
            runner.verify_invocation(
                self.api,
                {"operation_id": str(uuid4()), "read_only": True},
                "production",
                {},
                repair_dependency=str(uuid4()),
            )
        f = dependency_fixture.IndependentDependencyTests()
        f.setUp()
        f.frontend.update(self.source)
        f.proof.update(verifier_path="/original/verifier.py", recovery_commission={})
        with self.assertRaisesRegex(runner.Refused, "CANONICAL_REPAIR_HANDOFF"):
            frontend.independent_api(f.plan, f.frontend)

    def test_identity_bridge_stays_same_store_and_full_source(self):
        import test_oracle_robot_identity_scopes as identity_fixture

        f = identity_fixture.NativeBridgeTests()
        f.setUp()
        f.binding.update(self.source)
        for reference in f.references.values():
            reference["binding"].update(self.source)
        f.observations["frontend"]["frontend"]["source_commit"] = self.source[
            "candidate_commit"
        ]
        f.paths = {
            runner.candidate_environment(f.references["api"]["binding"], env): pin
            for env, pin in f.references["api"]["binding"][
                "configuration_sha256"
            ].items()
        }
        self.assertEqual(f.run_bridge(), f.observations)
        for component in ("api", "frontend"):
            reference = f.references[component]
            for key, value in (("product_source_tree", "f" * 40), ("repository_id", 1)):
                original = reference["binding"][key]
                reference["binding"][key] = value
                with (
                    self.subTest(component=component, key=key),
                    self.assertRaises(runner.Refused),
                ):
                    f.run_bridge()
                reference["binding"][key] = original
            reference["verifier_store"] = "/foreign/old-store"
            with self.assertRaisesRegex(
                runner.Refused, "EXACT_NATIVE_BRIDGE_REFERENCE"
            ):
                f.run_bridge()
            del reference["verifier_store"]

    def test_actual_product_archives_preserve_backend_edge_and_ros_without_root_relabeling(
        self,
    ):
        import oracle_robot_recovery as recovery

        with tempfile.TemporaryDirectory(dir=Path.home()) as directory:
            root = Path(directory)
            root.chmod(0o700)
            old = root / "old"
            old.mkdir(mode=0o700)
            installed = root / "installed"
            installed.mkdir(mode=0o700)
            files = {
                "onesa-ai-robot-backend/robot_api/main.py": b"main",
                "onesa-ai-robot-backend/robot_api/inspection.py": b"inspection",
                "edge/runtime.py": b"edge",
                "ros2/package.xml": b"ros",
                "onesa-ai-robot-frontend/index.html": b"old frontend",
            }

            def archive(path, content):
                checkout = root / ("git-" + str(len(list(root.glob("git-*")))))
                checkout.mkdir(mode=0o700)
                for name, data in content.items():
                    f = checkout / name
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(data)
                subprocess.run(
                    ["git", "init", "--quiet", str(checkout)],
                    check=True,
                    capture_output=True,
                )
                subprocess.run(
                    ["git", "-C", str(checkout), "add", "."],
                    check=True,
                    capture_output=True,
                )
                tree = subprocess.check_output(
                    ["git", "-C", str(checkout), "write-tree"], text=True
                ).strip()
                path.write_bytes(
                    subprocess.check_output(
                        ["git", "-C", str(checkout), "archive", tree]
                    )
                )
                path.chmod(0o600)
                return tree

            old_tree = archive(old / "source.tar", files)
            old_binding = {
                "repository": runner.LEGACY_REPOSITORY,
                "component": "api",
                "candidate_commit": "e" * 40,
                "source_tree": old_tree,
                "source_sha256": runner.sha(old / "source.tar"),
            }
            current = {**files, "onesa-ai-robot-frontend/index.html": b"new frontend"}

            def compare(content):
                tree = archive(installed / "tool-source.tar", content)
                source = {
                    **self.source,
                    "product_source_tree": tree,
                    "source_sha256": runner.sha(installed / "tool-source.tar"),
                }
                f = installed / "installation.json"
                f.write_bytes(
                    owner.canonical(
                        {
                            "source": source["candidate_commit"],
                            "source_tree": source["source_tree"],
                        }
                    )
                )
                f.chmod(0o600)
                owner_private = owner.private
                with (
                    patch.object(runner, "component_folder", return_value=old),
                    patch.object(
                        owner,
                        "private",
                        side_effect=lambda path, **kw: owner_private(
                            path, **{**kw, "uid": os.getuid()}
                        ),
                    ),
                    patch.object(
                        runner,
                        "private",
                        # The non-root sandbox owns / and /home. Reuse the
                        # actual private-file validator at the fixture UID;
                        # the separate custody test checks runner ancestors.
                        side_effect=lambda path, **kw: (
                            owner_private(path, **{**kw, "uid": os.getuid()}),
                            Path(path),
                        )[1],
                    ),
                ):
                    return recovery.unchanged_components(old_binding, installed, source)

            result = compare(current)
            self.assertEqual(result["files"], 4)
            for name in (
                "onesa-ai-robot-backend/robot_api/inspection.py",
                "edge/runtime.py",
                "ros2/package.xml",
            ):
                with (
                    self.subTest(name=name),
                    self.assertRaisesRegex(owner.Rejected, "CHANGED_BACKEND"),
                ):
                    compare({**current, name: b"changed"})
