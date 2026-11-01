# Independent software builds and tests

The NVIDIA reference-library build verifies all local GLBs, previews, source
records and licenses against their manifest hashes and rejects external or active
GLB resources. `tests/nvidia-assets.test.mjs` exercises actual asset integrity,
altered-package refusal, bounded browser downloads, cancellation and the complete
locally packaged Three.js import graph. Proxy tests check actual bytes/MIME types
and retain unknown-path/traversal denial.
Fixed local reference requests retain same-origin preview cookies and reject
redirects. The protected-preview browser fixture requires a synthetic HttpOnly
cookie at an actual loopback HTTP server, proves that a static 401 does not sign
out the operator,
then requires actual rendering after access is established. It also checks that
a model redirect reaches neither a same-origin target nor a foreign loopback fixture,
without sending external requests or performing control writes. This observes
headers on the wire instead of inspecting a Playwright route before the browser's
network stack attaches cookies; redirects are real HTTP responses in every engine.
In the deliberate-redirect case only, WebKit's complete page-error list must equal
the two exact model-URL refusal diagnostics recorded after the fixture sends each
302. Other engines require no page errors. Extra, duplicate, unrelated or differently
worded errors fail; all wire-target, rendering/recovery and no-write assertions remain.
The sign-in readiness request also retains same-origin preview access. Its fixture
requires that cookie at the loopback HTTP server before one deliberate credential
submission;
existing bounded readiness, no-replay and sign-in input-preservation checks remain.

`tests/browser/nvidia-library.spec.mjs` covers automatic loading, all three
selections, system appearance and keyboard focus, narrow layouts and accessibility,
failed/corrupt downloads, missing/lost graphics, explicit recovery, stale selection
and sign-out cancellation. Source/license/provenance links resolve the active system
foreground directly, with synchronous gallery colors under reduced motion; browser
checks cover link colors after each theme change and accessibility in both modes.
It checks that visual interaction issues no mission or
control mutation and that independent Stop/sign-out remain usable. When WebGL is
unavailable, the explicitly injected missing-graphics test still requires the preview.
Native CI sets `ROBOT_REQUIRE_WEBGL=true` so every successful-load, selection and
recovery flow requires actual rendering in all three engines. It runs the combined
matrix under a disposable 1920×1080×24 Xvfb display and uses headed Firefox because
the stock Linux headless Firefox profile disables WebGL 2. The single report keeps
all engine outcomes, and no test is skipped or retried. The same environment setting
can strengthen an actual graphics-capable supplemental run.
Local supplemental browser versions never substitute for the three pinned CI
engines or fresh source-bound hosted acceptance. No physical hardware is used.

The disposable CI runner's Ubuntu source preparation retains its exact mirror
allowlist, canonical HTTPS archive and signing/suite fields. Rejected sources report
only bounded URI structure; credentials, arbitrary paths and query/fragment values
stay redacted, including malformed Unicode parsing failures. Explicit zero ports
and empty user information are rejected before any source file is rewritten.

Legacy joint and inspection supervision now renews its heartbeat only after both
status and owned history reads succeed and their simulation-only payloads validate.
Failed, malformed, obsolete or interrupted reads cannot keep an unobserved task
alive. The concurrency unit tests cover both read failures, invalid mode/actuation
and task data, session replacement and post-action observation ownership. The
three-engine `observation-supervision.spec.mjs` test starts a real synthetic task
through the local gateway, fails each browser observation route in turn, and
requires the actual backend watchdog to cancel it while heartbeat remains reachable.
Stop and sign-out stay available. These source tests do not establish hosted
acceptance or physical stopping performance.

The contract toolchain independently packages a narrowly scoped callable adapter
for `@redocly/openapi-core@1.34.20`, executing upstream `minimatch@11.0.1` to address
[GHSA-g3hv-75wg-m249](https://github.com/advisories/GHSA-g3hv-75wg-m249).
The exact archive, upstream integrity and license stay inside the frontend build
context. Installed-consumer URL/header matching and contract generation are tested;
the legacy matcher is absent and no broader tooling upgrade is included. Ordinary
build, type, dependency-audit and source-bound release gates remain required.

Independent frontend dependency tests retain separate staging and production API
sources, verify the original accepted signed chain including its authorization
decision, and require exact backend/edge/ROS bytes and modes, current image scans
and unchanged complete runtime identities. Frontend custody binds the selected
frontend source; it cannot authorize or redeploy its retained API. Disposable tests
cover revoked decisions, wrong scopes/stores/artifacts, missing acceptance and
runtime drift. Genuine hosted frontend qualification remains mandatory.


The existing-API observer recovery has disposable signed-ledger regressions for
historical source preservation, revoked ancestors and owner commissions, failed or
incomplete checks, wrong source/artifact/provider identity, expiry, immutable plans,
production password hash confirmation, disabled actuation, concurrent observation
and interruption. Recovery issues one production verification only after the actual
read-only checks; it never creates a deployment or renews an approval. Its reviewed
observer and production password stay outside Actions. Local fixtures establish no
hosted acceptance.


The owner frontend fixture injects a conflicting production default and verifies
explicit Preview selection before staging dispatch. A wrong provider target retains
the one-create intent and refuses acceptance. Actual pinned CLI protocol fixtures
also exercise the explicit staging argument with synthetic loopback-only services.
These controls use no provider credentials and do not establish hosted acceptance.

Installer fixtures also place disposable custody under the private home directory
when executed as root. The explicit writable-parent regression still requires the
production verifier to reject shared writable ancestors; no custody guard changes.

Release-unit fixtures pin a disposable commissioning registry and keep its keys,
policy and database inside their temporary directory. Root test execution uses a
private home-directory parent and never reads the host's production trust registry.
Production trust checks and release authority are unchanged.

Oracle CI selection keeps an existing push authoritative, including failed,
pending, skipped or cancelled evidence. Only an absent push permits the existing
owner-dispatched full workflow; both owner identities, a complete run listing,
first attempt and every mandatory job/security step remain required. Signed
references preserve the actual CI event. Synthetic mixed-event regressions must
reject a newer successful manual run presented over a failing push.

The canonical repository is `DruHustle/onesa-robot`; its existing frontend and
backend CI artifacts retain the `runtime-onesa-ai-robot-` component prefix. The
owner verifier checks these explicit artifact names against the actual workflow
while requiring the canonical repository on both the run and head repository.
Neither a renamed artifact nor an old repository identity can substitute for it.

The disposable frontend job bounds APT requests and removes the observed Azure
mirror from the runner's existing failover list, retaining its existing signed
Ubuntu archive, suite, components and keys. It never runs on the workstation,
owner VM or an unrelated repository; conflicting sources fail closed. The helper
and synthetic custody/source tests derive from reviewed AI Platform source
`2eacf0aff2bfbf8b3e8713a4cc3610c37c2207dd`, with the repository guard changed to
Robot. All three engines remain mandatory; installer failure remains unqualified.

Runtime scans fetch the vulnerability database from Aqua's official GHCR origin,
avoiding an observed pull-through mirror 404. The scanner digest, full vulnerability
and secret scans, complete-report requirement and rejection of high/critical or
secret findings remain unchanged. A database-download failure still fails CI.
See [Trivy database locations](https://trivy.dev/docs/latest/configuration/db/).

Frontend CI uses the supported Ubuntu 24.04 hosted image explicitly. Chromium,
Firefox, WebKit and their system dependencies remain mandatory; the moving latest
OS label is not part of the browser qualification contract. See the official
[Playwright requirements](https://playwright.dev/docs/intro#system-requirements) and
[hosted image inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md).

The offline release suite includes `test_oracle_inventory.py`: completed temporary
monitoring clients and list/inspect races do not become preservation baselines.
Long-lived temporary workloads, unchanged-inventory command failures, incomplete
metadata and real independent runtime changes still fail closed. These fixtures
make only mocked Docker reads and do not establish hosted qualification.

Container health uses the Alpine base image's existing BusyBox `wget` without
starting Python for every request. Proxies are disabled; the readiness route,
15-second interval and deadlines are retained. Run
`python3 scripts/healthcheck_smoke.py <image>` after building: the actual image probe
must handle a custom loopback port, HTTP 503/404, hostile proxy settings, refusal and
timeout in an isolated network-free container. Application, journal and safety
qualification remain separate requirements.

CI and hosted artifact-build workflows pin Node 24.22.1 explicitly; a cached
`24` major selector can otherwise select an older vulnerable patch. Runtime images
and provider-managed runtimes require their own source-bound verification.

`tests/test_numeric_history.py` reproduces one-ULP numeric JSON transport drift,
then checks exact decimal-text/SQLite conversion, adjacent representable values,
unchanged persisted history, idle recovery and stale-owner refusal. Numeric history
projections must reject invalid stored types rather than coerce them into valid
records. No tolerance comparison, schema migration or history repair is involved.

> Current pipeline: [automated review, staging qualification and owner activation](../development/CI_CD_PIPELINE_REPORT.md). Prepared local changes are not live deployments; branch/PR optimization and automatic technical evidence supersede older manual-only details.

Use the [frontend](../../onesa-ai-robot-frontend/README.md) and
[backend](../../onesa-ai-robot-backend/README.md) commands. Node 24 and Python 3.11
are qualified software runtimes. Both applications have independent Dockerfiles,
lockfiles and environment examples; sibling source is never a build dependency.

From the repository root, with the backend venv active, regenerate a deliberately
reviewed contract change with `python scripts/generate-contracts.py`, then from the
frontend directory `npx openapi-typescript contracts/openapi.json -o
contracts/schema.ts`. `python scripts/check-contracts.py` rejects accidental drift;
do not regenerate to erase a breaking change without compatibility review.

CI checks backend security/simulation/persistence, frontend builds/proxy/typed routes,
all three browsers at 320/390/768/1024/1440, dependency advisories, runtime image
vulnerabilities/secrets and publication hygiene. Candidate packaging requires all
exact-revision jobs. Generated runtime data/raw security reports stay private.

Scope: software simulation only. ROS 2 installed edge integration,
model endpoints, actual sensor/actuator behavior, emergency-stop hardware and real
edge-network latency remain NOT TESTED or BLOCKED until their targets exist.
Automated accessibility checks do not certify complete WCAG 2.2 AA compliance.

The owned inspection process-restart test ignores inherited HTTP/SOCKS proxy
configuration for its loopback readiness and authenticated requests. It exercises
that boundary with deliberately unusable proxy settings and cleans up its direct
child if startup fails. Its synthetic credentials never require an external proxy;
normal application/provider TLS and proxy policy are unchanged.

## Bounded sign-in acceptance

After submitting Sign in, browser scenarios use `waitForOperatorSignIn` with a
30-second acknowledgement bound matching the existing application budgets:
20 seconds for readiness followed by a 10-second credential request. The former
25-second observer still assumed an older 15-second readiness budget and could
reject a valid delayed sign-in. A controlled three-engine fixture waits 19 seconds
for readiness and 6.5 seconds for the one login response, requires successful
sign-in with zero control mutations, and clears the masked password afterward.
The owner observer uses the same 30-second sign-in observation budget without a
retry or any new release authority. Ordinary layout, application, simulation,
request and safety deadlines are unchanged.

Reload and late-response regressions observe the existing session/logout HTTP
acknowledgement within the client's existing 10-second request budget before
checking the resulting UI with its unchanged assertion deadline. They issue no
extra request and require HTTP 200 with CSRF or `signed_out` as appropriate.
Offline tests retain failure behavior for missing fields, 401/403/503 and timeout;
cleanup diagnostics expose only HTTP status, never bodies or credentials.

The owner browser probe also observes the single Stop, password-confirmed Reset
and logout request within that same transport budget before its unchanged UI
assertions. It requires the exact same-origin POST, HTTP 200, the prescribed
response state and disabled physical actuation. Per-scenario Stop/Reset receipts
record actual acknowledgement duration without credentials. The three-engine
`owner-acknowledgement.spec.mjs` regression covers a six-second Stop response and
refuses failed HTTP, wrong state, physical activation, missing fields and unknown
operations. The observer submits no retry and grants no release authority.

`tests/browser/sign-in-readiness.spec.mjs` uses only synthetic API responses; the
preview-cookie case observes readiness at its isolated loopback server, while the
timing cases intercept all API requests. It holds readiness unavailable beyond five
seconds, verifies that no
credentials are sent, then permits readiness and requires one login, cleared password
input and zero task/control mutations. Its local three-engine result does not replace
source-bound hosted qualification or authorize retrying a failed context.

Release changes also require `.venv/bin/python -m unittest discover -s scripts/release
-p 'test_*.py'` from the root, `actionlint .github/workflows/*.yml`, and backend
`python -m mypy robot_api` with the hash-locked development requirements. ROS interface
verification uses the pinned official Jazzy image command in `scaffold.yml`, with no
external network. Keep all three browser projects required; local launch failure is
not a passing application test.


Appearance uses `node --test scripts/test_system_appearance.mjs` from the root;
repository documentation uses `python3 scripts/check_repository_docs.py` and
`python3 -m unittest discover -s scripts -p test_repository_docs.py`. The browser
suite tests both system preferences against contrary mocked local hours, public
root isolation, live preference changes with drafts/simulation and accessibility.
`ONESA_VISUAL_EVIDENCE_DIR` may select only a protected directory for synthetic
screenshots; never set it to public assets or include private test credentials.
The shared foundation digest is verified by the frontend build.

Enumerate the current browser matrix with `npm run test:e2e -- --list` in the
frontend. Chromium, Firefox and WebKit remain required, with no skipped/flaky
cases or automatic retries. The pending-form interaction
fixture uses test-owned HTTP responses and no backend mutation; it requires Stop
and Sign out to remain usable while an unrelated form response is pending. Existing
operator, authorization, appearance and accessibility cases remain required. Offline
module tests prove independent Stop/cancel/logout and duplicate originating-form
suppression. Submission scope comes from the synchronous event currentTarget;
focused controls do not determine which independent action may run.

The header appearance regression switches system preference in all three browsers
at 320/390/1440 pixels. It checks the wordmark/navigation foreground and canvas,
measured accessibility, retained sign-in entries and focus, no JavaScript errors
and no mutation. Robot header text resolves the existing system tokens directly,
avoiding the reproduced WebKit stale inherited foreground after a media change.
No shared foundation pin, appearance override, credential or release gate changes.

The idle-administrator regression in `administration.spec.mjs` switches dark,
light and dark at 1440 pixels and checks every permission/controller status leaf
against the current system foreground. It retains password input and focus,
requires blocked physical permission, performs no additional write and runs the
accessibility audit after each switch. These leaves resolve the existing system
token directly to avoid the observed WebKit stale inherited color; authentication,
controller readiness and physical-operation guards are unchanged.

The build backend and development lock pin setuptools 84.1.0 for
[GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w), and the
development lock pins filelock 4.2.1 for its six reported advisories. Official PyPI
wheel/source hashes and MIT provenance were verified for both updates. The isolated
wheel build, full backend tests and both dependency-lock audits must pass before
promotion. Runtime dependency pins are unchanged.

Inspection refresh also preserves the shared form's `aria-busy` lock. The pending
approval regression waits for another status response before requiring the approval
button to remain disabled, one approval request and an independently available Stop.
This reproduces the WebKit failure without changing safety deadlines or adding retries.

Browser cleanup checks for the test context's host-scoped operator cookie before
requesting protected session endpoints. Anonymous public and intercepted fixtures
make no cleanup API calls. An existing session requires bounded session read,
CSRF-protected logout and confirmed revocation; unavailable cleanup fails rather
than silently passing. `node --test tests/session-cleanup.test.mjs` from the
frontend verifies these boundaries without a browser, account or network target.
Failed browser cases now retain bounded request-start, response-header, completion,
transport-failure and document-lifecycle events, plus requests still pending when
failure is reported. This closes the earlier diagnostic gap where a stalled request
could leave an empty timing log. Only known same-origin route labels, masked task
identifiers, methods, statuses and elapsed times are recorded; headers, bodies,
query strings, arbitrary paths and credentials are excluded. Five isolated recorder
regressions cover stalled navigation, terminal events, redaction and memory bounds.
An isolated Firefox control and GET-only public console observation passed on
17 October; API requests were blocked. That observation does not qualify the failed
Preview or establish the cause of its earlier navigation timeout.

The public navigation, image, isolation and accessibility assertions and their
timeouts are unchanged. This corrects an observed anonymous cleanup-hook timeout;
it does not establish the cause of a separate hosted navigation transport timeout.

The six new administration cases use isolated, test-owned API responses to verify
the permission form, role visibility, OS-preference focus preservation and accessible
state labels. Backend `tests/test_physical_permission.py` exercises actual authorization,
Origin/CSRF, password/revision checks, concurrent Block/Stop, expiry and logout.
The real API/safety smoke requires `administrator_operation_permission`: the designated
staging administrator must actually allow then block permission while hardware stays
unconnected/inactive and task history stays unchanged. The previous 84-case hosted
result cannot qualify this new source; local Firefox launch failures stay explicit
until the new source passes Firefox in CI and hosted staging.

## Dependency maintenance

The 20 October 2026 audit detected critical
[GHSA-6q48-6c7w-9gw2](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2):
certifi 2026.7.22 is affected; the reviewed source remedy is 2026.10.19. Andrew Gotora
owns remediation in the existing restricted findings register. Preserve the original
failed CI and any Dependabot advisory/PR references. Updated locks/audits are fix
preparation, not deployed verification: qualify the backend candidate, rebuild its
image and read back the installed version and trust store after release. Keep TLS
hostname/certificate validation, scans and owner-controlled dev/staging/main promotion.
Do not dismiss the finding or describe production as patched merely because a lock
or default-branch alert changed. No unrelated dependency upgrade is included.


The owner's latest 14 October 2026 instruction authorizes temporary Dependabot
security-update PR branches and necessary narrowly scoped security-release candidates,
while retaining exactly three permanent branches: dev, staging and main. It supersedes
the earlier same-day security-PR disable instruction. The owner confirmed receipt and
review of Actions failures and Dependabot notifications. Alerts, dependency/source and
final-image scans, existing security PRs and evidence remain intact. The root coordinating
session restores and verifies the separate repository security-fix setting; a source
configuration alone does not prove the remote setting is active. Read-only API
metadata at 2026-10-14 01:25 UTC returned `enabled: true`, `paused: false`;
default main and the existing open dev-to-staging PR were preserved.

Ordinary version PRs remain disabled with `open-pull-requests-limit: 0`. Their retained
configuration targets dev. Security updates always target the unchanged repository
default main; target-branch does not redirect security fixes, and version-only groups/
cooldown settings do not constrain them. No auto-merge or automatic production release
is enabled. Review fixes through [the security integration procedure](CI_CD.md#security-pr-integration-and-urgent-candidates).

The existing full verification workflow runs weekly on Monday at 06:17 UTC once this
version reaches main; dev/staging/main changes retain required checks. Temporary security
PR code receives credential-free GitHub-hosted PR checks targeting a permanent branch;
never send it provider secrets or a persistent privileged runner. A scheduled scan or
Actions session does not itself establish delivered monitoring.

Review alerts and preserve advisory, severity, affected version, remediation and owner
in the existing restricted register. Normally apply reviewed fixes on dev and qualify
staging/main; necessary urgent candidates start from exact production and exclude
unfinished dev work. No alert dismissal, check suppression, credential rotation or
branch deletion is implied. Direct graph SBOM readback previously returned HTTP404;
no dependency-graph setting was changed by this source update.

## Safe acceptance failure evidence

The hosted observer keeps the failing phase and distinguishes unexpected HTTP
status, invalid JSON, timeout, DNS, TLS and socket failures using fixed categories
and bounded numbers only. It retains no URLs, error text, response bodies or credentials.
A transport failure is never a passing assertion or a confirmed application defect.
Expected replay denial still requires HTTP409 and valid JSON; timeouts, assertions,
mutation counts and retry limits are unchanged. Use the existing bounded repair
authority before another failed-context attempt.


## Bounded journal connection reuse

The Turso transport retains at most two healthy HTTPS connections for ten idle
seconds. This avoids a new TCP/TLS handshake for every five-second lease renewal.
It does not retry ambiguous statements or extend the two-second statement/seven-second
renewal budgets. Expired/disconnected idle sockets are discarded before sending;
a read/write failure after send remains a failure and never replays the statement.
Mock socket tests exercise healthy reuse, idle expiry, server disconnect and a
lost acknowledgement on an already reused connection. Hosted qualification is
still required; four staging SELECT1 samples establish overhead, not an uptime guarantee.


## Conditional journal recovery after a dropped connection

Task persistence compares the current record with its last acknowledged snapshot
inside the same owner-fenced UPSERT. An identical desired snapshot is idempotent;
a delayed older request cannot replace a newer acknowledged state. No schema or
existing history rewrite is required. After RemoteProtocolError only, an exact
read of the prior state (or an absent new ID) permits one conditional retry using
the same task ID and payload. Unknown/changed/missing known history, unavailable
confirmation, ordinary timeouts and explicit provider denials remain failures.
Each HTTP statement still has two seconds; total task-save recovery is bounded at
eight seconds, below the unchanged browser request deadline. Lease ownership,
renewal deadlines and safety/readiness limits remain unchanged. Real SQLite tests
cover late request ordering, committed/uncommitted acknowledgements, restored
snapshots, conflict, missing history and exhaustion. This is not an API command
replay or a promise to detect privileged out-of-band database tampering.


## Hosted completion observation

The simulation observer retains its original15-second total completion budget
(9s durable running acknowledgement plus6s history assertion). It starts observing
the existing GET task response immediately, requires the exact owned task to be
completed, then applies the unchanged6s DOM assertion within the remaining total.
It sends no extra request, retries no command and changes no browser30s test,
client10s request or backend safety/watchdog deadline. A real delayed response
control distinguishes this sequencing correction from an application failure.
Unknown/unauthorized/malformed responses and another task never satisfy it.

## Idle journal recovery and Preview observation regressions

Run `python -m pytest -q tests/test_idle_journal_recovery.py` in the backend
environment, then the complete affected backend suite. The fake Turso HTTP
boundary uses isolated SQLite and synthetic credentials; it never targets a cloud
database. Cover late old-owner writes, live-owner contention, changed/pending or
unacknowledged history, revoked sessions, cancellation and bounded shared backoff.

Frontend unit tests reject automation credentials for unintended origins. Browser
qualification sends Vercel's documented `x-vercel-skip-toolbar: 1` request header
without changing project protection or CSP. The zero-script public-page assertion
remains mandatory and reports only fixed script classes on failure. This header
is a hosting-interference precaution, not proof of the earlier unknown script's
source. Navigation and assertion deadlines remain unchanged.

## Node TLS trust regression

Use Node 24.22.1 or a later reviewed security patch in the 24 series. Run
`node --test scripts/test_tls_trust.mjs` from the repository root. The test uses
a known public CA certificate and newly generated synthetic localhost certificates
in isolated child processes. It proves native fetch/HTTPS trust, rejection of
untrusted certificates and hostname mismatches, idempotence and fail-closed startup.
Temporary test keys are removed; it sends no application/provider requests.

The startup policy excludes only the SHA-256 fingerprint of the affected
emSign ECC Root CA - G3 from the effective application default store. It retains
other default/extra authorities and never disables certificate verification.
Node changes are thread-local. The gateway imports the policy before its
handler; container Node entry points also preload it. The protected Python
simulator backend remains an independently qualified component.
See the [advisory](https://github.com/advisories/GHSA-6q48-6c7w-9gw2) and
[Node TLS API](https://nodejs.org/api/tls.html#tlssetdefaultcacertificatescerts).
Source tests are separate from source-bound staging and production acceptance.

The inspection cases cover seven scenarios, three responsive/appearance layouts,
and independent Stop during pending approval in each browser. Backend tests cover
exact-plan authority, source conditions, unit/freshness findings, one active task,
session-isolated imports, bounded caches/writes, real fenced journal acknowledgements
and an actual owned-process crash/restart with disposable data. Browser/source test
results are distinct from hosted qualification and production acceptance.

## Robot owner-run adapter checks

`python -m unittest discover -s scripts/release -p 'test_oracle*.py'` exercises
synthetic signing stores, installer source verification, image/scan descriptors,
source binding, expiring operator custody, cross-process locking and interrupted
provider requests. These fixtures never establish hosted acceptance or owner authority.
The release tool image in `infra/oracle/qualification.Dockerfile` uses the locked
Chromium, Firefox and WebKit engines; all three must actually launch and pass their
hosted checks. A completed image build or one passing engine cannot qualify it.

The separate offline runtime-image recovery probe creates only disposable synthetic
inspection data, completes a report and opens a fresh application instance. It
checks exact report preservation, revoked sessions, stopped/idle state and the existing
seven-field task schema. It runs without network or cloud credentials. A successful
probe does not authorize an old-source restart or a production database restore.


The Robot owner tool extends the existing Oracle bridge. Its stdlib release tests
run in the backend verification job; `node --test
scripts/release/oracle_vercel_transport_guard.test.cjs` runs without credentials in
the frontend job. The pinned Vercel 65.2.0 client/CLI protocol fixture additionally
runs offline inside the independently scanned tool image, using synthetic credentials
and a loopback provider only. It exercises cold/warm upload negotiation, project-root
resolution, immutable read-only credential custody and one-create request enforcement.
That fixture supplies no production authority. Keep its original failed observations
alongside a corrected result in the protected evidence index.

The pinned CLI protocol fixture asserts each actual outgoing `ROBOT_API_URL` value.
The history/disclosure case must restore its own completed scenario's stopped state;
later reset/legacy cases must not inherit an unrelated ready state. Run the history
case followed by a legacy simulation case to verify that isolation without increasing
deadlines or removing checks.

## Post-action observation and catalog readiness

`tests/observation-concurrency.test.mjs` verifies that a successful action waits for
a snapshot started after the action, without overlapping observation batches or
ignoring current-session authorization failures. The catalog timing browser cases
hold later polls and verify that completed metadata enables only the latest valid
observation. Late metadata cannot restore controls after Stop or a failed read.
These controlled fixtures are local regression evidence; genuine hosted workflow
qualification remains required.

## Sign-in restoration and unavailable JavaScript

`tests/browser/initial-sign-in-input.spec.mjs` settles a held anonymous lookup after
typing and before clicking Sign in. The unchanged application loses the password
in both controlled WebKit cases, preventing a login POST. The correction passes
these cases, existing restoration/readiness cases, unit checks, types and build in
Chromium, Firefox and WebKit: 15 browser cases with no skips or retries. No login
happens before the deliberate click; valid credentials submit once, rejected
credentials grant no session, and rejection/logout clear all password fields.
The preservation option applies only to the initial lookup; later restoration
retains normal expiry cleanup. Tests use intercepted synthetic loopback services.

`tests/browser/sign-in-restoration.spec.mjs` delays the initial anonymous session response until a submitted sign-in is waiting for simulator readiness. The pending restoration must not erase or replace that explicit submission. It also blocks the application module and checks that native form behavior cannot place a password in a navigation URL. The controlled restoration case fails on the prior source in Chromium, Firefox and WebKit.

The sign-in handler captures the submission immediately and binds it to its own session generation. The disabled native submit control becomes available only after its handler is installed; the form uses POST even without JavaScript. Owner browser diagnostics retain only bounded same-origin route/method/status counts, never credentials, response bodies, cookies or query values. These regression checks do not establish the cause of a historical hosted failure or qualify a release; exact-source hosted verification remains required.

## Independent API and frontend production boundaries

The API-only production browser observation checks the retained frontend's existing
task proxy for anonymous denial. The actual new API separately checks anonymous
mission/catalog denial, authenticated catalog/history, stopped-idle state and
physical-operation denial. Every staging browser and the independently promoted
frontend still checks the new mission proxy. A missing route, redirect or other
non-401 response never becomes a fallback success.

`tests/owner-component-boundaries.test.mjs` uses actual local HTTP responses to
exercise this split and prove single-request failure without fallback. Release
tests require distinct before-sign-in/after-logout receipts for all three engines;
missing, duplicated, mismatched or failed observations are refused. These changes
need fresh source-bound qualification and authorization. Previously failed
production observations and provider intents remain immutable and must not be
resubmitted or relabelled.

The release suite also covers a separately commissioned frontend repair prerequisite
when a previously deployed API remains unverified solely because its retained
frontend fails accessibility. This prerequisite is not API verification or release
authorization. It binds the exact frontend source/build, original API chain and
unchanged runtime, and preserves all fresh frontend qualification checks. Real-key
fixtures cover expiry, revocation, tampering, source/artifact/scope changes, owner
provenance, numeric run ID zero and concurrent issuance. An accepted frontend is
required before the original API observer can inspect that repaired UI; the original
API deployment request is never repeated.

## Full-body humanoid source checks

`onesa-ai-robot-backend/tests/test_humanoid.py` exercises the connected 33-axis tree,
URDF limits, coordinated whole-frame motion, peak trajectory bounds, exact-plan
integrity, command replay retention, session ownership, dedicated heartbeat expiry,
Stop/adapter failure and asynchronous Stop/reset/logout/session-expiry confirmation
races. `test_humanoid_journal.py` holds the existing mutation lock with a real fenced
journal behind the synthetic HTTP boundary and verifies that supervision and Stop
finish before that lock is released.

The frontend proxy tests check every exact humanoid route and reject unknown
subpaths, malformed command paths and query injection. `humanoid.spec.mjs` covers
immutable review values, appearance/focus, mobile accessibility, stale observations,
unresolved proposal/approval acknowledgements, page departure and session loss.
`humanoid-live.spec.mjs` crosses the real gateway and API for sign-in, reset, all-axis
review, confirmation, observation, Stop and authenticated URDF export. Run these
with the existing complete Chromium/Firefox/WebKit suite and disposable synthetic
services. None of these checks supplies physical commissioning or hosted acceptance.

Administrator appearance checks compare every operator-action request while allowing
the existing scheduled legacy heartbeat during accessibility scans. A scan crossing
the normal poll interval must not mistake routine supervision for a theme-triggered
permission or motion command.
