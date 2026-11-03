"""Synthetic provider readback and duplicate-prevention tests; no external requests."""

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import oracle_robot as r
import oracle_robot_frontend as f


class ProviderConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.project = {
            "id": "prj_fixture",
            "rootDirectory": f.APP_ROOT,
            "framework": None,
            "nodeVersion": "24.x",
            "serverlessFunctionRegion": "iad1",
            "ssoProtection": {"deploymentType": "all"},
            "protectionBypass": {},
            "passwordProtection": None,
            "trustedIps": None,
        }
        fields = {
            k: self.project[k]
            for k in [
                "ssoProtection",
                "protectionBypass",
                "passwordProtection",
                "trustedIps",
            ]
        }
        self.plan = {
            "project_id": self.project["id"],
            "serverless_function_region": "iad1",
            "vercel_protection_sha256": hashlib.sha256(
                json.dumps(fields, sort_keys=True).encode()
            ).hexdigest(),
            "vercel_api_url_variable_ids": {
                "staging": "fixture_stage",
                "production": "fixture_production",
            },
            "api_origins": {
                "staging": "https://robot-api-staging.onesa.eu",
                "production": "https://robot-api.onesa.eu",
            },
        }
        self.variables = {
            self.plan["vercel_api_url_variable_ids"][env]: {
                "id": self.plan["vercel_api_url_variable_ids"][env],
                "key": "ROBOT_API_URL",
                "value": self.plan["api_origins"][env],
                "target": ["preview" if env == "staging" else "production"],
                "gitBranch": None,
            }
            for env in ["staging", "production"]
        }
        self.calls = []

    def provider(self, path, *, data=None):
        self.calls.append((path, data))
        self.assertIsNone(data)
        if path.startswith("/v9/projects/"):
            return self.project
        self.assertTrue(path.startswith("/v1/projects/prj_fixture/env/"))
        return self.variables[path.rsplit("/", 1)[1]]

    def call(self):
        with (
            patch.object(r, "read", return_value={"project_id": "prj_fixture"}),
            patch.object(f, "provider", side_effect=self.provider),
        ):
            return f.protection(self.plan)

    def test_foreign_api_origin_fails_before_provider_or_credential_use(self):
        with patch.object(f, "provider") as provider:
            with self.assertRaisesRegex(r.Refused, "CANONICAL_ROBOT_API_ORIGINS"):
                f.protection({"api_origins": {"staging": "https://foreign.invalid"}})
            provider.assert_not_called()

    def test_only_two_explicit_nonsecret_routing_variables_are_read(self):
        self.assertEqual(self.call(), self.project)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(
            all("/env" not in path or "/env/" in path for path, _ in self.calls)
        )

    def test_wrong_origin_target_branch_or_project_fails_before_deploy(self):
        row = self.variables["fixture_stage"]
        for key, value in [
            ("value", "https://foreign.invalid"),
            ("key", "UNRELATED_SECRET"),
            ("target", ["production"]),
            ("gitBranch", "foreign"),
            ("id", "foreign_id"),
        ]:
            old = row[key]
            row[key] = value
            with self.subTest(key=key), self.assertRaises(r.Refused):
                self.call()
            row[key] = old
        self.project["rootDirectory"] = "foreign"
        with self.assertRaises(r.Refused):
            self.call()


class ProviderIntentTests(unittest.TestCase):
    def test_ambiguous_cli_acknowledgement_never_allows_duplicate_create(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "operations").mkdir()
            binding = {
                "repository": "DruHustle/onesa-robot",
                "component": "frontend",
                "candidate_commit": "a" * 40,
            }
            plan = {
                "project_id": "prj_fixture",
                "serverless_function_region": "iad1",
                "builds": {"production": {"archive_sha256": "b" * 64}},
                "api_origins": {"production": "https://robot-api.onesa.eu"},
                "cli_image": "sha256:" + "c" * 64,
            }
            operation = str(uuid4())
            calls = []

            def provider(*args, **kwargs):
                calls.append(args)
                raise subprocess.TimeoutExpired("synthetic-cli", 1)

            with (
                patch.object(r, "private", side_effect=lambda p, **kw: Path(p)),
                patch.object(f, "unpack"),
                patch.object(f, "protection", return_value={"id": "prj_fixture"}),
                patch.object(f, "deployments_for", return_value=[]),
                patch.object(
                    r,
                    "trusted_toolchain",
                    return_value={
                        "transport_guard_sha256": r.sha(
                            Path(f.__file__).with_name(
                                "oracle_vercel_transport_guard.cjs"
                            )
                        )
                    },
                ),
                patch.object(f.subprocess, "run", side_effect=provider),
                patch.object(f.time, "monotonic", side_effect=[0, 121]),
            ):
                with self.assertRaisesRegex(r.Refused, "UNCERTAIN"):
                    f.create(binding, "production", operation, folder, plan)
                with self.assertRaises(FileExistsError):
                    f.create(binding, "production", operation, folder, plan)
            self.assertEqual(len(calls), 1)
            self.assertTrue(
                (folder / "operations" / operation / "intent.json").exists()
            )
            self.assertTrue((folder / "operations" / operation / "cli.stderr").exists())

    def test_interrupted_acknowledgement_is_reconciled_without_a_create_request(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            operation = str(uuid4())
            context_id = str(uuid4())
            op = folder / "operations" / operation
            op.mkdir(parents=True)
            binding = {
                "repository": "DruHustle/onesa-robot",
                "component": "frontend",
                "candidate_commit": "a" * 40,
            }
            r.write(
                op / "intent.json",
                {
                    "binding": binding,
                    "operation_id": operation,
                    "environment": "production",
                },
            )
            plan = {"builds": {"production": {"archive_sha256": "b" * 64}}}
            with (
                patch.object(f, "validate", return_value=(folder, plan)),
                patch.object(
                    r,
                    "verify_invocation",
                    return_value=[{"kind": "context", "id": context_id}],
                ),
                patch.object(f, "accepted_api"),
                patch.object(r, "inventory", return_value={}),
                patch.object(f, "deployments_for", return_value=[{"uid": "dpl_owned"}]),
                patch.object(
                    f,
                    "check_deployment",
                    return_value={"id": "dpl_owned", "readyState": "READY"},
                ),
                patch.object(
                    r, "read", side_effect=lambda p: json.loads(Path(p).read_bytes())
                ),
                patch.object(f, "verify_static"),
                patch.object(r, "probe", return_value={"status": "PASS"}),
                patch.object(f, "create") as create,
            ):
                result = f.runner(
                    "production",
                    binding,
                    {"operation_id": operation, "read_only": True},
                )
            create.assert_not_called()
            self.assertEqual(result["deployment_id"], "dpl_owned")
            self.assertTrue(
                json.loads((op / "provider-receipt.json").read_bytes())[
                    "reconciled_read_only"
                ]
            )
