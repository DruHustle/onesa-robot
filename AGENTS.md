# Onesa engineering and agent policies

Authoritative agent/contribution/security policy for this independent repository.
Read README, release checklist and relevant code/runbooks before edits. Continue
incomplete work; preserve concurrent changes, history, data and credentials. Stage
only reviewed paths. Update contracts/docs/changelog with behavior changes; report
unverified scope honestly. User instructions/authorization take precedence. Use free
resources; paid services require explicit budget approval. Do not recreate root
setup/status/handoff checklists or first-party SECURITY.md/CONTRIBUTING.md.

## Contribution rules

Keep this repository independent within `onesa-ug`: retain component directories,
separate releases and no workspace Git repository or sibling submodules.

Keep exactly three permanent branches: `dev`, `staging`, `main`. Normal integration
is `dev → staging → main`. Temporary Dependabot security-update PR branches and
necessary narrowly scoped security-release candidates are authorized exceptions,
not permanent branch families. Ordinary version-update PRs remain disabled. See the
security integration procedure in `docs/engineering/CI_CD.md` before using an exception.
Preserve work already staged or modified locally. The verified team currently has one
developer: require PRs and automated checks, with zero independent approvals until
an authorized reviewer exists. Then require one independent review; seek two for
authentication, personal data, infrastructure or robot actuation when available.
Describe the problem, resulting behavior, validation and deployment/rollback impact.
Update API contracts, migrations and documentation with behavior changes.

Before staging run BUILD_AND_TEST and `python3 scripts/repository_hygiene.py`;
inspect exact cached diff/status. Synthetic fixtures only; never stage private data,
models, uploads, backups or secrets. Keep examples placeholder-only and dependencies
locked with provenance/license review. Do not assign a project license without approval.
Shared contracts must support independent rollouts. Pushes, production promotion and
visibility changes require current authorization; inspect actual remote protection.

Frontend-only release uses the existing owner-selected component mode and independently
derived immutable retained-backend proof. Keep exact-main CI, fresh staging,
qualification and owner authorization. Record actual component SHAs, not a false
repository SHA. No caller PASS, weakened expiry or chained retained proofs. Never
redeploy unchanged backend for CSS; keep runtime digests restricted. Read DEPLOYMENT.

Detailed deployment/recovery documents in public Git are safe reference pages.
Read the protected canonical or approved local engineering source bundle before
operations; preserve its original hashes/revisions. Do not copy full runbooks back
into public sources or give private bundles to PR jobs. Historical Git exposure
is not removed by active-source cleanup.

## Security policy

Supported software: the latest reviewed revision of `main`; no historical release
support promise is made before a release/support policy is approved.

Report privately through verified GitHub private reporting or info@onesa.eu; never
post sensitive exploits/records publicly or invent a mailbox, bounty or response SLA.
Keep credentials, private records, weights, backups and uploads out of Git/CI/docs;
use restricted storage, least-privilege identities and protected environments. Separate
browser-public configuration from backend secrets; ignore private .env files.
If credentials are committed/exposed, notify the owner and use scoped authorized
revocation/rotation with access-log assessment. Untracking is not revocation or history
removal. Historical rewrite requires separate coordinated approval; never force-push.

`python3 scripts/repository_hygiene.py --history` checks reachable history,
index and publishable files without printing values; it cannot prove confidentiality.
Preserve available secret scanning/push protection and inspect classifications.
Confidential history blocks publication pending actual owner remediation authority.

## Repository governance and release trust

Use credential-free disposable GitHub-hosted runners for PRs. Local/self-hosted runner implementation is removed. Use included hosted capacity only;
never run untrusted PR code there or provide production credentials to PR jobs.
Production credentials stay in external owner-controlled custody, outside editable
Actions. Restrict repository write access: writers can alter workflows/secrets.
Do not change visibility, ownership, billing or access without authorization.

Inspect current remote rules and plan capabilities before claiming enforcement.
Historical private-Free protection failures do not establish current visibility or
eligibility. Native private-Free branch/environment protection is unavailable;
written PR discipline alone cannot stop privileged writers. Never invent reviewers
or drop failed checks. Use the actual solo-owner review policy above. Read
`docs/development/PRIVATE_FREE_SAFEGUARDS.md`, `FREE_GITHUB_PLAN.md`,
`BRANCH_PROTECTION_REPORT.md` and `CI_CD_PIPELINE_REPORT.md` before provider/release work.

Use reviewed dev PRs to staging, then qualified staging PRs to main. Retain exact-main
CI/security/ROS contract checks, source-bound hosted staging, owner authorization,
external production promotion and separate hardware authorization. Reviewdog is CI
evidence, not independent human review. Keep automatic staging private-only,
opt-in and default-off pending scoped targets. Provider settings and preview protection
require actual readback. Prior acceptance branches remain restricted recovery bundles;
do not recreate them or introduce an unqualified direct-main/native deployment path.
Emergency exceptions require their actual temporary authority/evidence, never an
invented PASS. No local runner registration or protection activation is implied.

## Product, appearance and Code Review Rules

The public product at `/` and development privacy at `/privacy` are script-free
and never read operator data. `/console`
loads only the authenticated simulator API; menus and hidden markup are not access
controls. Preserve the configured operator and existing credentials in legacy mode, device
bearer isolation, object ownership, expiring proposals, session/heartbeat revocation,
independent software motion gate. The owner has requested session-bound administrator
allow/block permission for future physical controllers; hardware is still in development.
Keep that permission separate from controller connection, readiness and actual actuation.
The owner has authorized one shared Onesa identity and cross-product administrators.
When explicitly enabled, only the confidential identity service may assign Robot
viewer/operator/admin access or the MFA-protected global administrator role; bind
ownership to issuer and subject, never automatically merge accounts by email.
Recheck live grants, session, Origin/CSRF and revision on the backend. Shared-identity
operations require recent central MFA and explicit review confirmation. Legacy mode
retains the designated existing operator and password confirmation. Identity changes
do not enable physical controllers, migrate journal ownership or reset credentials.
Block/Stop, logout, expiry and restart revoke permission. Permission alone must never
send movement or claim connected hardware. Future physical activation needs its actual
controller integration and independently qualified hardware safety/stop behavior.
Idle-only renewal transport recovery must use new database-fenced ownership, exact
terminal-history preservation and revoked sessions/permission. Preserve refusal for
active/armed/pending/dirty work, non-transient errors and exhausted attempt budgets.
It never resumes commands or restarts a process.
No physical movement test, public DDS or automatic task replay is authorized. Real model/ROS/
hardware capability must never be presented as a synthetic fallback.

Follow the versioned Onesa design contract in `docs/engineering/design-system.consumer.json`
and its protected guide. Shared foundations preserve Robot's product identity and
operational hierarchy. Appearance follows `prefers-color-scheme` through CSS; no
clock schedule, selector, appearance cookie or stored override. Mode changes must
preserve focus, drafts, authentication and simulation execution. Environment labels
come from trusted runtime configuration; simulation/live labels come from actual data.

Code review prioritizes authorization/object access, stop/freshness/replay behavior,
private telemetry, task cancellation, storage/lease integrity, secret handling,
contract compatibility and release provenance. Distinguish a demonstrated regression
from a hypothesis; avoid duplicate style remarks. Use parameterized SQL, bounded
validation/timeouts/retries, UTC events, minimal redacted logs and locked dependencies.
Never weaken a failed mandatory check, hand-edit generated contracts, upload code to
an unapproved reviewer or assert independent approval of your own implementation.

## Local command matrix and definition of done

| Task / directory | Command | Prerequisites, environment and effect |
| --- | --- | --- |
| Frontend install / frontend | `npm ci --ignore-scripts` | Node 24; local locked dependency installation |
| Frontend verification / frontend | `npm run lint && npm run build && npm run typecheck && npm test` | Installed locks; local generated client/build; unit tests bind loopback |
| Backend install / backend | `python3.11 -m venv .venv && .venv/bin/python -m pip install --require-hashes -r requirements-dev.lock` | Python 3.11; isolated local environment, no provider credentials |
| Backend verification / backend | `.venv/bin/python -m ruff check robot_api tests && .venv/bin/python -m ruff format --check robot_api tests && .venv/bin/python -m mypy robot_api && .venv/bin/python -m pytest -q` | Backend venv; isolated synthetic fixtures only |
| Contract / root | `python scripts/generate-contracts.py && python scripts/check-contracts.py` | Active backend venv and Node 24; source-generated schema/types |
| Appearance / root | `node --test scripts/test_system_appearance.mjs` | Node 24; offline shared digest/system-preference/public isolation checks |
| Repository docs / root | `python3 scripts/check_repository_docs.py` | Offline root policies, references and true release-baseline checks |
| Browser / frontend | `npm run test:e2e` | Isolated simulator and three installed browsers; synthetic account, no live hardware |
| Hygiene / root | `python3 scripts/repository_hygiene.py` | Read-only source/index scan; private values are never printed |

The backend README defines secure local environment startup. The root README and
onboarding define the smallest local path; provider/recovery procedures belong in
protected engineering docs. Installed dependencies are not proof of a clean-room
setup. Record the actual environment/result for every check, including missing
browser/device/resource blockers. Deployment is not a safe local test default.

Before risky work read DEPLOYMENT, AUTOMATED_QUALIFICATION, SIMULATION_ARCHITECTURE
and the current restricted release evidence. Current user authorization can permit a
qualified release but never proves tests or extends blanket authority to future sessions.
Coordinate branches, schemas and provider writes; retain recovery and existing data.
Done means reviewed source, safe cleanup, passing affected checks, accounted evidence
and verified authorized hosted scope. Report the exact genuine blocker and next
executable action. Only verified production changes enter the root CHANGELOG.

Render observer timeouts are not provider failure/cancellation. Follow DEPLOYMENT's
pinned-receipt GET-only resume; preserve the immediate receipt. Never duplicate a
create request, clear cache, cancel or consume qualification just to test monitoring.
Actual source/readiness/hosted acceptance remain independent requirements.

Retain dependency alerts, scans, scheduled CI and owner-confirmed notifications.
Dependabot security fixes target default main; version target-branch cannot redirect them. Keep
PR code credential-free/hosted, with no auto-merge, privileged PR event or production
deploy. Normally integrate fixes through dev/staging/main. Urgent candidates start
from exact production and require isolated source-bound staging/owner release,
then reconciliation. Preserve all guards; delete temporary branches only after
incorporation and evidence/review/rollback-dependency checks. Never bulk-delete bots.

The owner has explicitly deferred the Robot development-processing legal-basis,
retention and existing-provider processing/transfer records until launch. Record
this as a deferral, never a legal PASS or executed contract. The existing manual
Free authorization can bind that decision to one exact qualified development
candidate with `defer_development_privacy`; default is false. Only the external
owner verifier may consume it after rechecking the latest immutable authorization.
Keep operations, exact-source CI/staging, authentication, preservation and physical
safety gates. This cannot authorize a customer launch, new processing/provider,
physical operation or an automatic deployment. See DEPLOYMENT for the scoped path.

GitHub Free is permanent; only the owner changes current public visibility.
Private hosted jobs require explicit included-allowance opt-in; no paid overages.
Unavailable private protections are procedural; passing CI and external owner release
gates remain mandatory. Follow docs/development/FREE_GITHUB_PLAN.md for transition checks.
