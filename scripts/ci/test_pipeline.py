"""Pipeline regressions using real analyzers/tools and synthetic inputs, never live services."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import analyze
import install_tools
import paths
import security
import summary

ROOT = Path(__file__).resolve().parents[2]


class PipelineTests(unittest.TestCase):
    def test_historical_non_secret_fixture_is_bound_to_snapshot_path_and_literal(self):
        spec = importlib.util.spec_from_file_location(
            "publication_hygiene", ROOT / "scripts/repository_hygiene.py"
        )
        hygiene = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hygiene)
        fixture = b'token = "synthetic-private-edge-marker"'
        name = "scripts/release/test_staging_configuration.py"
        self.assertIn("credential-assignment", hygiene.secrets(fixture, name))
        with patch.object(hygiene.hashlib, "sha256") as digest:
            digest.return_value.hexdigest.return_value = (
                "0784c4944a08aeca974d35f2e663f9f189deb42307a66e67b4d32d2b51f75244"
            )
            self.assertNotIn("credential-assignment", hygiene.secrets(fixture, name))
            self.assertIn(
                "credential-assignment", hygiene.secrets(fixture, "robot_api/main.py")
            )
            changed = fixture.replace(b"edge-marker", b"edge-marker-extra")
            self.assertIn("credential-assignment", hygiene.secrets(changed, name))
            # Other real credential rules still fire even for the reviewed snapshot.
            self.assertIn(
                "github-token",
                hygiene.secrets(fixture + b"\n" + b"ghp_" + b"x" * 40, name),
            )
            digest.return_value.hexdigest.return_value = "0" * 64
            self.assertIn("credential-assignment", hygiene.secrets(fixture, name))

    def test_release_sources_always_run_every_critical_check(self):
        for product in ("onesa-company-portal", "onesa-ai-platform", "onesa-robot"):
            selection = paths.classify(product, ["docs/readme.md"], full=True)
            self.assertTrue(all(selection[key] for key in paths.FIELDS))
            self.assertEqual(selection["applications"], ["frontend", "backend"])

    def test_native_clients_build_on_release_pushes_without_cancelling_release_runs(self):
        workflow = (ROOT / ".github/workflows/mobile-clients.yml").read_text()
        self.assertRegex(
            workflow,
            r"(?ms)^  push:\n    branches:\n      - dev\n      - staging\n      - main\n",
        )
        self.assertIn(
            "group: mobile-clients-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}",
            workflow,
        )
        self.assertIn(
            "cancel-in-progress: ${{ github.event_name == 'pull_request' }}",
            workflow,
        )
        self.assertIn("'onesa-ai-robot-frontend/mobile/**'", workflow)

    def test_unknown_and_shared_security_contract_paths_fail_safe(self):
        for product in ("onesa-company-portal", "onesa-ai-platform", "onesa-robot"):
            for name in (
                "scripts/release/evidence.py",
                ".github/workflows/ci.yml",
                "infra/migration.sql",
                "contracts/openapi.json",
                "docs/api/openapi.json",
                ".gitignore",
                "unknown/new-file",
            ):
                with self.subTest(product=product, path=name):
                    self.assertTrue(
                        all(
                            paths.classify(product, [name])[key] for key in paths.FIELDS
                        )
                    )
            self.assertTrue(
                all(paths.classify(product, None)[key] for key in paths.FIELDS)
            )

    def test_docs_prs_reduce_application_work_but_not_release_pushes(self):
        selection = paths.classify("onesa-ai-platform", ["docs/public/roadmap.md"])
        self.assertFalse(selection["backend"])
        self.assertTrue(selection["documentation"])
        self.assertTrue(selection["browser"])
        self.assertFalse(selection["containers"])

    def test_auth_client_and_backend_changes_keep_affected_frontend_checks(self):
        for name in (
            "onesa-ai-platform-backend/app/auth.py",
            "onesa-ai-platform-frontend/packages/api-client/schema.ts",
            "onesa-ai-platform-frontend/apps/chat/app/api/backend/route.ts",
        ):
            self.assertTrue(
                all(
                    paths.classify("onesa-ai-platform", [name])[key]
                    for key in paths.FIELDS
                )
            )

    def test_unavailable_diff_base_runs_full_ci(self):
        self.assertIsNone(paths.changed_files("not-a-sha"))
        with patch.object(
            paths.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 128, "", ""),
        ):
            self.assertIsNone(paths.changed_files("a" * 40))

    def test_summary_rejects_failure_and_unexpected_skips(self):
        selection = {key: "true" for key in paths.FIELDS}
        selection["verify"] = "true"
        results = {
            key: {"result": "success"}
            for key in ("changes", "pipeline-security", "verify", "image-security")
        }
        self.assertEqual(summary.check(selection, results, "onesa-company-portal"), [])
        for state in ("failure", "cancelled", "skipped"):
            results["verify"]["result"] = state
            self.assertTrue(summary.check(selection, results, "onesa-company-portal"))
        results["verify"]["result"] = "skipped"
        selection["verify"] = "false"
        self.assertEqual(summary.check(selection, results, "onesa-company-portal"), [])
        results["pipeline-security"]["result"] = "skipped"
        self.assertTrue(summary.check(selection, results, "onesa-company-portal"))

    def test_summary_cannot_accept_missing_selection_or_hide_optional_failure(self):
        results = {
            key: {"result": "success"}
            for key in ("changes", "pipeline-security", "verify", "image-security")
        }
        self.assertTrue(summary.check({}, results, "onesa-company-portal"))
        results["verify"]["result"] = "failure"
        self.assertTrue(
            summary.check(
                {"verify": "false", "containers": "false"},
                results,
                "onesa-company-portal",
            )
        )

    def test_real_failing_command_remains_failed_after_real_annotations(self):
        import sys

        env = dict(os.environ)
        env.pop("GITHUB_ACTIONS", None)
        command = [
            sys.executable,
            str(ROOT / "scripts/ci/analyze.py"),
            "tsc",
            "--",
            sys.executable,
            "-c",
            "import sys; print('src/example.ts(1,1): error TS2322: synthetic wrong type'); sys.exit(2)",
        ]
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("synthetic wrong type", result.stdout + result.stderr)

    def test_type_diagnostics_and_ruff_eslint_json_are_annotatable(self):
        self.assertEqual(
            len(analyze.parse("tsc", "src/index.ts(2,3): error TS2322: Type mismatch")),
            1,
        )
        self.assertEqual(
            len(analyze.parse("mypy", "app/main.py:4: error: wrong type")), 1
        )
        self.assertEqual(
            len(
                analyze.parse(
                    "ruff",
                    json.dumps(
                        [
                            {
                                "filename": "app.py",
                                "code": "F401",
                                "message": "unused import",
                                "location": {"row": 1, "column": 1},
                            }
                        ]
                    ),
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                analyze.parse(
                    "eslint",
                    json.dumps(
                        [
                            {
                                "filePath": "app.ts",
                                "messages": [
                                    {
                                        "message": "bad value",
                                        "severity": 2,
                                        "line": 1,
                                        "column": 1,
                                    }
                                ],
                            }
                        ]
                    ),
                )
            ),
            1,
        )

    def test_failed_analyzer_cannot_be_hidden_by_successful_annotations(self):
        with (
            patch.object(
                analyze.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], 2, "src/index.ts(2,3): error TS2322: mismatch", ""
                ),
            ),
            patch.object(analyze, "annotate") as annotate,
            patch("sys.argv", ["analyze.py", "tsc", "--", "tsc"]),
        ):
            self.assertEqual(analyze.main(), 2)
            annotate.assert_called_once()

    def test_reviewdog_emits_annotations_without_write_api_or_real_token(self):
        tool = shutil.which("reviewdog")
        self.assertIsNotNone(
            tool, "Pinned reviewdog is required for pipeline validation"
        )
        with tempfile.TemporaryDirectory() as folder:
            event = Path(folder) / "event.json"
            event.write_text("{}")
            env = dict(
                os.environ,
                GITHUB_ACTIONS="true",
                GITHUB_EVENT_NAME="push",
                GITHUB_REPOSITORY="DruHustle/" + ROOT.name,
                GITHUB_EVENT_PATH=str(event),
                REVIEWDOG_GITHUB_API_TOKEN="test-read-only",
            )
            report = {
                "source": {"name": "negative-test"},
                "diagnostics": [
                    {
                        "message": "synthetic lint failure",
                        "severity": "ERROR",
                        "location": {
                            "path": "README.md",
                            "range": {"start": {"line": 1, "column": 1}},
                        },
                    }
                ],
            }
            result = subprocess.run(
                [
                    tool,
                    "-f=rdjson",
                    "-reporter=github-annotations",
                    "-filter-mode=nofilter",
                    "-fail-level=none",
                    "-diff=git diff HEAD",
                ],
                input=json.dumps(report),
                capture_output=True,
                text=True,
                cwd=ROOT,
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("::error ", result.stdout)
            self.assertIn("file=README.md", result.stdout)
            self.assertIn("synthetic lint failure", result.stdout)

    def test_actionlint_rejects_broken_workflow(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "broken.yml"
            file.write_text(
                "name: broken\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n    - run: echo ${{ unrecognized.value }}\n"
            )
            result = subprocess.run(
                ["actionlint", str(file)], capture_output=True, text=True, check=False
            )
            self.assertNotEqual(result.returncode, 0)

    def test_security_findings_and_tool_failures_cannot_become_green(self):
        low = [{"determinations": {"severity": "Low"}}]
        high = [{"determinations": {"severity": "High"}}]
        self.assertEqual(security.workflow_security_status(12, low)[0], 0)
        for status, findings in (
            (13, []),
            (11, []),
            (12, []),
            (2, []),
            (0, high),
            (14, high),
        ):
            with self.subTest(status=status, findings=findings):
                self.assertEqual(
                    security.workflow_security_status(status, findings)[0], 1
                )

    def test_zizmor_rejects_template_injection(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "unsafe.yml"
            file.write_text(
                'name: unsafe\non: pull_request\npermissions:\n  contents: read\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n    - run: echo "${{ github.event.pull_request.title }}"\n'
            )
            result = subprocess.run(
                ["zizmor", "--offline", "--format", "json", "--no-progress", str(file)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertTrue(
                any(
                    item["ident"] == "template-injection"
                    for item in json.loads(result.stdout)
                )
            )
            self.assertNotEqual(result.returncode, 0)

    def test_gitleaks_detects_synthetic_token_without_printing_it(self):
        with tempfile.TemporaryDirectory() as folder:
            fake = "ghp_" + "Ab3Cd4Ef5Gh6Ij7Kl8Mn9Op0Qr1St2Uv3Wx4"
            (Path(folder) / "fake.txt").write_text("token=" + fake)
            result = subprocess.run(
                [
                    "gitleaks",
                    "dir",
                    folder,
                    "--redact=100",
                    "--no-banner",
                    "--log-level=error",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(fake, result.stdout + result.stderr)

    def test_checksum_mismatch_is_rejected_before_binary_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "scripts/ci").mkdir(parents=True)
            (root / "scripts/ci/tools.lock.json").write_text(
                json.dumps(
                    {
                        "reviewdog": {
                            "assets": {
                                "darwin-arm64": {
                                    "url": "https://github.com/reviewdog/reviewdog/releases/download/test/tool.tar.gz",
                                    "sha256": "a" * 64,
                                    "binary_sha256": "b" * 64,
                                }
                            }
                        }
                    }
                )
            )
            response = io.BytesIO(b"tampered archive")
            with (
                patch.object(install_tools, "ROOT", root),
                patch.object(install_tools.platform, "system", return_value="Darwin"),
                patch.object(install_tools.platform, "machine", return_value="arm64"),
                patch.object(
                    install_tools.urllib.request, "urlopen", return_value=response
                ),
                self.assertRaisesRegex(ValueError, "checksum"),
            ):
                install_tools.install(root / "bin", ["reviewdog"])
            self.assertFalse((root / "bin/reviewdog").exists())

    def test_secret_export_rejects_symlinks_to_ignored_private_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "private.env").write_text("synthetic private data")
            (root / "link").symlink_to(root / "private.env")
            with (
                patch.object(security, "ROOT", root),
                patch.object(
                    security.subprocess, "check_output", return_value=b"link\x00"
                ),
                self.assertRaisesRegex(ValueError, "symlinks"),
            ):
                security.exported_source(root / "export")


if __name__ == "__main__":
    unittest.main()
