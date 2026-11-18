"""Regression checks for credentials and triggers at the private-Free boundary."""

import json
import re
import subprocess
import unittest
from pathlib import Path

from evidence import PRODUCT

ROOT = Path(__file__).resolve().parents[2]


class WorkflowBoundaryTests(unittest.TestCase):
    def test_all_evidence_outputs_use_the_required_record_filename(self):
        outputs = []
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            outputs.extend(
                re.findall(
                    r'evidence\.py[^\n]*--output\s+"([^"\n]+)"', path.read_text()
                )
            )
        self.assertGreaterEqual(len(outputs), 8)
        for output in outputs:
            self.assertEqual(Path(output).name, "record.json", output)
        staging = (ROOT / ".github/workflows/release-staging.yml").read_text()
        self.assertIn("robot-release/preflight/record.json", staging)
        for script in ("cloud.py", "edge-manifest.py"):
            self.assertIn(
                '/ "preflight" / "record.json"',
                (ROOT / "scripts/release" / script).read_text(),
            )

    def test_free_workflows_are_credential_free_and_authorization_is_manual(self):
        for name in ("free-qualification.yml", "free-authorization.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertIn("workflow_dispatch:", text)
            self.assertNotRegex(
                text, r"(?m)^  (push|pull_request|pull_request_target):"
            )
            self.assertNotRegex(text, r"\$\{\{\s*secrets\.")
            if name == "free-authorization.yml":
                self.assertNotIn("workflow_run:", text)
            self.assertNotIn("environment:", text)
            self.assertIn("github.triggering_actor", text)
            self.assertIn("persist-credentials: false", text)

    def test_private_production_jobs_are_disabled_and_json_fallback_removed(self):
        text = (ROOT / ".github/workflows/deploy.yml").read_text()
        self.assertNotIn("RELEASE_QUALIFICATION_JSON", text)
        self.assertNotIn("inputs.approval_run == ''", text)
        self.assertNotRegex(text, r"(?m)^  (push|pull_request|workflow_run):")
        job_conditions = re.findall(r"^    if: (.+)", text, re.MULTILINE)
        self.assertTrue(job_conditions)
        self.assertTrue(
            all(
                "github.event.repository.private == false" in value
                for value in job_conditions
            )
        )

    def test_staging_uses_separate_credentials(self):
        text = (ROOT / ".github/workflows/release-staging.yml").read_text()
        self.assertIn("secrets.STAGING_RENDER_API_KEY", text)
        self.assertIn("secrets.STAGING_VERCEL_TOKEN", text)
        self.assertNotRegex(
            text,
            r"secrets\.(RENDER_API_KEY|VERCEL_TOKEN|VERCEL_PROJECT_ID|VERCEL_AUTOMATION_BYPASS_SECRET)\b",
        )
        repository_job, environment_job = text.split("  deploy-environment:\n", 1)
        self.assertNotRegex(repository_job, r"(?m)^\s*environment:")
        self.assertIn("github.event.repository.private == true", repository_job)
        self.assertIn("github.event.repository.private == false", environment_job)
        self.assertIn("vars.STAGING_SECRET_SCOPE == 'environment'", environment_job)
        self.assertIn("environment: Staging", environment_job)
        self.assertIn("steps: *deploy_steps", environment_job)
        # The single shared step sequence is selected once; private Free has no environment dependency.
        references = re.findall(r"secrets\.([A-Z_]+)", text)
        self.assertTrue(references)
        self.assertTrue(all(name.startswith("STAGING_") for name in references))
        self.assertNotIn("secrets.VERCEL_PROJECT_ID", text)

    def test_staging_scope_selects_one_trusted_job_and_private_fallback(self):
        text = (ROOT / ".github/workflows/release-staging.yml").read_text()
        conditions = re.findall(r"^    if: (.+)", text, re.MULTILINE)
        self.assertEqual(len(conditions), 2)
        program = r"""
const conditions = JSON.parse(process.argv[1]);
const repository = process.argv[2];
for (const privateRepo of [false, true]) {
  for (const scope of ['', 'repository', 'environment']) {
    for (const trusted of [false, true]) {
      const github = {repository, actor: trusted ? 'DruHustle' : 'outsider',
        triggering_actor: 'DruHustle', event_name: 'workflow_dispatch',
        ref: 'refs/heads/staging', event: {repository: {private: privateRepo}}};
      const vars = {STAGING_SECRET_SCOPE: scope, STAGING_AUTOMATION_ENABLED: 'false', ONESA_PRIVATE_HOSTED_VALIDATION_ENABLED: 'true'};
      const selected = conditions.map(condition =>
        new Function('github', 'vars', 'return (' + condition + ')')(github, vars));
      const expected = trusted ? (privateRepo || scope !== 'environment' ? [true, false] : [false, true]) : [false, false];
      if (JSON.stringify(selected) !== JSON.stringify(expected)) process.exit(1);
    }
  }
}
"""
        result = subprocess.run(
            ["node", "-e", program, json.dumps(conditions), "DruHustle/" + PRODUCT],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_registered_staging_entrypoint_supports_private_free_without_production(
        self,
    ):
        name = "release-candidate.yml" if PRODUCT.endswith("robot") else "deploy.yml"
        text = (ROOT / ".github/workflows" / name).read_text()
        staging = re.split(
            r"(?m)^  [A-Za-z0-9_-]+:", text.split("\n  staging:\n", 1)[1], maxsplit=1
        )[0]
        condition = re.search(r"^    if: (.+)", staging, re.MULTILINE).group(1)
        condition = condition.removeprefix("${{ ").removesuffix(" }}")
        self.assertIn("uses: ./.github/workflows/release-staging.yml", staging)
        self.assertNotRegex(staging, r"secrets\.(?!STAGING_)[A-Z_]+")
        program = r"""
const condition=process.argv[1], repository=process.argv[2];
for (const privateRepo of [true, false]) {
 for (const invalid of ['', 'actor', 'ref', 'event', 'target']) {
  const github={repository,actor:invalid==='actor'?'outsider':'DruHustle',
   triggering_actor:'DruHustle',ref:invalid==='ref'?'refs/heads/main':'refs/heads/staging',
   event_name:invalid==='event'?'pull_request':'workflow_dispatch',
   event:{repository:{private:privateRepo}}};
  const inputs={target:invalid==='target'?'production':'staging'};
  const selected=new Function('github','inputs','return ('+condition+')')(github,inputs);
  if(selected !== (invalid==='')) process.exit(1);
 }
}
"""
        result = subprocess.run(
            ["node", "-e", program, condition, "DruHustle/" + PRODUCT],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_git_previews_do_not_enable_native_production_deployment(self):
        config = json.loads(
            (ROOT / "onesa-ai-robot-frontend" / "vercel.json").read_text()
        )
        self.assertEqual(
            config["git"]["deploymentEnabled"],
            {
                "**": False,
                "main": False,
                "dev": False,
                "staging": True,
            },
        )

    def test_automatic_staging_is_opt_in_owner_main_only_and_preflights_first(self):
        text = (ROOT / ".github/workflows/release-staging.yml").read_text()
        for constraint in (
            "vars.STAGING_AUTOMATION_ENABLED == 'true'",
            "github.event.repository.private == true",
            "github.event.workflow_run.event",
            "== 'push'",
            "head_repository.full_name",
            "head_sha == github.sha",
            "conclusion ==",
            "github.triggering_actor == 'DruHustle'",
        ):
            self.assertIn(constraint, " ".join(text.split()))
        self.assertLess(
            text.index("scripts/release/evidence.py stage-preflight"),
            text.index("secrets.STAGING_"),
        )
        self.assertNotIn("pull_request_target", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_pr_validation_has_no_local_runner_dependency_or_provider_secrets(self):
        ci = {
            "onesa-company-portal": "verify.yml",
            "onesa-ai-platform": "ci.yml",
            "onesa-robot": "scaffold.yml",
        }[PRODUCT]
        for name in (ci, "repository-hygiene.yml"):
            text = (ROOT / ".github/workflows" / name).read_text()
            self.assertNotIn("local-validation", text)
            self.assertNotIn("self-hosted", text)
            self.assertNotRegex(text, r"\$\{\{\s*secrets\.")
            self.assertIn("pull_request:", text)
            self.assertRegex(text, r"pull_request:\n    branches:\n    - main")

    def test_temporary_security_refs_cannot_authorize_release_or_staging_credentials(
        self,
    ):
        authorization = (ROOT / ".github/workflows/free-authorization.yml").read_text()
        condition = re.search(r"    if: ([\s\S]+?)\n    runs-on:", authorization).group(
            1
        )
        staging = (ROOT / ".github/workflows/release-staging.yml").read_text()
        staging_conditions = re.findall(r"^    if: (.+)", staging, re.MULTILINE)
        program = r"""
const conditions=JSON.parse(process.argv[1]), repository=process.argv[2];
for(const ref of ['refs/heads/dependabot/pip/security-fixture','refs/heads/security/production-fixture']) {
 for(const actor of ['DruHustle','dependabot[bot]']) {
  const github={repository,actor,triggering_actor:actor,ref,event_name:'workflow_dispatch',
   event:{repository:{private:false}}};
  const vars={STAGING_SECRET_SCOPE:'environment',STAGING_AUTOMATION_ENABLED:'true',ONESA_PRIVATE_HOSTED_VALIDATION_ENABLED:'true'};
  for(const condition of conditions)if(new Function('github','vars','return ('+condition+')')(github,vars))process.exit(1);
 }
}
const github={repository,actor:'dependabot[bot]',triggering_actor:'dependabot[bot]',ref:'refs/heads/main',event:{repository:{private:false}}};
const vars={ONESA_PRIVATE_HOSTED_VALIDATION_ENABLED:'true'};
if(new Function('github','vars','return ('+conditions[0]+')')(github,vars))process.exit(1);
"""
        result = subprocess.run(
            [
                "node",
                "-e",
                program,
                json.dumps([condition, *staging_conditions]),
                "DruHustle/" + PRODUCT,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_version_target_does_not_claim_to_redirect_dependabot_security_fixes(self):
        config = (ROOT / ".github/dependabot.yml").read_text()
        ecosystems = re.split(r"(?m)^- package-ecosystem:", config)[1:]
        self.assertTrue(ecosystems)
        for ecosystem in ecosystems:
            self.assertRegex(ecosystem, r"(?m)^  target-branch: dev$")
            self.assertRegex(ecosystem, r"(?m)^  open-pull-requests-limit: 0$")
        policy = (ROOT / "AGENTS.md").read_text()
        self.assertIn("security fixes", policy)
        self.assertIn("default main", policy)
        self.assertIn("three permanent branches", policy)


if __name__ == "__main__":
    unittest.main()


class HostedOnlyRunnerRemovalTests(unittest.TestCase):
    def test_removed_runner_has_no_workflow_or_provisioning_consumer(self):
        self.assertFalse((ROOT / "scripts/runner").exists())
        self.assertFalse((ROOT / ".github/workflows/_local-validation.yml").exists())
        for workflow in (ROOT / ".github/workflows").glob("*.yml"):
            text = workflow.read_text()
            self.assertNotIn("self-hosted", text, str(workflow))
            self.assertNotIn("scripts/runner", text, str(workflow))
            self.assertNotIn("_local-validation.yml", text, str(workflow))
            if "runs-on:" in text:
                self.assertIn("ONESA_PRIVATE_HOSTED_VALIDATION_ENABLED", text)

    def test_hosted_budget_guard_allows_public_and_blocks_private_without_opt_in(self):
        ci = {
            "onesa-company-portal": "verify.yml",
            "onesa-ai-platform": "ci.yml",
            "onesa-robot": "scaffold.yml",
        }[PRODUCT]
        text = (ROOT / ".github/workflows" / ci).read_text()
        job = text.split("  changes:\n", 1)[1]
        condition = re.search(r"^    if: (.+)", job, re.MULTILINE).group(1)
        condition = condition.removeprefix("${{ ").removesuffix(" }}")
        program = r"""
const condition=process.argv[1];
for(const privateRepo of [false,true]) for(const allowance of ['', 'false', 'true']) {
 const github={event:{repository:{private:privateRepo}}};
 const vars={ONESA_PRIVATE_HOSTED_VALIDATION_ENABLED:allowance};
 const actual=new Function('github','vars','success','always','return ('+condition+')')(github,vars,()=>true,()=>true);
 if(actual!==(!privateRepo || allowance==='true'))process.exit(1);
}
"""
        result = subprocess.run(
            ["node", "-e", program, condition],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_every_hosted_job_denies_private_execution_without_allowance(self):
        conditions = []
        for path in (ROOT / ".github/workflows").glob("*.yml"):
            text = path.read_text().split("jobs:\n", 1)[1]
            for block in re.split(r"(?m)^  [A-Za-z0-9_-]+:\n", text)[1:]:
                if "    runs-on:" not in block:
                    continue
                match = re.search(r"^    if: (.+)", block, re.MULTILINE)
                self.assertIsNotNone(match, str(path))
                conditions.append(
                    match.group(1).removeprefix("${{ ").removesuffix(" }}")
                )
        program = r"""
for(const condition of JSON.parse(process.argv[1])) {
 const github={event:{repository:{private:true}}},vars={};
 const result=new Function('github','vars','success','always','return ('+condition+')')(github,vars,()=>true,()=>true);
 if(result!==false)process.exit(1);
}
"""
        result = subprocess.run(
            ["node", "-e", program, json.dumps(conditions)],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
