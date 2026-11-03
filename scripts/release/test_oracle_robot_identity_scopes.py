"""Separate bridge and identity authority, configurations and accepted dependencies."""

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import oracle_robot as r
import oracle_robot_frontend as frontend
import oracle_robot_identity as identity
from test_oracle_robot_identity import fixture


class IdentityScopeTests(unittest.TestCase):
    def setUp(self):
        self.binding, self.tool, self.plan, _, _ = fixture()

    def test_legacy_scope_cannot_enable_identity_or_consume_identity_dependency(self):
        for component in ["api", "frontend"]:
            with (
                self.subTest(component=component),
                self.assertRaisesRegex(r.Refused, "DISTINCT_COMPONENT_SCOPES"),
            ):
                identity.profile(
                    self.plan, {**self.binding, "component": component}, self.tool
                )
        with self.assertRaisesRegex(r.Refused, "EXPLICIT_IDENTITY_PROFILE"):
            identity.profile({}, self.binding, self.tool)
        changed = copy.deepcopy(self.plan)
        changed["bindings"]["identity-api"]["component"] = "api"
        with self.assertRaisesRegex(r.Refused, "PROFILE_MISMATCH"):
            r.plan_binding(changed, "api")

    def test_both_identity_components_are_exact_source_and_not_legacy_repair(self):
        for field in ["candidate_commit", "source_tree", "source_sha256"]:
            changed = copy.deepcopy(self.plan)
            changed["bindings"]["identity-frontend"][field] = "0" * len(
                self.binding[field]
            )
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(r.Refused, "PAIR_EXACT_SOURCE"),
            ):
                identity.profile(changed, self.binding, self.tool)
        with self.assertRaisesRegex(r.Refused, "DISTINCT_COMPONENT_SCOPES"):
            identity.profile(
                {**self.plan, "independent_api": {}}, self.binding, self.tool
            )
        changed = copy.deepcopy(self.plan)
        changed["identity"]["environments"]["production"]["baseline_frontend"][
            "source"
        ] = "0" * 40
        with self.assertRaisesRegex(r.Refused, "BASELINE_FRONTEND"):
            identity.profile(changed, self.binding, self.tool)

    def test_config_and_artifact_paths_do_not_reuse_native_authority(self):
        native = {**self.binding, "component": "api"}
        identity_frontend = r.plan_binding(self.plan, "frontend")
        for environment in r.ENV:
            old = r.candidate_environment(native, environment)
            new = r.candidate_environment(self.binding, environment)
            self.assertNotEqual(old, new)
            self.assertEqual(new.parent, old.parent / "identity-api")
            self.assertEqual(
                new, r.candidate_environment(identity_frontend, environment)
            )
            self.assertNotEqual(
                r.frontend_configuration(
                    {**native, "component": "frontend"}, environment
                ),
                r.frontend_configuration(identity_frontend, environment),
            )
        with patch.object(r, "private", side_effect=lambda path, **kwargs: path):
            for component in r.COMPONENTS:
                binding = {**self.binding, "component": component}
                self.assertEqual(
                    r.component_folder(binding),
                    r.ROOT / component / "artifacts" / binding["candidate_commit"],
                )
                self.assertEqual(binding["component"], component)

    def test_identity_frontend_reads_only_accepted_identity_api_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = sqlite3.connect(root / "records.sqlite")
            db.executescript(
                "CREATE TABLE deployments(id TEXT,state TEXT);"
                "CREATE TABLE records(id TEXT,scope TEXT,sequence INTEGER);"
            )
            for scope, operation, sequence in [
                ("identity-api", "identity-operation", 1),
                ("api", "native-operation", 2),
            ]:
                db.execute(
                    "INSERT INTO deployments VALUES(?, 'accepted')", (operation,)
                )
                db.execute(
                    "INSERT INTO records VALUES(?,?,?)",
                    (operation, "DruHustle/onesa-robot:" + scope, sequence),
                )
            db.commit()
            db.close()
            runtime = {
                "image": "synthetic-image",
                "source": "a" * 40,
                "container_id": "live",
            }
            plan = {**self.plan, "verifier_store": str(root), "retained_api": runtime}
            with (
                patch.object(r, "private", side_effect=lambda path, **kwargs: path),
                patch.object(r, "verify_invocation") as verify,
                patch.object(r, "local_identity", return_value=runtime),
            ):
                self.assertEqual(frontend.accepted_api(plan), "identity-operation")
                self.assertEqual(verify.call_args.args[0], self.binding)
                self.assertTrue(verify.call_args.kwargs["dependency"])


class NativeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.binding, _, self.plan, _, _ = fixture()
        self.references = {}
        for number, component in enumerate(["api", "frontend"], 1):
            self.references[component] = {
                "binding": {
                    **self.binding,
                    "component": component,
                    "configuration_sha256": {
                        "staging": "2" * 64,
                        "production": "3" * 64,
                    },
                },
                "operation_id": f"00000000-0000-4000-8000-{number:012d}",
            }
        self.plan["identity"]["native_bridge"] = self.references
        self.runtime = {"container_id": "native-api", "configuration_sha256": "4" * 64}
        self.observations = {
            "api": {"backend_runtime": self.runtime},
            "frontend": {
                "deployment_id": self.plan["identity"]["environments"]["production"][
                    "baseline_frontend"
                ]["deployment_id"],
                "frontend": {"source_commit": self.binding["candidate_commit"]},
                "backend_runtime": self.runtime,
            },
        }
        self.paths = {
            r.candidate_environment(self.references["api"]["binding"], env): pin
            for env, pin in self.references["api"]["binding"][
                "configuration_sha256"
            ].items()
        }

    def verify(self, binding, context, phase, plan, *, dependency):
        self.assertTrue(dependency)
        self.assertEqual(phase, "production")
        reference = self.references[binding["component"]]
        self.assertEqual(binding, reference["binding"])
        self.assertEqual(
            context, {"operation_id": reference["operation_id"], "read_only": True}
        )
        self.assertIs(plan, self.plan)
        return [
            {
                "kind": "production-verification",
                "binding": binding,
                "parent": reference["operation_id"],
                "details": {"observations": self.observations[binding["component"]]},
            }
        ]

    def run_bridge(self, *, verify=None, enabled="false", changed_digest=False):
        with (
            patch.object(r, "verify_invocation", side_effect=verify or self.verify),
            patch.object(r, "private", side_effect=lambda path: path),
            patch.object(
                r,
                "sha",
                side_effect=lambda path: (
                    "0" * 64 if changed_digest else self.paths[path]
                ),
            ),
            patch.object(
                r, "runtime_values", return_value={"ONESA_IDENTITY_ENABLED": enabled}
            ),
        ):
            return identity.native_bridge(self.plan, self.binding)

    def test_only_both_accepted_native_bindings_and_unchanged_disabled_configs_pass(
        self,
    ):
        self.assertEqual(self.run_bridge(), self.observations)
        for options in [
            {"enabled": "true"},
            {"changed_digest": True},
            {"verify": lambda *args, **kwargs: []},
        ]:
            with self.subTest(options=options), self.assertRaises(r.Refused):
                self.run_bridge(**options)

    def test_revoked_bridge_failure_propagates_without_substituting_another_record(
        self,
    ):
        def revoked(*args, **kwargs):
            raise r.Refused("SIGNED_LEDGER_RECORD_MISSING_OR_REVOKED")

        with self.assertRaisesRegex(r.Refused, "REVOKED"):
            self.run_bridge(verify=revoked)

    def test_foreign_source_or_identity_scope_cannot_be_reused_as_native_bridge(self):
        for field, wrong in [
            ("component", "identity-api"),
            ("candidate_commit", "0" * 40),
            ("source_tree", "0" * 40),
            ("source_sha256", "0" * 64),
        ]:
            native = self.references["api"]["binding"]
            original = native[field]
            native[field] = wrong
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(r.Refused, "DISTINCT_SCOPE"),
            ):
                self.run_bridge()
            native[field] = original

    def test_image_frontend_deployment_or_runtime_mismatch_blocks_activation(self):
        native = self.references["api"]["binding"]
        original = native["artifact_digest"]
        native["artifact_digest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(r.Refused, "QUALIFIED_BRIDGE_IMAGE"):
            self.run_bridge()
        native["artifact_digest"] = original
        for field, wrong in [
            ("deployment_id", "dpl_foreign123456"),
            ("backend_runtime", {**self.runtime, "container_id": "replaced"}),
            ("frontend", {"source_commit": "0" * 40}),
        ]:
            original = self.observations["frontend"][field]
            self.observations["frontend"][field] = wrong
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(r.Refused, "FRONTEND_AND_API"),
            ):
                self.run_bridge()
            self.observations["frontend"][field] = original


if __name__ == "__main__":
    unittest.main()
