# Engineering changelog

## 2026-11-11 — Bench feedback and operator supervision — unreleased

Add a read-only ROS 2 JointState bench observer and an offline rosbag2 checker.
Both compare feedback with the supplied URDF and report timing and axis coverage.
The 33-axis simulator checks actuator readback before acknowledging a move. A
feedback fault cancels active motion and revokes physical-operation permission.
The console clears stale permission and joint displays and requests a new review
when the administrator permission revision or proposed humanoid plan changes.
The physical drive interface and hardware stop still require commissioning.

## 2026-10-31 — Full-body humanoid reference — unreleased

Recover the 33-axis reference model, whole-frame simulated actuator interface,
coordinated bounded motion, authenticated URDF and operator console. Bind approval
to the exact reviewed plan and session; use a separate command-specific heartbeat
so unrelated console polling cannot sustain unobserved whole-body motion. Stop and
authority loss revoke all axes, including during asynchronous confirmation, stale
telemetry, panel closure and unresolved responses. Preserve fail-closed adapter
errors and replay protection beyond the detailed history window.

Expose only the exact humanoid API routes through the existing gateway and add
real-proxy and real-API browser coverage alongside controlled response-race cases.
The UI locks draft controls while showing a read-only start/target review table.
Physical actuation remains disabled, and no dynamics, balance, independent fingers
or commissioned motor limits are claimed. This is source development, not hosted
acceptance or a production release.

## 2026-10-31 — Input validation review — unreleased

Reject malformed edge boot challenges at the schema boundary before constant-time
identity comparison; invalid telemetry leaves sequence, connection and audit state
unchanged. Treat excessively nested remote-journal JSON as a redacted protocol
failure under the existing stop/recovery rules, without retrying the request.
Regression tests reproduce both prior exceptions and verify controlled rejection.
The generated API contract records the existing server-issued challenge format.
These source fixes do not establish hosted acceptance or physical readiness.

## 2026-10-28 — Repository renamed to onesa-robot

Update canonical GitHub references, workflow owner/repository guards and local
repository-name policy lookup to `DruHustle/onesa-robot`. Keep the existing
component directory names, image/artifact identities, hosting resources and
commissioned runtime paths so this naming change requires no deployment or data
migration. Historical release records retain the names used when they were issued.
This source update does not qualify a new hosted release or migrate external
owner-installed trust records; those must be requalified against the new identity
before the next deployment.

## 2026-10-13 — Remove the unused cloud filesystem-path setting

Use the actual existing native Python staging service in the Free-only reference. Turso keeps sessions process-local and stores task history remotely, so no cloud filesystem path or disk declaration is needed. Remove the erroneous libsql value from the unused staging path key, preserve credentials/history and require the path only for filesystem storage modes.

## 2026-10-12 — Staging journal handoff and approved company contacts

Stop the existing synthetic simulation and expire only its observed Turso lease before rolling deployment, preserving operator credentials and history. Reject production, foreign resources and untrusted actors/runners. Use the verified hyphenated staging hostname, report attempted smoke failures as FAIL, and display approved company/contact particulars with central legal/privacy links. Full hosted acceptance remains required.

## 2026-10-12 — Stop requests when the operator page leaves

A hosted WebKit run caught an old-page task request continuing after reload. The typed client cancels pending requests when the page leaves, bounds request duration, and resumes authentication/polling only after a preserved page returns. Successful HTTP responses with invalid JSON are rejected. Existing session-generation guards and simulation-stop behavior remain intact. Three real HTTP client/proxy tests and 26 Chromium/WebKit browser cases pass; every hosted check remains required.

## 2026-10-12 — Visitor-time appearance and protected staging deployment bridge

- The simulation interface supports persisted Auto/Light/Dark at visitor-local 07:00/19:00 boundaries, including blocked storage and mobile controls. Both-mode simulation/a11y coverage now spans all five viewport widths.
- Owner-hosted `release-candidate.yml` dispatches `inspect-staging`, `repair-staging` and `staging` against the retained staging branch. This registered workflow avoids requiring an unqualified main merge solely to register new workflow files.
- Staging inspection confirmed that the existing Free/Frankfurt service uses native Python and the approved Turso journal. Staging now accepts only the reviewed hash-locked dependency command and single-process startup, pinned to the locally qualified Python 3.11.14. Repair changes branch/root/health/runtime commands and disables automatic deployment; it preserves credentials, database records, production and physical permissions. Production retains its stricter Docker/storage gates.
- Staging receipts retain FAIL/BLOCKED results and only attest browser success from actual successful steps. Production durable-storage/hardware gates remain unchanged. The existing Turso adapter is preserved.
- Local backend: 42 simulation tests pass; release guards and history/security scans pass. Updated official immutable Node 24-compatible Actions. Hardware and external capabilities remain unqualified until real authorized evidence exists.

## 2026-10-11 — Visibility-aware Actions policy

- Public repositories use hosted execution with zero local registration access; the former portal service was removed and its VM/login startup stopped. Repository visibility was not changed.
- Private workflows use a shared exact-revision local validation phase before cloud checks, preserving existing check names and release approvals. Runtime visibility hooks deny public/missing-token/API-failure execution independently of workflow conditions.
- Public cloud CI results and private unverified activation limitations are recorded in the runner maintenance guide after verification. Production remains gated.

- CI-only migration retains production gating by disabling automatic Vercel Git
  deployments at the scaffold root and frontend root. No provider release invoked.

Staging device identity is copied only from the verified provider into a 0600 candidate-bound runner file, reused for the synthetic challenge, and removed after acceptance; no identity or credential is exposed in artifacts.


## Pump inspection source — unreleased

- Add deterministic P-101 mission planning, versioned skills/profiles, exact-plan
  approval, event-bounded evidence, reports, export and read-only replay.
- Add optional session-scoped read-only robotics/v1 file review; preserve source
  identity, revisions, units, timestamps and declared quality. Import defaults off.
- Reshape the protected console and public product journey around industrial tasks,
  retain joint diagnostics and separate physical-operation permission, and document
  the evidence-backed deferred FluxVLA evaluation. No production acceptance is
  implied by this source record; the root changelog records verified go-live only.

## Owner-run Robot extension and history refinement — unreleased

- Extend the existing externally commissioned bridge with reviewed-source installation,
  independently scoped API/frontend operations, bounded hosted probes and expiring
  confirmation of the existing operator/device credentials. Caller JSON and Actions
  artifacts remain incapable of authorizing an owner-run release.
- Preserve durable provider intents and original timeout receipts. Read-only inspection
  reconciles an interrupted request without submitting it again. No old-source restart,
  automatic rollback, journal repair, new account or physical actuation is introduced.
- Preserve the existing Robot serverless region explicitly in the frontend configuration.
- Show six recent inspection records with expandable older records. Unchanged history
  refreshes preserve the existing DOM, keyboard focus and disclosure state.
- Source implementation and local verification do not establish trusted installation,
  fresh hosted qualification, authorization or production acceptance.


The unreleased owner extension explicitly supplies the verified `ROBOT_API_URL`
for each prebuilt deployment; Git metadata alone cannot select a branch-scoped
server variable. Canonical origins are checked before credential use. The history
browser scenario restores its own completed simulator to stopped before later legacy
journeys. Existing assertions, three-engine coverage and deadlines are unchanged.
