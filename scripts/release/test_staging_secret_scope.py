"""Run boolean-only owner diagnostics with synthetic values, never provider secrets."""

import json
import os
import re
import subprocess
import unittest
from pathlib import Path

from evidence import PRODUCT

ROOT = Path(__file__).resolve().parents[2]


class SecretScopeTests(unittest.TestCase):
    def test_environment_and_repository_diagnostics_expose_only_availability(self):
        workflow = (ROOT / ".github/workflows/release-candidate.yml").read_text()
        expected = set(
            re.findall(
                r"secrets\.(STAGING_[A-Z0-9_]+)",
                (ROOT / ".github/workflows/release-staging.yml").read_text(),
            )
        )
        for scope in ("environment", "repository"):
            job = workflow.split("  staging-" + scope + "-availability:\n", 1)[1].split(
                "  staging-", 1
            )[0]
            self.assertEqual("    environment: Staging" in job, scope == "environment")
            self.assertIn("github.event_name == 'workflow_dispatch'", job)
            self.assertIn("github.actor == 'DruHustle'", job)
            self.assertIn("github.triggering_actor == 'DruHustle'", job)
            self.assertNotIn("checkout", job)
            self.assertNotIn("pull_request", job)
            self.assertEqual(
                set(re.findall(r"secrets\.(STAGING_[A-Z0-9_]+)", job)), expected
            )
            program = job.split("node <<'NODE'\n", 1)[1].split("        NODE", 1)[0]
            for supplied in (False, True):
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "STAGING_SECRET_SCOPE": scope,
                    "GITHUB_REPOSITORY": "DruHustle/" + PRODUCT,
                }
                sentinel = "SYNTHETIC-NEVER-LOG-THIS-CREDENTIAL"
                if supplied:
                    env.update({name: sentinel for name in expected})
                result = subprocess.run(
                    ["node"],
                    input=program,
                    text=True,
                    capture_output=True,
                    env=env,
                    timeout=10,
                    check=False,
                )
                self.assertNotIn(sentinel, result.stdout + result.stderr)
                record = json.loads(result.stdout)
                self.assertEqual(result.returncode, 0 if supplied else 1)
                self.assertEqual(record["all_available"], supplied)
                self.assertEqual(
                    record["available"], {name: supplied for name in expected}
                )
                self.assertFalse(record["values_disclosed"])
                self.assertFalse(record["provider_requests"])
                self.assertEqual(record["scope"], scope)


if __name__ == "__main__":
    unittest.main()
