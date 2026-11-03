"""Pinned-image staging acceptance and safe CLI failures; synthetic inputs only."""

import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import oracle_robot as r
import oracle_robot_probe as probe
from smoke import CHECKS


class LegacyImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        binding = {"repository": "DruHustle/onesa-robot", "component": "api"}
        origin = "https://onesa-ai-robot-git-staging-synthetic.vercel.app"
        self.request = {
            "binding": binding,
            "environment": "staging",
            "physical_hardware_authorized": False,
            "read_only": False,
            "api_origin": "https://robot-api-staging.onesa.eu",
            "origin": origin,
            "provider": {
                "environment": "staging",
                "origin": origin,
                "provider_token_included": False,
                "binding": binding,
                "provider": {"source": "a" * 40},
                "bypass": "synthetic-preview-only",
            },
            "expected_frontend_source": "a" * 40,
            "probe_image": "sha256:" + "b" * 64,
            "cpu_limit": "1",
            "output": str(self.output),
            "credentials": {
                "operator": "synthetic-operator",
                "password": "invented-password-only",
                "edge_token": "invented-edge-only",
            },
        }
        self.result = {
            "status": "PASS",
            "scope": "software-simulation-only",
            "physical_hardware_approved": False,
            "checks": {name: "PASS" for name in CHECKS},
        }

    def call(self, *, request=None, result=None, returncode=0):
        with (
            patch.object(
                r,
                "runtime_values",
                return_value={"ROBOT_EDGE_DEVICE_ID": "synthetic-edge"},
            ),
            patch.object(
                probe.subprocess,
                "run",
                return_value=subprocess.CompletedProcess(
                    [], returncode, json.dumps(result or self.result).encode(), b""
                ),
            ) as child,
        ):
            value = probe.staging_legacy(request or self.request)
        return value, child.call_args

    def test_complete_existing_contract_runs_in_exact_image_with_private_stdin(self):
        self.assertEqual(tuple(CHECKS), probe.LEGACY_CHECKS)
        value, call = self.call()
        self.assertEqual(value, self.result)
        args = call.args[0]
        self.assertEqual(
            args[-3:], [self.request["probe_image"], "-c", probe.LEGACY_PROGRAM]
        )
        for flag in [
            "--read-only",
            "--cap-drop",
            "--security-opt",
            "--memory",
            "--cpus",
        ]:
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--pull") + 1], "never")
        self.assertNotIn("-v", args)
        self.assertNotIn("-e", args)
        private = json.loads(call.kwargs["input"])
        self.assertEqual(
            private["environment"]["ROBOT_TEST_SCOPE"], "approved-synthetic-staging"
        )
        for key in ["password", "edge_token"]:
            secret = self.request["credentials"][key]
            self.assertNotIn(secret, repr(args))
            self.assertNotIn(secret, repr(call.kwargs["env"]))
            self.assertIn(secret.encode(), call.kwargs["input"])
        receipt = self.output / "legacy-smoke.json"
        self.assertEqual(json.loads(receipt.read_bytes()), self.result)
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)

    def test_failed_skipped_and_missing_mandatory_check_remain_unqualified(self):
        for status in ["FAIL", "SKIPPED", None]:
            with self.subTest(status=status):
                value = copy.deepcopy(self.result)
                if status is None:
                    del value["checks"][CHECKS[-1]]
                else:
                    value["checks"][CHECKS[-1]] = status
                with self.assertRaisesRegex(
                    r.Refused, "EXISTING_SIMULATOR_ACCEPTANCE_FAILED"
                ):
                    self.call(result=value)
                receipt = self.output / "legacy-smoke.json"
                self.assertEqual(json.loads(receipt.read_bytes()), value)
                receipt.unlink()

    def test_production_refused_before_any_mutating_child(self):
        request = copy.deepcopy(self.request)
        request.update(
            environment="production",
            read_only=True,
            api_origin="https://robot-api.onesa.eu",
            origin="https://robot.onesa.eu",
        )
        request["provider"].update(environment="production", origin=request["origin"])
        with patch.object(probe.subprocess, "run") as child:
            with self.assertRaisesRegex(r.Refused, "NO_LEGACY_MUTATIONS_IN_PRODUCTION"):
                probe.staging_legacy(request)
            child.assert_not_called()

    def test_wrong_target_or_unpinned_image_refused_before_child(self):
        for key, wrong in [
            ("origin", "https://attacker.invalid"),
            ("probe_image", "latest"),
        ]:
            request = {**self.request, key: wrong}
            with self.subTest(key=key), patch.object(probe.subprocess, "run") as child:
                with self.assertRaises(r.Refused):
                    probe.staging_legacy(request)
                child.assert_not_called()

    def test_non_synthetic_edge_refused_before_child(self):
        with (
            patch.object(
                r,
                "runtime_values",
                return_value={"ROBOT_EDGE_DEVICE_ID": "real-device"},
            ),
            patch.object(probe.subprocess, "run") as child,
        ):
            with self.assertRaisesRegex(
                r.Refused, "EXISTING_STAGING_EDGE_CREDENTIAL_REQUIRED"
            ):
                probe.staging_legacy(self.request)
            child.assert_not_called()

    def test_image_import_failure_never_becomes_acceptance(self):
        with self.assertRaisesRegex(r.Refused, "LEGACY_IMAGE_EXECUTION_FAILED"):
            self.call(returncode=1)
        self.assertFalse((self.output / "legacy-smoke.json").exists())

    def test_complete_transitive_source_pin_is_mandatory(self):
        root = Path(__file__).resolve().parents[2]
        names = [
            str(path.relative_to(root))
            for path in (root / "scripts/release").glob("*.py")
        ]
        names += [
            "scripts/release/oracle_robot_browser.mjs",
            "scripts/release/oracle_robot_identity_browser.mjs",
            "onesa-ai-robot-frontend/package.json",
            "onesa-ai-robot-frontend/package-lock.json",
        ]
        files = {name: "synthetic-pin" for name in names}
        self.assertIn("scripts/release/service_runtime.py", r.probe_source_files(files))
        del files["scripts/release/service_runtime.py"]
        with self.assertRaisesRegex(
            r.Refused, "COMPLETE_COMMISSIONED_PROBE_SOURCE_REQUIRED"
        ):
            r.probe_source_files(files)

    def test_actual_image_program_accepts_only_private_staging_input(self):
        _, call = self.call()
        run = Mock(return_value=self.result)
        output = io.StringIO()
        with (
            patch.dict(sys.modules, {"smoke": SimpleNamespace(run=run)}),
            patch.object(sys, "path", list(sys.path)),
            patch.object(
                sys, "stdin", SimpleNamespace(buffer=io.BytesIO(call.kwargs["input"]))
            ),
            patch.object(sys, "stdout", output),
            patch.dict(os.environ, {}, clear=True),
        ):
            exec(compile(probe.LEGACY_PROGRAM, "<pinned-legacy-program>", "exec"), {})  # noqa: S102
            self.assertEqual(
                os.environ["ROBOT_TEST_SCOPE"], "approved-synthetic-staging"
            )
            run.assert_called_once_with(
                self.request["origin"],
                "Staging",
                oracle_staging_api="https://robot-api-staging.onesa.eu",
                identity_login=None,
            )
        self.assertEqual(json.loads(output.getvalue()), self.result)

    def test_actual_image_program_refuses_production_and_real_device_before_suite(self):
        for field, wrong in [
            ("origin", "https://robot.onesa.eu"),
            ("ROBOT_EDGE_DEVICE_ID", "real-device"),
        ]:
            # Obtain the exact host-generated wire input, never a second live run.
            if (self.output / "legacy-smoke.json").exists():
                (self.output / "legacy-smoke.json").unlink()
            _, call = self.call()
            value = json.loads(call.kwargs["input"])
            if field == "origin":
                value[field] = wrong
            else:
                value["environment"][field] = wrong
            run = Mock()
            with (
                self.subTest(field=field),
                patch.dict(sys.modules, {"smoke": SimpleNamespace(run=run)}),
                patch.object(sys, "path", list(sys.path)),
                patch.object(
                    sys, "stdin", SimpleNamespace(buffer=io.BytesIO(r.canonical(value)))
                ),
                patch.dict(os.environ, {}, clear=True),
                self.assertRaisesRegex(ValueError, "LEGACY_IMAGE_INPUT_REFUSED"),
            ):
                exec(  # noqa: S102 -- execute the fixed reviewed image program, with a fake suite
                    compile(probe.LEGACY_PROGRAM, "<pinned-legacy-program>", "exec"), {}
                )
            run.assert_not_called()


class CliFailureTests(unittest.TestCase):
    def test_missing_module_writes_only_safe_failure_metadata(self):
        request = {
            "binding": {"candidate_commit": "a" * 40, "component": "api"},
            "context": {"context_id": "11111111-1111-1111-1111-111111111111"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with (
                patch.object(sys, "argv", ["oracle_robot.py", "staging"]),
                patch.object(
                    sys,
                    "stdin",
                    SimpleNamespace(buffer=io.BytesIO(r.canonical(request))),
                ),
                patch.object(os, "geteuid", return_value=0),
                patch.dict(os.environ, {}, clear=True),
                patch.object(r, "component_folder", return_value=folder),
                patch.object(r, "private", side_effect=lambda path, **kw: Path(path)),
                patch.object(
                    r, "runner", side_effect=ModuleNotFoundError("invented-secret")
                ),
                self.assertRaisesRegex(r.Refused, "ModuleNotFoundError"),
            ):
                r.main()
            records = list((folder / "failures").glob("*.json"))
            self.assertEqual(len(records), 1)
            value = json.loads(records[0].read_bytes())
            self.assertEqual(value["failure_code"], "ModuleNotFoundError")
            self.assertFalse(value["provider_retry_allowed"])
            self.assertNotIn("invented-secret", records[0].read_text())

    def test_cli_and_sibling_import_share_refusal_identity(self):
        program = """
import runpy, sys
def trace(frame, event, arg):
    if event == 'call' and frame.f_code.co_name == 'main':
        import oracle_robot
        if oracle_robot.Refused is not frame.f_globals['Refused']:
            raise SystemExit('SPLIT_REFUSAL_IDENTITY')
        raise SystemExit('SHARED_REFUSAL_IDENTITY')
    return trace
sys.settrace(trace)
runpy.run_path(sys.argv[1], run_name='__main__')
"""
        result = subprocess.run(
            [sys.executable, "-c", program, str(Path(r.__file__).resolve())],
            capture_output=True,
            check=False,
            timeout=15,
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.decode().strip(), "SHARED_REFUSAL_IDENTITY")


if __name__ == "__main__":
    unittest.main()
