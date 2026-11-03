"""Disposable installer provenance tests; these fixtures confer no trust."""

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oracle_install as installer
import oracle_owner as verifier
import oracle_robot as runner


class Source:
    def __init__(self, files):
        self.files = files
        self.failed = False
        self.truncated = False
        self.mode = "100644"

    def owner(self):
        return verifier.OWNER

    def qualify(self, binding):
        verifier.require(not self.failed, "Real CI fixture failed")
        return [{"run_id": 123, "run_attempt": 1}]

    def api(self, path):
        if "/git/trees/" in path:
            return {
                "truncated": self.truncated,
                "tree": [
                    {
                        "type": "blob",
                        "mode": self.mode,
                        "path": name,
                        "sha": hashlib.sha1(data).hexdigest(),
                    }
                    for name, data in self.files.items()
                ],
            }
        data = next(
            data
            for data in self.files.values()
            if hashlib.sha1(data).hexdigest() == path.rsplit("/", 1)[1]
        )
        return {"encoding": "base64", "content": base64.b64encode(data).decode()}


class InstallTests(unittest.TestCase):
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
        self.manifest = {
            "schema_version": 1,
            "repository": "DruHustle/onesa-robot",
            "candidate_commit": "a" * 40,
            "source_tree": "",
            "files": {},
            "policy_sha256": "",
            "tool_source_sha256": "",
        }
        files = {}
        source_root = self.root / "synthetic-source"
        source_root.mkdir(mode=0o700)
        repo = Path(__file__).resolve().parents[2]
        for local, source in installer.ALLOWED.items():
            data = (repo / source).read_bytes()
            files[source] = data
            self.put(local, data)
            self.manifest["files"][local] = hashlib.sha256(data).hexdigest()
        for name in [
            "scripts/release/oracle_robot_browser.mjs",
            "scripts/release/oracle_robot_identity_browser.mjs",
            "onesa-ai-robot-frontend/package.json",
            "onesa-ai-robot-frontend/package-lock.json",
            "scripts/release/smoke.py",
            "scripts/release/cloud.py",
            "scripts/release/service_runtime.py",
            "scripts/release/evidence.py",
            "scripts/release/edge_fixture.py",
        ]:
            files[name] = (repo / name).read_bytes()
        for name in ["main.py", "inspection.py"]:
            files["onesa-ai-robot-backend/robot_api/" + name] = (
                b"# disposable parser fixture only\n"
            )
        for name, data in files.items():
            path = source_root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        subprocess.run(
            ["git", "init", "--quiet", str(source_root)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(source_root), "add", "."], check=True, capture_output=True
        )
        tree = subprocess.check_output(
            ["git", "-C", str(source_root), "write-tree"], text=True
        ).strip()
        self.manifest["source_tree"] = tree
        archive = subprocess.check_output(
            ["git", "-C", str(source_root), "archive", tree]
        )
        self.put("tool-source.tar", archive)
        self.manifest["tool_source_sha256"] = hashlib.sha256(archive).hexdigest()
        self.github = Source(files)
        target = installer.INSTALL_PARENT / ("robot-" + tree)
        self.policy = {
            "schema_version": 1,
            "components": {},
            "toolchain": {
                "source_commit": "a" * 40,
                "source_tree": tree,
                "source_sha256": self.manifest["tool_source_sha256"],
                "probe_image": "sha256:" + "1" * 64,
                "cli_image": "sha256:" + "2" * 64,
                "builder_image": "sha256:" + "3" * 64,
                "builder_sha256": self.manifest["files"][
                    "runners/oracle_frontend_build.py"
                ],
                "transport_guard_sha256": self.manifest["files"][
                    "runners/oracle_vercel_transport_guard.cjs"
                ],
                "probe_files": runner.probe_source_files(
                    {
                        name: hashlib.sha256(data).hexdigest()
                        for name, data in files.items()
                    }
                ),
            },
        }
        for component, script in [
            ("api", "oracle_robot.py"),
            ("frontend", "oracle_robot_frontend.py"),
        ]:
            self.policy["components"]["DruHustle/onesa-robot:" + component] = {
                "qualification_attempt_limit": 1,
                "runners": {
                    phase: {
                        "path": str(target / "runners" / script),
                        "sha256": self.manifest["files"]["runners/" + script],
                        "timeout_seconds": 1800,
                    }
                    for phase in verifier.PHASES
                },
                "required_checks": {
                    phase: sorted(verifier.REQUIRED_CHECKS[phase])
                    for phase in verifier.PHASES
                },
            }
        self.seal()

    def put(self, name, data):
        p = self.root / name
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        p.write_bytes(data)
        p.chmod(0o600)

    def seal(self):
        data = verifier.canonical(self.policy)
        self.put("policy.json", data)
        self.manifest["policy_sha256"] = hashlib.sha256(data).hexdigest()
        data = verifier.canonical(self.manifest)
        self.put("manifest.json", data)
        self.pin = hashlib.sha256(data).hexdigest()

    def validate(self):
        return installer.validate(self.root, self.pin, self.github, uid=os.getuid())

    @unittest.skipUnless(os.getuid() == 0, "Root custody ancestor policy")
    def test_shared_writable_parent_still_refused(self):
        shared = self.root / "shared-parent"
        shared.mkdir(mode=0o700)
        shared.chmod(0o777)
        child = shared / "custody"
        child.mkdir(mode=0o700)
        with self.assertRaisesRegex(verifier.Rejected, "ancestors"):
            verifier.private(child, directory=True, uid=0)

    def test_exact_main_bytes_and_complete_policy(self):
        m, _target, files, ci = self.validate()
        self.assertEqual(m, self.manifest)
        self.assertEqual(ci[0]["run_id"], 123)
        self.assertIn("policy.json", files)

    def test_explicit_identity_pair_preserves_pinned_runners_and_one_context(self):
        for component in ["api", "frontend"]:
            key = "DruHustle/onesa-robot:" + component
            self.policy["components"]["DruHustle/onesa-robot:identity-" + component] = (
                json.loads(json.dumps(self.policy["components"][key]))
            )
        self.seal()
        self.validate()
        key = "DruHustle/onesa-robot:identity-api"
        self.policy["components"][key]["qualification_attempt_limit"] = 2
        self.seal()
        with self.assertRaisesRegex(verifier.Rejected, "One initial context"):
            self.validate()
        self.policy["components"][key]["qualification_attempt_limit"] = 1
        del self.policy["components"]["DruHustle/onesa-robot:identity-frontend"]
        self.seal()
        with self.assertRaisesRegex(verifier.Rejected, "explicit identity pair"):
            self.validate()

    def test_tamper_wrong_pin_and_unreviewed_blob(self):
        self.put("oracle_owner.py", b"candidate replacement")
        with self.assertRaises(verifier.Rejected):
            self.validate()
        self.manifest["files"]["oracle_owner.py"] = hashlib.sha256(
            b"candidate replacement"
        ).hexdigest()
        self.seal()
        with self.assertRaises(verifier.Rejected):
            self.validate()

    def test_ci_tree_and_source_type_fail_closed(self):
        for field, value in [("failed", True), ("truncated", True), ("mode", "120000")]:
            original = getattr(self.github, field)
            setattr(self.github, field, value)
            with self.subTest(field=field), self.assertRaises(verifier.Rejected):
                self.validate()
            setattr(self.github, field, original)

    def test_incomplete_policy_and_extra_component_refused(self):
        self.policy["components"]["DruHustle/onesa-robot:api"]["required_checks"][
            "staging"
        ].remove("persistence")
        self.seal()
        with self.assertRaises(verifier.Rejected):
            self.validate()

    def test_alias_and_mode_refused(self):
        p = self.root / "oracle_owner.py"
        data = p.read_bytes()
        p.unlink()
        other = self.root / "other"
        other.write_bytes(data)
        other.chmod(0o600)
        p.symlink_to(other)
        with self.assertRaises(verifier.Rejected):
            self.validate()

    def test_dry_run_performs_no_install_or_authority_write(self):
        with patch.object(installer, "INSTALL_PARENT", self.root / "not-created"):
            # Regenerate paths in the checked policy for this disposable location.
            for component, script in [
                ("api", "oracle_robot.py"),
                ("frontend", "oracle_robot_frontend.py"),
            ]:
                for r in self.policy["components"][
                    "DruHustle/onesa-robot:" + component
                ]["runners"].values():
                    r["path"] = str(
                        installer.INSTALL_PARENT
                        / ("robot-" + self.manifest["source_tree"])
                        / "runners"
                        / script
                    )
            self.seal()
            result = installer.install(
                self.root, self.pin, self.github, dry_run=True, uid=os.getuid()
            )
            self.assertEqual(result["state"], "INSTALLATION_READY")
            self.assertFalse(installer.INSTALL_PARENT.exists())


class IsolatedInstallerEntryTests(unittest.TestCase):
    def test_isolated_python_resolves_the_bundled_verifier(self):
        result = subprocess.run(
            [sys.executable, "-I", str(Path(installer.__file__)), "--help"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"--manifest-pin", result.stdout)
