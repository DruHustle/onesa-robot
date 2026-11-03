"""Synthetic source/configuration/session boundaries for the identity profile."""

import copy
import datetime as dt
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import oracle_robot as r
import oracle_robot_frontend as frontend
import oracle_robot_identity as identity
import oracle_robot_probe as probe


def fixture():
    binding = {
        "repository": "DruHustle/onesa-robot",
        "component": "identity-api",
        "target_environment": "production",
        "candidate_commit": "a" * 40,
        "source_tree": "b" * 40,
        "source_sha256": "c" * 64,
        "build_sha256": "d" * 64,
        "artifact_digest": "sha256:" + "e" * 64,
        "configuration_sha256": {"staging": "f" * 64, "production": "1" * 64},
    }
    tool = {
        "source_commit": binding["candidate_commit"],
        "source_tree": binding["source_tree"],
        "source_sha256": binding["source_sha256"],
    }
    plan = {
        "schema_version": 1,
        "bindings": {
            "identity-api": binding,
            "identity-frontend": {**binding, "component": "identity-frontend"},
        },
        "compose_sha256": "2" * 64,
        "origins": {
            "staging": "https://onesa-ai-robot-git-staging-fixture.vercel.app",
            "production": "https://robot.onesa.eu",
        },
        "api_origins": {
            "staging": "https://robot-api-staging.onesa.eu",
            "production": "https://robot-api.onesa.eu",
        },
        "baseline_runtime_sha256": {"staging": "3" * 64, "production": "4" * 64},
        "identity": {
            "profile": identity.PROFILE,
            **tool,
            "environments": {},
            "native_bridge": {},
        },
    }
    configurations, held = {}, {}
    for number, environment in enumerate(["staging", "production"]):
        issuer = (
            "https://onesa-company-portal-staging-fixture.vercel.app"
            if environment == "staging"
            else "https://portal.onesa.eu"
        ) + "/api/v1/identity"
        accounts = {
            role: {
                "subject": f"00000000-0000-4000-8000-{number * 2 + offset:012d}",
                "email": f"{role}-{environment}@example.test",
            }
            for offset, role in enumerate(["administrator", "operator"], 1)
        }
        secret = ("synthetic-" + environment + "-").ljust(64, "a")
        entry = {
            "issuer": issuer,
            "client_id": "robot-synthetic-" + environment,
            "client_secret_sha256": hashlib.sha256(secret.encode()).hexdigest(),
            "callback_url": plan["origins"][environment]
            + "/api/v1/auth/identity/callback",
            "portal_bypass_sha256": None,
            "accounts": accounts,
            "baseline_frontend": {
                "source": binding["candidate_commit"],
                "deployment_id": "dpl_123456789" + str(number),
            },
        }
        plan["identity"]["environments"][environment] = entry
        configurations[environment] = {
            "ROBOT_DEPLOYMENT_ENVIRONMENT": environment,
            "ROBOT_STORAGE_MODE": "turso",
            "ROBOT_ACTUATION_ENABLED": "false",
            "ROBOT_PUBLIC_ORIGIN": plan["origins"][environment],
            "ROBOT_OPERATOR_NAME": "fixture-operator",
            "ROBOT_OPERATOR_PASSWORD_HASH": "legacy-" + environment,
            "ROBOT_TURSO_DATABASE_URL": "synthetic-journal-" + environment,
            "ROBOT_TURSO_AUTH_TOKEN": "synthetic-token-" + environment,
            "ONESA_IDENTITY_ENABLED": "true",
            "ONESA_IDENTITY_ISSUER": issuer,
            "ONESA_IDENTITY_CLIENT_ID": entry["client_id"],
            "ONESA_IDENTITY_CLIENT_SECRET": secret,
            "ONESA_IDENTITY_CALLBACK_URL": entry["callback_url"],
        }
        held[environment] = {
            "accounts": {
                role: {
                    **account,
                    "password": "synthetic-private-password",
                    "totp_secret": "JBSWY3DPEHPK3PXP",
                }
                for role, account in accounts.items()
            },
            "portal_bypass": None,
        }
    created = r.now()
    custody = {
        "schema_version": 1,
        "candidate_commit": binding["candidate_commit"],
        "created_at": created.isoformat(),
        "expires_at": (created + dt.timedelta(hours=1)).isoformat(),
        "mechanism": "owner-tty-existing-identity-credentials",
        "release_authority": False,
        "environments": held,
    }
    return binding, tool, plan, configurations, custody


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.binding, self.tool, self.plan, self.configurations, self.custody = (
            fixture()
        )

    def test_explicit_profile_is_exact_installed_source_and_preserves_default(self):
        self.assertEqual(
            identity.profile(self.plan, self.binding, self.tool), self.plan["identity"]
        )
        self.assertIsNone(
            identity.profile({}, {**self.binding, "component": "api"}, self.tool)
        )
        self.assertEqual(identity.configuration(None, "staging", {}), set())
        with self.assertRaisesRegex(r.Refused, "EXPLICIT_IDENTITY_PROFILE"):
            identity.configuration(None, "staging", self.configurations["staging"])
        for key in ["source_commit", "source_tree", "source_sha256"]:
            changed = {**self.tool, key: "0" * len(self.tool[key])}
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(r.Refused, "EXACT_INSTALLED_SOURCE"),
            ):
                identity.profile(self.plan, self.binding, changed)

    def test_cross_environment_foreign_callback_and_extra_profile_fields_fail_closed(
        self,
    ):
        for key, wrong in [
            ("issuer", "https://attacker.example/api/v1/identity"),
            ("issuer", "https://portal.onesa.eu/api/v1/identity"),
            ("callback_url", "https://attacker.example/callback"),
            ("client_id", "short"),
            ("portal_bypass_sha256", "unbound"),
        ]:
            plan = copy.deepcopy(self.plan)
            plan["identity"]["environments"]["staging"][key] = wrong
            with self.subTest(key=key, wrong=wrong), self.assertRaises(r.Refused):
                identity.profile(plan, self.binding, self.tool)
        plan = copy.deepcopy(self.plan)
        plan["identity"]["skip_safety"] = True
        with self.assertRaises(r.Refused):
            identity.profile(plan, self.binding, self.tool)
        plan = copy.deepcopy(self.plan)
        plan["identity"]["environments"]["staging"]["accounts"]["operator"] = plan[
            "identity"
        ]["environments"]["production"]["accounts"]["operator"]
        with self.assertRaisesRegex(r.Refused, "SEPARATE"):
            identity.profile(plan, self.binding, self.tool)

    def test_only_exact_identity_configuration_is_permitted(self):
        values = self.configurations["staging"]
        self.assertEqual(
            identity.configuration(self.plan["identity"], "staging", values),
            identity.KEYS,
        )
        for key in identity.KEYS:
            with self.subTest(key=key), self.assertRaises(r.Refused):
                identity.configuration(
                    self.plan["identity"], "staging", {**values, key: "changed"}
                )

    def validate_runner(self, plan=None, prepared=None):
        selected = plan or self.plan
        values = prepared or self.configurations
        baseline = {
            environment: {
                key: value for key, value in config.items() if key not in identity.KEYS
            }
            for environment, config in self.configurations.items()
        }
        folder = Path("/synthetic/artifacts")
        paths = {r.ENV[env]: baseline[env] for env in r.ENV}
        paths.update(
            {r.candidate_environment(self.binding, env): values[env] for env in r.ENV}
        )
        pins = {
            folder / "source.tar": self.binding["source_sha256"],
            folder / "image.tar.gz": self.binding["build_sha256"],
            r.COMPOSE: selected["compose_sha256"],
        }
        pins.update(
            {r.ENV[env]: selected["baseline_runtime_sha256"][env] for env in r.ENV}
        )
        pins.update(
            {
                r.candidate_environment(self.binding, env): self.binding[
                    "configuration_sha256"
                ][env]
                for env in r.ENV
            }
        )
        with (
            patch.object(
                r, "read", side_effect=lambda path: selected if path == r.PLAN else {}
            ),
            patch.object(r, "verify_toolchain", return_value=self.tool),
            patch.object(r, "component_folder", return_value=folder),
            patch.object(r, "private", side_effect=lambda path: path),
            patch.object(r, "sha", side_effect=pins.__getitem__),
            patch.object(r, "source_inventory"),
            patch.object(r, "runtime_values", side_effect=paths.__getitem__),
            patch.object(r, "image_security"),
            patch.object(r, "verify_application_bytes"),
            patch.object(r, "verify_probe_bytes"),
            patch.object(r, "credentials"),
            patch.object(identity, "credentials"),
            patch.object(identity, "native_bridge"),
        ):
            return r.validate_plan(self.binding)

    def test_real_runner_rejects_unrelated_configuration_even_with_identity_profile(
        self,
    ):
        self.assertEqual(self.validate_runner()[0], Path("/synthetic/artifacts"))
        for key in [
            "ROBOT_OPERATOR_PASSWORD_HASH",
            "ROBOT_TURSO_DATABASE_URL",
            "ROBOT_TURSO_AUTH_TOKEN",
            "ROBOT_ACTUATION_ENABLED",
            "ROBOT_CONTEXT_IMPORT_ENABLED",
        ]:
            changed = copy.deepcopy(self.configurations)
            changed["staging"][key] = "changed"
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(r.Refused, "UNRELATED_CONFIGURATION"),
            ):
                self.validate_runner(prepared=changed)
        without = copy.deepcopy(self.plan)
        del without["identity"]
        with self.assertRaisesRegex(r.Refused, "EXPLICIT_IDENTITY_PROFILE"):
            self.validate_runner(plan=without)

    def test_identity_credentials_are_expiring_existing_subjects_not_authority(self):
        result = identity.validate_credentials(self.custody, self.plan, self.binding)
        self.assertEqual(result, self.custody["environments"])
        for key, wrong in [
            ("release_authority", True),
            ("candidate_commit", "f" * 40),
            ("expires_at", (r.now() - dt.timedelta(seconds=1)).isoformat()),
        ]:
            with self.subTest(key=key), self.assertRaises(r.Refused):
                identity.validate_credentials(
                    {**self.custody, key: wrong}, self.plan, self.binding
                )
        changed = copy.deepcopy(self.custody)
        changed["environments"]["staging"]["accounts"]["administrator"]["subject"] = (
            "00000000-0000-4000-8000-000000000099"
        )
        with self.assertRaisesRegex(r.Refused, "CREDENTIAL_BINDING"):
            identity.validate_credentials(changed, self.plan, self.binding)

    def test_bad_collection_is_rejected_before_any_credential_file_write(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(identity, "CUSTODY", Path(directory) / "identity.json"),
            patch.object(r, "private", side_effect=lambda path, **kwargs: path),
        ):
            with self.assertRaises(r.Refused):
                identity.collect(
                    self.plan, self.binding, prompt=lambda _prompt: "invalid"
                )
            self.assertFalse(identity.CUSTODY.exists())

    def test_only_exact_expired_custody_can_be_cleared_without_changing_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity.json"
            path.write_text(json.dumps(self.custody))
            with (
                patch.object(identity, "CUSTODY", path),
                patch.object(
                    identity.sys,
                    "argv",
                    [
                        "identity",
                        "--candidate",
                        self.binding["candidate_commit"],
                        "--clear-expired",
                    ],
                ),
                patch.object(identity.os, "geteuid", return_value=0),
                patch.dict(identity.os.environ, {"SUDO_USER": "ubuntu"}, clear=True),
                patch.object(r, "read", return_value=self.custody),
                patch.object(r, "sync_directory") as sync,
                patch("builtins.print") as output,
            ):
                with self.assertRaisesRegex(r.Refused, "ONLY_THIS_EXPIRED"):
                    identity.main()
                self.assertTrue(path.exists())
                sync.assert_not_called()
                self.custody["expires_at"] = (
                    r.now() - dt.timedelta(seconds=1)
                ).isoformat()
                identity.main()
                self.assertFalse(path.exists())
                sync.assert_called_once_with(path.parent)
                self.assertFalse(
                    json.loads(output.call_args.args[0])["release_authority"]
                )


class SessionTests(unittest.TestCase):
    def setUp(self):
        binding, _tool, plan, _configs, custody = fixture()
        with patch.object(
            identity, "credentials", return_value=custody["environments"]
        ):
            selected = identity.request(plan, binding, "staging")
        self.request = {
            "identity": selected,
            "provider": {"bypass": "synthetic-robot-bypass"},
            "probe_image": "sha256:" + "a" * 64,
            "cpu_limit": "1",
        }
        self.payload = {
            "identity": selected,
            "actor": "administrator",
            "robot_bypass": "synthetic-robot-bypass",
        }
        instant = r.now().timestamp()
        self.value = {
            "schema_version": 1,
            "origin": selected["origin"],
            "issuer": selected["issuer"],
            "subject": selected["accounts"]["administrator"]["subject"],
            "role": "administrator",
            "csrf": "synthetic-csrf-" + "x" * 32,
            "confirmation_expires_at": instant + 240,
            "cookie": {
                "name": "robot_session",
                "value": "synthetic-session-" + "x" * 32,
                "domain": "onesa-ai-robot-git-staging-fixture.vercel.app",
                "path": "/api/v1",
                "expires": instant + 900,
                "httpOnly": True,
                "secure": True,
                "sameSite": "Strict",
            },
        }

    def test_browser_session_is_fresh_exact_role_and_original_host_only_cookie(self):
        identity.validate_session(self.value, self.payload)
        for key, wrong in [
            ("role", "operator"),
            ("subject", "foreign-subject"),
            ("origin", "https://robot-api-staging.onesa.eu"),
            ("confirmation_expires_at", 0),
        ]:
            with self.subTest(key=key), self.assertRaises(r.Refused):
                identity.validate_session({**self.value, key: wrong}, self.payload)
        for key, wrong in [
            ("domain", ".vercel.app"),
            ("secure", False),
            ("httpOnly", False),
            ("path", "/"),
            ("sameSite", "None"),
        ]:
            changed = copy.deepcopy(self.value)
            changed["cookie"][key] = wrong
            with self.subTest(key=key), self.assertRaises(r.Refused):
                identity.validate_session(changed, self.payload)

    def test_fixed_pinned_child_receives_secrets_only_in_private_stdin(self):
        child = subprocess.CompletedProcess([], 0, r.canonical(self.value), b"")
        with patch.object(identity.subprocess, "run", return_value=child) as run:
            self.assertEqual(identity.acquire(self.request), self.value)
        call = run.call_args
        args = call.args[0]
        self.assertEqual(args[-2:], [self.request["probe_image"], identity.BROWSER])
        self.assertEqual(args[args.index("--pull") + 1], "never")
        self.assertIn("--read-only", args)
        self.assertNotIn("-v", args)
        self.assertNotIn("-e", args)
        self.assertNotIn(
            "synthetic-private-password", str(args) + str(call.kwargs["env"])
        )
        self.assertIn(b"synthetic-private-password", call.kwargs["input"])
        self.assertIs(json.loads(call.kwargs["input"])["api_only"], False)
        with (
            patch.object(
                identity.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 1, b"secret", b"private"),
            ),
            self.assertRaisesRegex(r.Refused, "ACQUISITION_FAILED"),
        ):
            identity.acquire(self.request)

    def test_api_entry_is_explicit_boolean_and_remains_in_private_stdin(self):
        child = subprocess.CompletedProcess([], 0, r.canonical(self.value), b"")
        with patch.object(identity.subprocess, "run", return_value=child) as run:
            identity.acquire({**self.request, "identity_api_only": True})
            self.assertIs(json.loads(run.call_args.kwargs["input"])["api_only"], True)
        for wrong in ["true", 1, None]:
            with (
                self.subTest(wrong=wrong),
                patch.object(identity.subprocess, "run") as run,
                self.assertRaisesRegex(r.Refused, "ENTRY_SCOPE"),
            ):
                identity.acquire({**self.request, "identity_api_only": wrong})
            run.assert_not_called()

    def test_live_me_revalidation_and_cookie_origin_are_required(self):
        api = probe.API(self.value["origin"], self.value["origin"], {})
        observed = {
            "authentication_method": "onesa",
            "role": "administrator",
            "csrf": self.value["csrf"],
            "confirmation_expires_at": self.value["confirmation_expires_at"],
        }
        with patch.object(api, "call", return_value=observed) as call:
            identity.attach(api, self.value)
        call.assert_called_once_with("/api/v1/auth/me")
        cookie = next(iter(api.jar))
        self.assertFalse(cookie.domain_specified)
        self.assertTrue(cookie.secure)
        self.assertEqual(cookie.domain, self.value["cookie"]["domain"])
        api.origin = "https://robot-api-staging.onesa.eu"
        with (
            patch.object(api, "call") as call,
            self.assertRaisesRegex(r.Refused, "MUST_NOT_BE_REWRITTEN"),
        ):
            identity.attach(api, self.value)
        call.assert_not_called()
        api.origin = self.value["origin"]
        with (
            patch.object(api, "call", return_value={**observed, "role": "viewer"}),
            self.assertRaisesRegex(r.Refused, "REVALIDATION"),
        ):
            identity.attach(api, self.value)

    def test_shared_logout_does_not_claim_global_revocation_when_unconfirmed(self):
        api = probe.API(
            self.value["origin"],
            self.value["origin"],
            {},
            identity_request=self.request,
        )
        api.csrf = self.value["csrf"]
        with (
            patch.object(api, "call", return_value={"identity_signed_out": False}),
            self.assertRaisesRegex(r.Refused, "SIGNOUT_MUST_BE_CONFIRMED"),
        ):
            api.logout()


class RuntimeEvidenceTests(unittest.TestCase):
    def runtime(self):
        return {
            "Id": "actual-container",
            "Image": "sha256:" + "a" * 64,
            "RestartCount": 0,
            "Config": {
                "Env": ["PRIVATE_VALUE=synthetic-secret"],
                "Labels": {"org.onesa.source-commit": "b" * 40},
            },
            "HostConfig": {"Privileged": False},
            "Mounts": [{"Source": "/private/fixture", "Destination": "/data"}],
            "NetworkSettings": {
                "Networks": {"robot": {"NetworkID": "fixed-network", "IPAddress": "1"}}
            },
            "State": {
                "Running": True,
                "OOMKilled": False,
                "StartedAt": "synthetic-start",
                "Health": {"Status": "healthy"},
            },
        }

    def observe(self, before, after, folder):
        (folder / "observations").mkdir(mode=0o700)
        binding = {"component": "frontend", "candidate_commit": "b" * 40}
        plan = {
            "origins": {"production": "https://robot.onesa.eu"},
            "api_origins": {"production": "https://robot-api.onesa.eu"},
            "probe_image": "sha256:" + "c" * 64,
        }
        result = {
            "binding": binding,
            "environment": "production",
            "operation_id": "synthetic-observation",
            "status": "PASS",
        }
        module = SimpleNamespace(execute=Mock(return_value=result))
        with (
            patch.object(r, "local_identity", return_value=r.summary(before)),
            patch.object(
                r,
                "inventory",
                side_effect=[{r.TARGET["production"]: row} for row in [before, after]],
            ),
            patch.object(r, "credentials", return_value={"production": {}}),
            patch.object(r, "probe_cpu_limit", return_value="1"),
            patch.object(r, "history", return_value={"retained": "d" * 64}),
            patch.object(r, "private", side_effect=lambda path: path),
            patch.object(
                frontend,
                "observe_provider",
                return_value={
                    "origin": "https://robot.onesa.eu",
                    "provider": {
                        "deployment_id": "dpl_actual12345678",
                        "source": "b" * 40,
                    },
                },
            ),
            patch("importlib.util.spec_from_file_location", return_value=Mock()),
            patch("importlib.util.module_from_spec", return_value=module),
        ):
            return r.probe(
                binding,
                "production",
                "synthetic-observation",
                folder,
                plan,
                read_only=True,
            )

    def test_actual_runtime_and_frontend_evidence_are_retained_without_secrets(self):
        before = self.runtime()
        after = copy.deepcopy(before)
        # Ephemeral network addressing is not configuration authority.
        after["NetworkSettings"]["Networks"]["robot"]["IPAddress"] = "2"
        with tempfile.TemporaryDirectory() as directory:
            result = self.observe(before, after, Path(directory))
        self.assertEqual(result["backend_runtime"], r.backend_runtime(before))
        self.assertEqual(result["deployment_id"], "dpl_actual12345678")
        self.assertEqual(result["frontend"]["deployment_id"], result["deployment_id"])
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertNotIn("/private/fixture", json.dumps(result))

    def test_changed_configuration_during_acceptance_never_writes_pass_receipt(self):
        before = self.runtime()
        for field, value in [
            (
                "Config",
                {
                    "Env": ["PRIVATE_VALUE=changed"],
                    "Labels": before["Config"]["Labels"],
                },
            ),
            ("HostConfig", {"Privileged": True}),
            ("Mounts", [{"Source": "/foreign", "Destination": "/data"}]),
            ("NetworkSettings", {"Networks": {"robot": {"NetworkID": "foreign"}}}),
        ]:
            after = {**before, field: value}
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(r.Refused, "RUNTIME_CHANGED_DURING"):
                    self.observe(before, after, Path(directory))
                self.assertFalse(list(Path(directory).rglob("receipt.json")))


if __name__ == "__main__":
    unittest.main()
