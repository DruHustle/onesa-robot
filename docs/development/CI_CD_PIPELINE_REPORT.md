> Owner update — 2026-10-18: GitHub Free is permanent; public visibility is temporary.
> Local-runner implementation has now been removed, superseding earlier private-first
> runner plans below. Use standard hosted runners while public and included hosted
> allowance after a future owner visibility change. No local executor is a fallback.
> Historical runner/plan observations are retained as history, not current instructions.

# CI/CD pipeline implementation and audit — onesa-ai-robot

> Current branch/dependency policy: the owner's latest 14 October 2026 instruction
> authorizes temporary security PR/candidate branches while retaining dev/staging/main
> permanently. Ordinary version PRs remain disabled. Follow
> [the maintained security procedure](../engineering/CI_CD.md#security-pr-integration-and-urgent-candidates).
> Historical branch/update-count observations below do not override this current policy.

Audit date: 2026-10-11. Prepared locally on `feature/cicd-review-optimization`; no
remote push, merge, provider setting change or live deployment occurred. The explicitly
authorized Dependabot security-update PR setting was enabled and read back on all three
repositories; all other remote settings remain unchanged. This report supersedes
manual-only qualification/feature-push details in earlier safeguard reports while
preserving their production custody boundary.

## Verified state and workflow inventory

Requested operating model: private GitHub Free. Actual readback: **public**, default
branch main, authenticated owner admin/write, no registered self-hosted runners.
The account plan is unavailable, main has no active classic protection/rulesets and
production environments have no required reviewers. The remote Actions default token
is write and PR-approval permission is enabled; each prepared workflow overrides to
least privilege. No supported paid/private protection was assumed.

Existing local workflow count: **13**; final local count:
**14**. Before remotely installed count:
**4**. Added one reusable
static-review workflow; no old workflow was deleted before remote replacement success.
Robot retains independent frontend/backend image and ROS verification; the matrix application input is passed through an allowlisted environment value. Automatic credential-bearing production monitoring is replaced by manual staging-only monitoring.

`PIPELINE_AUDIT_BEFORE.json` contains every original trigger, runner, permission,
secret **name**, command, target/environment and sampled history. `PIPELINE_AUDIT_AFTER.json`
contains the exact final local configuration including all job commands. These are
sanitized inventories, not secret values. Detailed owner-only usage records live under
ignored `docs/internal/release-audit/cicd/`; they must not be published as app docs.

Sample: last 100 remote runs across workflows, not the whole history. Completed/failure
counts and mean elapsed seconds include historical configuration; elapsed is not
billed runner minutes. Empty entries are not proof a workflow is broken or unused.

| Workflow | Final triggers | Runner | Before sample: completed / failed / mean seconds | Purpose / usefulness |
| --- | --- | --- | --- | --- |
| `_pipeline-review.yml` | workflow_call | ubuntu-latest | No runs in sample | Reusable blocking offline workflow/secret scan, local annotations and pipeline regressions |
| `deploy.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Retained manual native-protection deployment; private production jobs excluded; no push trigger |
| `free-authorization.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Manual owner approval/revocation binding exact evidence digest; no deployment or provider keys |
| `free-qualification.yml` | workflow_dispatch, workflow_run | ubuntu-latest | No runs in sample | Automatically derive exact-commit CI/build/staging evidence; owner dispatch fallback; no provider keys |
| `release-build.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Retained qualified production packaging; credentials/build excluded on private-Free |
| `release-candidate.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Manual Robot candidate package; no physical distribution |
| `release-qualification.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Retained supported-native-protection qualification path; manual only; private-Free uses free-qualification |
| `release-review.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Retained manual native-protection owner review; no independent reviewer invented |
| `release-staging.yml` | workflow_dispatch, workflow_run | ubuntu-latest | No runs in sample | Single scoped Actions staging authority; automatic mode private-only, owner-only and opt-in |
| `repository-hygiene.yml` | push, pull_request, workflow_dispatch | ubuntu-latest | 41 / 6 / 18.4 | Independent publication/history hygiene gate; all relevant PRs and release-source pushes |
| `robot-edge-release.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Separately reviewed ROS/edge package; licensing/distribution authorization and exact checks; no actuators |
| `robot-monitor.yml` | workflow_dispatch | ubuntu-latest | No runs in sample | Manual owner staging-only live verification; production monitoring uses external owner credentials |
| `scaffold.yml` | push, pull_request, workflow_dispatch | reusable, ubuntu-latest | 41 / 8 / 95.0 | Independent Robot frontend/backend, image startup/security and isolated ROS interfaces |

## Shared implementation status

| Repository | Frontend CI | Backend CI | Automated review | Staging | Qualification | Production release | Monitoring | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Company Portal | Next.js lint/types/build/browser/contracts | Node tests/DB/contracts/image/startup | Read-only annotations + blocking scans | Scoped manual + opt-in auto; not deployed | Exact technical artifact + owner reviews | External owner handoff; blocked pending controls | Receipt/probes configured; live not run | Local implementation; owner activation pending |
| AI Platform | Next.js lint/types/build/browser/docs/TLS/contracts | Python tests/DB/pgvector/API/image/startup | Read-only annotations + blocking scans | Scoped manual + opt-in auto; not deployed | Exact technical artifact; hosted model/operations required | External owner handoff; blocked pending controls | Receipt/probes configured; live not run | Local implementation; owner activation pending |
| Robot | Static interface/proxy/types/browser/contracts | Cloud API/simulation/safety/image/startup | Read-only annotations + blocking scans | Scoped manual + opt-in auto; not deployed | Cloud-only technical artifact; hardware excluded | External owner handoff; physical separate | Manual staging monitor + receipt; live not run | Local implementation; durable storage/owner pending |


## Usage, runners and measured efficiency

Three successful historical CI runs yielded rounded job-runtime slot sums of
**6, 6, 6 minutes** (mean
**6.0**). Heavy jobs in the newest sample: frontend 160s, containers (backend) 35s, containers (frontend) 25s.
These historical public-repository timings do not measure the new local pipeline,
GitHub private billing or the stopped local VM. A transparent planning estimate is
`monthly full-run equivalents × 6.0` minutes, plus hygiene/staging/security and
actual scheduling overhead. For example 20 full-run equivalents would yield
**120.0 historical CI slot minutes**, before added review/summary overhead and
before savings; this is a scenario, not a forecast of actual monthly usage. There is
no verified private-month usage or monetary estimate because the billing endpoint is
unavailable to the token. Do not extend OAuth permissions automatically.

Observed unexpired artifact bytes: **0**,
0 total artifact records (complete readback).
Cache bytes: **0**,
0 active caches. These byte counts are
API snapshots, not billable-storage accounting. Portal's historical artifact bytes
already exceed the hypothetical private-Free 500 MB allowance; review retention
before migration, without deleting evidence blindly.

Current published Free limits: 2,000 monthly Actions minutes and 500 MB artifact
storage per owner/account allowance, 10 GB cache per repository. Do not multiply the
owner's minute allowance by three repositories. Keep new review/diagnostic artifacts
three days and release receipts seven days; old large artifacts are unchanged. Use
scheduled existing scans, on-demand expensive packaging, PR cancellation and measured
usage alerts/budget-stop settings; ordinary automatic version PRs are disabled. Removing feature push duplication saves one full
CI invocation per feature push that also produces a PR run; numeric time/percentage
savings for the new pipeline require authorized remote runs and are not claimed.

No repository runner is registered. The existing local Lima VM `onesa-portal-ci` is
stopped (ARM64, four CPUs, 6 GiB RAM, 60 GiB disk). Its persistent shared Docker and
lease state are not verified as an ephemeral untrusted-PR sandbox. It was not started,
registered or exposed. Hosted Ubuntu is the safe current execution path. Before any
future trusted-only local use: independently isolate identities/networks/storage,
clean workspace and caches between jobs, deny production credentials and untrusted
PR execution, enforce lease/resource limits, and provide explicit hosted fallback
rather than queueing critical checks indefinitely on an offline runner.

## Current checks, findings and credential gaps

Pipeline release regression suites and real-tool negative tests run locally; exact
final counts and application validation are recorded in the final commit report.
All workflow syntax checks pass. Zizmor has **zero blocking** findings and
**4 Low advisory** findings, with the two documented trigger exceptions.
Gitleaks has zero unresolved publishable-source matches. Existing full application
browser/DB/image/ROS suites remain required in CI; they are not claimed as freshly
executed live provider acceptance by these local tests. Platform local non-DB tests
skip database cases without an isolated test DB; that is not complete DB qualification.

Missing staging Actions secret names from remote inventory:

- `STAGING_RENDER_API_KEY`
- `STAGING_RENDER_SERVICE_ID`
- `STAGING_ROBOT_EDGE_TOKEN`
- `STAGING_ROBOT_TEST_PASSWORD`
- `STAGING_VERCEL_AUTOMATION_BYPASS_SECRET`
- `STAGING_VERCEL_ORG_ID`
- `STAGING_VERCEL_PROJECT_ID`
- `STAGING_VERCEL_TOKEN`

Names are not values. Existing production/shared repository secrets still require
owner migration/rotation. Provider credentials are unavailable in this process/local
environment files, so fresh authenticated project/service configuration and token
scope readback could not be completed. Existing provider settings, free service limits,
real alert delivery, hosted inference and durable Robot storage remain owner checks.
**No staging or production deployment/monitoring was executed.** Synthetic fixture
passes and local builds do not change that status.

## Deployment architecture and trust boundary

```mermaid
flowchart TD
  Dev[dev / normal reviewed changes] --> PR[Owner-reviewed dev to staging PR]
  PR --> CI[Credential-free hosted CI / security / contracts / images]
  CI --> Stage[Exact staged candidate and full source checks]
  Stage --> Hosted[Protected native Git Preview and exact Render staging deploy]
  Hosted --> Receipt[Authenticated simulation / all-browser acceptance / source receipts]
  Receipt --> Promote[Reviewed staging to main promotion]
  Promote --> Main[Full exact-main CI and tested runtime digests]
  Main --> Q[Commit/artifact-bound qualification with matching staging source]
  Q --> A[Current owner authorization / exact evidence digest]
  A --> X[External owner-held credentials / trusted verifier]
  X --> Live[Qualified production handoff / hosted verification / rollback evidence]
  Bot[Temporary Dependabot security PR / default-main proposal] --> Review[Review advisory / dependency scope / provenance]
  Review --> Dev
  Review -. necessary urgent exception .-> Temp[Temporary candidate from exact verified production]
  Temp --> TCI[Credential-free candidate validation]
  TCI -. separately qualified isolated staging path required .-> Receipt
  Live --> Reconcile[Reconcile security fix into dev and staging]
  Reconcile --> Cleanup[Temporary cleanup after incorporation / retained evidence / active review checks]
  Main --> Edge[Separately reviewed ROS simulation / edge package]
  Edge --> Hardware[Physical hardware stays disabled / separate authorization]
```

These are workflow gates and an operating policy, not private-Free server-side branch
protection. GitHub Free cannot prevent a write-authorized user pushing directly to
private `main`. Such a push still runs full CI but is ineligible for production
qualification without an owner-merged PR. No independent reviewer is available;
automated annotations are CI evidence, never a second person's approval.

GitHub enforces token permissions, job dependencies/conditions, artifact integrity
metadata and workflow results for the unchanged configuration. Writers can replace
that configuration. Repository/environment labels and owner checks do not prevent a
writer changing a workflow to read repository secrets. Restrict write access to the
owner; keep production credentials in owner-controlled external custody; use separate
staging accounts/projects and identities. Stage secrets remain exposed to authorized
editable repository code, so isolation and provider scopes are essential.

External controls must disable Render auto-deploy/hooks and competing Vercel Git
Preview/production deployments, restrict production identity access and verify the
qualified revision/domain alias after promotion. Local `render-free-staging.yaml` and
Vercel configuration are prepared controls, not proof of deployed provider settings.
PR discipline, owner review, legal/content review and credential custody remain team
responsibilities. Existing remote workflows remain active until the owner authorizes
publishing these branches and merging the reviewed PRs.

## Automated review and dependency policy

Reviewdog v0.21.2 consumes existing ESLint, Ruff, mypy and TypeScript output without
rerunning the analyzer. It emits `github-annotations` to the Actions log with a
read-only token; no PR comment, Checks write token, reviewdog server or GitHub App is
required. Analyzer exit codes independently fail CI even if annotation succeeds.
Valid available PR base commits filter annotations to changed context; otherwise all
findings remain visible. Robot's existing JavaScript syntax checks stay in place;
it is a static interface/proxy, not a React application. Its actual directory is
`onesa-ai-robot-frontend` (package name `onesa-robot-interface`). Portal and Platform
frontends are Next.js; Portal backend is Node/TypeScript, the others Python/FastAPI.

Pinned official standalone binaries use archive **and binary** SHA256 locks checked
before execution on Linux AMD64 and local macOS ARM64. Reviewdog, Zizmor, Gitleaks and
actionlint have MIT licenses. No project license was introduced. Official Actions
retain immutable commit pins. The reusable security job is credential-free and uses
no `pull_request_target`, elevated PR token or analysis service.

| Tool | Role | Gate / limits |
| --- | --- | --- |
| Reviewdog | Read-only visible lint/type annotations | Original analyzer failure blocks; annotations are not approval |
| ESLint / Ruff / mypy / TypeScript | Existing applicable static checks | Fail independently; Ruff formatting and existing tests retained |
| actionlint 1.7.12 | Workflow schema/expression validation | Blocking; shellcheck disabled in this job, not claimed as a shell audit |
| Zizmor 1.30.1 | Offline Actions and Dependabot security | Medium/High/Critical blocking; Low advisory; reviewed trigger exceptions below |
| Gitleaks 8.30.1 | Published source secret detection | Blocking; ignored private data excluded, source symlinks rejected; matches redacted |
| Trivy 0.75.0, existing immutable image pin | Runtime image vulnerabilities and secrets | Existing HIGH/CRITICAL and secret gates retained, including unfixed vendor findings |
| npm audit / pip-audit | Existing package advisory checks | Existing severity thresholds retained; registry advisory queries do not upload source |
| Semgrep CE | Evaluated local scanning and telemetry controls | Not enabled in CI: no benchmarked project rule set yet; no hosted service/login/registry rules |

Semgrep CE is LGPL-2.1 per official repository metadata. A future evaluated local scan
must use owner-reviewed local rules, `semgrep scan --metrics=off --config=<local-rule>`
and no login/`semgrep ci`/hosted rule fetch. This audit evaluated its documented
capability; it did not run Semgrep or claim Semgrep coverage. The existing analyzers,
Trivy, secret/workflow checks remain the active baseline, avoiding an unmeasured extra
heavy scan on each PR. No external AI review bot was installed or source uploaded.

Copilot entitlement could not be read with the existing token. GitHub's documentation
says Copilot Free does not include repository Copilot Code Review; no paid capability
is assumed or required. An optional review of the diff through the user's already
authorized Codex session remains nonblocking; no Codex API subscription or runner
integration was added, and no CLI capability was claimed as verified.

Dependabot alerts are enabled, with **zero open alerts** in the readback. Automated
security-fix PRs were **disabled** in the before inventory and are now **enabled** on
all three remotes, verified by readback after the explicitly requested setting change. The 2026-10-11 preparation proposed monthly ecosystem updates, up to two version
PRs per ecosystem, seven-day version cooldown and compatible minor/patch groups.
That dated proposal is historical: current ordinary version PR limits are zero and
retained version targets are dev; temporary security PRs remain separately authorized. Security updates are ungrouped
and have no version cooldown. Security-fix preparation follows the authorized remote
setting. The old version-PR activation proposal is superseded: retained schedules with
limit zero do not create ordinary version PRs. Security bot PRs run the same read-only CI and
never automatically merge or deploy.

## Optimization and branch policy

Keep dev, staging and main permanently and normally integrate dev → staging → main.
The authorized temporary Dependabot security PR/candidate exception is described in
[the current procedure](../engineering/CI_CD.md#security-pr-integration-and-urgent-candidates).
Default-main bot proposals are reviewed and normally integrated through dev; necessary
urgent candidates start from exact production and require separately qualified isolated
staging/source evidence. Ordinary version PR limits are zero. Existing historical
feature/audit branches and preparation diagrams are not a permanent branch policy.
PR activity uses credential-free hosted checks; full permanent-branch/explicit trusted
checks and all release protections remain. No native Git push restriction is asserted.

Superseded PR runs cancel; release-source runs and deployments do not cancel in
flight. npm download and pip caches key from committed lockfiles; no credentials,
private databases, models or deploy artifacts enter caches. `npm ci` and Python
`--require-hashes` remain the install policy. Platform Makefiles now also require
hashes. Deployment CLI's exact Vercel version remains pinned, but its global npm
install has no transitive lock; this is explicitly a Low advisory, not a hidden PASS.

The path classifier defaults to full CI on missing/invalid bases, failed diffs and
unknown/shared source. CI, infrastructure, contracts, scripts, auth, API routes,
middleware, security, migrations and configuration trigger all affected checks.
Recognized documentation PRs avoid application builds; static security and publication
hygiene still run, and Portal documentation validation runs in the security job.
Platform docs retain their actual browser/access checks. Recognized frontend-only PRs
retain browser/build/container checks. All release-source pushes run every critical
check regardless of paths and produce both runtime records.

The always-running `ci-summary` rejects missing classifications, failed/cancelled
jobs and unexpected skips. Release evidence additionally requires the actual named
critical jobs, including `pipeline-security / workflow-security`, and runtime artifact
digests. Verify that reusable job name in the first authorized remote run; a mismatch
blocks release rather than falling back to an absent check. Product-specific checks
remain independent; there is no new central repository or external dependency between
products. Dormant native/local workflows are retained until replacements have run
successfully remotely. Workflow count reduction is not claimed as a savings measure.

## Automatic qualification and intentional production authorization

`RELEASE_QUALIFICATION_JSON` is retired from deployment inputs. CI derives exact
repository/SHA/source-hash/runtime-image-digests, successful run IDs, authenticated
staging checks, provider revision/URL receipts and security state into JSON artifacts.
It verifies artifact archive digests and origin, parses bounded JSON only, never runs
artifact code, rejects another commit/fork/re-run, and rechecks current main and owner
PR provenance. JSON containing arbitrary PASS values is not authorization.

Automatic staging is off until `STAGING_AUTOMATION_ENABLED=true` is intentionally set.
It only accepts successful owner-triggered **push-to-main** CI from the same repository
and current main SHA, with repository visibility private. Portal/Platform additionally
require their established staging branch to point to that same SHA; no branch is moved
automatically. Before the first provider credential is exposed to a step, the
credential-free preflight checks actual CI/security/image records. Parallel hygiene
may still be incomplete at the trigger; preflight fails closed and the owner can
start a fresh manual staging dispatch after all checks finish. No unlimited retry.
This is an explicit safety tradeoff, not a guarantee of automatic success.

After the staging workflow succeeds for current main, `free-qualification.yml` derives
technical qualification automatically; owner manual dispatch is retained as fallback.
It receives no provider secrets. CODE_VERIFIED and STAGING_VERIFIED pass only from
actual verified results. Technical readiness evidence is recorded automatically;
OPERATIONS_VERIFIED stays BLOCKED while alert delivery, off-host restore/rollback or
operating limits have not been demonstrated. CONTENT_LEGAL_APPROVED always remains
BLOCKED in technical evidence. The audit cannot fabricate those missing results to
satisfy an automation label. Hosted AI inference is not approved by local mock/Ollama.

The owner initiates `free-authorization.yml` on main, chooses **approve** (default
**revoke**), supplies the exact qualification run/digest and an open owner-authored
issue binding SHA/digest to OPERATIONS_APPROVED, CONTENT_LEGAL_APPROVED and
EXTERNAL_PRODUCTION_CONTROLS_VERIFIED. The authorization artifact records operator,
time, exact candidate, source digest, target Production and review issue digest.
Latest pending/failed/revoked authorization, issue changes/closure, stale evidence,
main movement, failed CI or mismatched artifacts invalidate previous authorization.
This is solo-owner authorization, not two-person approval.

Run `scripts/release/free_gate.py verify` from an independently owner-reviewed,
immutable clean checkout outside repository Actions with owner GitHub authentication.
It returns AUTHORIZED_EXTERNAL_HANDOFF only after live provenance/digest checks; it
never deploys. Production credentials must never be supplied to that editable CI.
Private credential-bearing native production build/deploy/repair/inspection/docs jobs
remain excluded. Production promotion still requires a separately authorized external
provider executor and post-deploy receipt. This task did not create or provision a
trusted external executor and does not claim a complete live private-Free production
service. The retained native protected-main path is only usable where support and
protection are verified; it is not a fallback to bypass a private-Free gate.

## Reviewed findings and workflow-run exception

Zizmor's generic HIGH `dangerous-triggers` rule is locally annotated only on the two
reviewed controllers `release-staging.yml` and `free-qualification.yml`. No HIGH or
CRITICAL application/image finding is suppressed. These trigger exceptions have
specific evidence: fixed owner/repository checks, owner-triggering actor, upstream
push/main/success and exact current SHA, automatic staging default-off/private-only,
full CI/image preflight before staging-only credentials, no production credentials,
no artifact execution, and credential-free qualification. Regression tests reject
foreign owners, forks, wrong SHAs/events/failures and changed branch pointers, and
check the opt-in/triggers/credential ordering. The staged source checkout is the exact
current main commit, not an upstream artifact. Re-runs are rejected.

The generic trigger finding was reviewed as not reaching the standard privileged
untrusted-PR/artifact attack path under these constraints. It is still a trust-sensitive
exception, **not** proof against a repository writer changing the controller or
stealing scoped staging credentials. Do not remove provenance checks or put production
keys into these workflows. The old native qualification trigger and Robot production
monitor trigger were made manual; Robot monitor now uses staging keys only. Remaining
Low advisories include the exact-version global Vercel install and owner-conditioned
self-repository comparison. Their presence is disclosed below.

## Monitoring, model and Robot limits

Staging deployment helpers poll actual completion with bounded attempts, check provider
identity/revision, authenticated readiness, real API workflows and frontend/backend
integration and retain sanitized receipts. No deployment was triggered during this
audit. Full runtime logs/error-rate/alert delivery and provider identity readback need
the separately scoped provider credentials, which are unavailable locally and absent
from repository staging secret inventories. No hosted/model/sensor/actuator PASS is
inferred from a synthetic fixture, local successful build or process liveness.

Platform acceptance keeps real-hosted streaming/cancellation/context/embeddings/vector
storage/knowledge/agent/usage-limit/recovery evidence separate from mock/local-model
tests. Robot frontend and cloud backend have separate images, CI and Render/Vercel
staging identities. ROS interfaces use an isolated pinned Jazzy container; simulation,
edge packages/operator distribution and physical robot deployment remain separate.
A passing simulator or message contract does not qualify physical hardware. Persistent
Robot SQLite/cloud storage still requires a verified durable approved target; do not
claim a free Render web service provides a persistent disk or silently provision one.

For rollback, retain previous exact provider deployment IDs/image digests and Vercel
aliases privately. Revalidate health before switching to a known compatible revision;
match application and schema compatibility, preserve databases/volumes/model assets,
and obtain separate approval for destructive or incompatible migrations. Do not
reverse migrations automatically or run an open-ended repair loop. Existing local
rollback baselines remain unchanged. No continuous observation after this session is
promised; owner-configured alert delivery remains a production prerequisite.

## Owner activation actions

1. Resolve the discrepancy: all remote repositories are public, while the requested
   policy is private/Free. Confirm subscription and protection capabilities in billing
   and repository settings. No visibility change, upgrade or paid capacity is authorized.
2. Review and authorize publishing the reviewed dev candidate and opening dev-to-staging PRs; merge only
   after the actual new remote CI is green. Reconcile remote job names with evidence
   policy; preserve all critical gates and validate with a failing synthetic PR.
3. Set default Actions token read-only and disable Actions PR approval where possible;
   restrict write access to the owner. Dependabot automatic security-fix PRs are
   already enabled and verified; the other settings were audited but not changed.
4. Move/rotate existing production repository secrets into external owner custody and
   remove them from editable repository Actions after confirming the replacement.
   GitHub repository secrets are Settings > Secrets and variables > Actions; a private
   Free environment label does not add an unavailable required-reviewer barrier.
5. Create separately scoped staging provider identities/accounts/projects, then add
   the STAGING_* names listed below as repository Actions secrets and nonsensitive
   target metadata as variables. Tokens come from Render Account Settings/API Keys
   and Vercel Account Settings/Tokens; project/service identifiers come from the actual
   staging resources. Never paste tokens into issues or chat. Provider application/DB
   runtime secrets belong in that provider's private environment, not browser variables.
6. Read back actual Vercel project/root/backend target/Preview protection/domain and
   Render repo/root/build/start/Dockerfile/branch/health/DB/storage/workers/auto-deploy.
   Disable competing Git auto-deploy/hooks in both staging and production with owner
   authorization. Do not enable the staging switch based only on files in Git.
7. Perform the authorized manual isolated staging run first, verify receipts/probes
   and both frontend/backend revisions, align preserved staging branch with main by
   reviewed PR/merge policy, then intentionally opt in to automatic staging if desired.
   Repeat monitoring/negative checks on staging, never destructive production tests.
8. Demonstrate missing operational evidence, complete legal/content review, arrange
   a trusted external production executor with owner-held scoped credentials and a
   verified rollback/receipt plan, then issue exact-digest manual authorization.
   Robot physical deployment has its own operator/safety/licensing authorization.
9. Check owner-level Actions billing minutes/storage, enable budget stop controls and
   usage alerts without enabling paid execution. Review oversized historical artifacts
   before any authorized deletion; no artifacts/caches were deleted here.

## Sources

- [Reviewdog reporters and integration](https://github.com/reviewdog/reviewdog)
- [Zizmor offline usage](https://docs.zizmor.sh/usage/)
- [GitHub included usage](https://docs.github.com/en/billing/reference/product-usage-included)
- [GitHub Actions limits](https://docs.github.com/en/actions/reference/limits)
- [Dependabot options and version-only cooldown](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference)
- [Copilot Code Review availability](https://docs.github.com/en/copilot/concepts/agents/code-review)
- [Semgrep metrics and local rule usage](https://docs.semgrep.dev/metrics)

## Final local validation and exact changed files

`PIPELINE_VALIDATION.json` records command results and sanitized diagnostic summaries.
Release regression tests: 38 passed, 7 native-protection-specific skips; pipeline real-tool negative tests: 17 passed.
Workflow syntax/security and history/publication hygiene pass. No provider call occurs
in the failed-CI handoff demonstration; this does not prevent the underlying Git push.

Application checks: backend Ruff/mypy and 25 API/simulation tests passed; frontend syntax/types/build and its proxy test passed; generated contract checks passed. The frontend proxy test required authorized loopback socket access after the sandbox denied binding.

The following files are changed in this local implementation. Ignored restricted
owner evidence is excluded. All changes are on feature/cicd-review-optimization;
commit IDs are reported in the task response; the commits also include the preceding
private-Free safeguards through their branch ancestry. No branch was pushed or PR opened.

- `.github/dependabot.yml`
- `.github/workflows/_pipeline-review.yml`
- `.github/workflows/deploy.yml`
- `.github/workflows/free-authorization.yml`
- `.github/workflows/free-qualification.yml`
- `.github/workflows/release-build.yml`
- `.github/workflows/release-candidate.yml`
- `.github/workflows/release-qualification.yml`
- `.github/workflows/release-review.yml`
- `.github/workflows/release-staging.yml`
- `.github/workflows/repository-hygiene.yml`
- `.github/workflows/robot-edge-release.yml`
- `.github/workflows/robot-monitor.yml`
- `.github/workflows/scaffold.yml`
- `AGENTS.md`
- `README.md`
- `docs/development/CHANGELOG.md`
- `docs/development/CI_CD_PIPELINE_REPORT.md`
- `docs/development/FREE_GITHUB_PLAN.md`
- `docs/development/PIPELINE_AUDIT_AFTER.json`
- `docs/development/PIPELINE_AUDIT_BEFORE.json`
- `docs/development/PIPELINE_VALIDATION.json`
- `docs/development/PRIVATE_FREE_SAFEGUARDS.md`
- `docs/engineering/BUILD_AND_TEST.md`
- `docs/engineering/CI_CD.md`
- `scripts/ci/analyze.py`
- `scripts/ci/install_tools.py`
- `scripts/ci/paths.py`
- `scripts/ci/security.py`
- `scripts/ci/summary.py`
- `scripts/ci/test_pipeline.py`
- `scripts/ci/tools.lock.json`
- `scripts/release/evidence.py`
- `scripts/release/free_gate.py`
- `scripts/release/monitor.py`
- `scripts/release/test_free_gate.py`
- `scripts/release/test_free_workflow_policy.py`
