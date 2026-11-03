# Robot simulator deployment and recovery

A frontend-only owner plan may reference the original independently accepted API
store and explicitly bind each actual API environment. Backend, edge and ROS bytes
must match the reviewed frontend source; current image checks and exact runtime
readback remain mandatory. The frontend receives its own fresh source-bound
qualification and authorization. Referencing an API acceptance record does not
renew it, relabel its source, or permit another API deployment.


An existing API deployment with a failed observer may be completed by the separate
owner-commissioned read-only observer in `oracle_robot_recovery.py`. It preserves the
original signed source, qualification, authorization, provider receipt and plan;
reviewed tool source must prove backend, edge and ROS bytes remain unchanged.
Fresh immutable image checks and complete production observations remain mandatory.
No replacement API deployment or transferred approval follows from a frontend or
observer correction. Commissioning and use belong in the protected guide.


<!-- onesa-protected-source: robot-deployment -->

Frontend staging explicitly selects Preview before dispatch. Provider source,
project and target readback and the single durable create budget remain mandatory.
Source changes require fresh reviewed installation and qualification.

Operational qualification waits a bounded time for a genuine post-staging monitor
sample and confirmed recovery. Unresolved alerts or notifications still block it;
earlier failures and one-use qualification reservations remain preserved.

Later owner authorization and deployment checks can re-observe existing staging
read-only when its operational observation has aged. The original signed full
qualification remains immutable and must still be current and unrevoked; no
context is renewed, Preview created, runtime replaced or simulator mission replayed.
Fresh observations retain their own identity and unchanged monitor freshness gates.

Runtime preservation snapshots wait a bounded time for temporary Docker clients
to finish. Every persistent and unknown workload remains in the snapshot; actual
identity changes, missing services and incomplete observations still block release.

Detailed operational instructions are maintained in protected Engineering Docs.
This public source is a reference page, not a deployment or recovery runbook.

[Open the protected guide](https://docs.engineering.onesa.eu/docs/ecosystem/ROBOT_DEPLOYMENT).
Sign in through Onesa Portal with an individually approved engineering identity.

Public repository history may contain earlier editions; moving this active copy
does not erase historical exposure. Credentials, records and release evidence
belong in their existing restricted stores, never in this reference page.

## Oracle owner-run evidence bridge

The new `scripts/release/oracle_owner.py` adapter preserves owner-run receipts
with workflow run ID `0` and genuine CI run references. It requires a separately
commissioned, pinned external verifier and owner-controlled signing custody.
Repository code or an unsigned JSON file supplies no release authority.
The protected guide contains installation, qualification and recovery instructions;
source implementation alone does not establish commissioning or deployment.

The same immutable installer includes `oracle_robot_journal_diagnostic.py` for
owner-run read-only investigation of an existing journal latch. It binds a literal
installation receipt pin and the selected running container, checks runtime
identity and private configuration before and after observation, and emits only
allowlisted latch/recovery log fields and aggregate remote task/lease state. Lease
remaining time comes from the database clock. Tokens, lease owner nonces, task
contents and arbitrary log text are excluded. Its bounded current-process log
window may omit the original latch; missing evidence is not a healthy result.

This diagnostic creates no release receipt or recovery authority. A live lease
does not prove ownership by the current process, and terminal remote task counts
do not establish local stopped/idle state, absence of dirty writes, history
equality, or an available recovery budget. Existing bounded idle recovery remains
limited to eligible renewal transport failures. Other latches require the original
fixed failure metadata, retained history and lease evidence, and the protected
owner recovery procedure. The diagnostic never logs in, clears data, replaces a
lease, restarts a process or replays a task. Installation and actual hosted
readback remain separate required steps.

## Shared identity release profile

The explicit `shared-identity-v1` owner plan binds the candidate and installed
tool commit, Git tree and source archive together. It permits changes only to
the five `ONESA_IDENTITY_*` settings required by the existing Robot adapter,
with exact environment-specific issuer, confidential client, callback and client
secret digest. Existing journal credentials, local operator identity, telemetry
identity, simulation-only behavior and disabled physical actuation retain their
previous preservation gates. A plan without this profile cannot enable identity.
Staging and production use separate registered clients, secrets and test subjects.

Qualification uses distinct `identity-api` and `identity-frontend` component
bindings. The existing `api` and `frontend` components remain identity-disabled.
The installer accepts its original two-component policy or an explicit policy
containing all four; commission all four before starting the bridge when identity
activation is planned. Every component retains exactly one qualification context
per source. The full component remains in signed records, artifact directories,
provider intents and receipts. Runtime implementation selection does not rename
an identity binding or consume a native component's allowance.

The deployment order for one exact current-main candidate is:

1. Independently qualify and accept the native `api` and `frontend` bridge with
   identity disabled. Its current gateway supports identity callback query
   parameters, redirects, cookies and mission routes. Historical frontends lacking
   those routes cannot support identity activation.
2. Bind `identity.native_bridge.api` and `.frontend` to each native component's
   full `binding` and accepted `operation_id` in the same verifier store. The
   identity runner verifies both original signed, nonrevoked accepted chains,
   identical candidate/tree/archive, the exact bridge API image and both retained
   identity-disabled configuration hashes. The native frontend's signed runtime
   must match the accepted native API, and its verified production deployment ID
   must match the planned baseline. This dependency check creates no new approval.
3. Prepare the paired `identity-api` / `identity-frontend` bindings and exact
   identity configuration. Backend files are isolated at
   `/etc/onesa/backends/candidates/<commit>/identity-api/robot-<environment>.env`;
   frontend files are under `identity-frontend/robot-frontend-<environment>.json`.
   The native bridge files remain at their original candidate paths. Both identity
   bindings use the identity API configuration; frontend configuration JSON names
   `identity-frontend`. API preparation uses the pair without a
   `release_component` override; frontend-only preparation selects
   `release_component: identity-frontend` after identity API acceptance.
4. Qualify and accept `identity-api`, then independently qualify and accept
   `identity-frontend`, each with its own staging and production observations.
   The frontend dependency query accepts only the `identity-api` scope. The
   staging baseline deployment in the frontend plan must identify the current
   preview produced by identity API qualification, with the same exact source.

Production API acceptance starts PKCE with the actual same-origin
`POST /api/v1/auth/identity/start` and continues through the real Portal form,
fresh MFA and Robot callback. This allows the already accepted native bridge
frontend to remain deployed during API qualification. Frontend acceptance must
still click the actual product sign-in control. Neither path substitutes a
fabricated session, bypasses the callback, nor relaxes the existing read-only
production checks.

The commissioned probe closure includes the identity Python driver and browser
helper. Existing test-account credentials are held separately in private,
expiring owner custody, bound to the source and exact account UUIDs. A real
browser observes PKCE authorization and callback state, authenticates at the
configured Portal origin, verifies the account UUID and fresh MFA, and obtains a
host-only Robot session. Cookies retain their original domain and path. Preview
credentials are used only in no-redirect handshakes and replaced with host-only
protection cookies before browser navigation. No account or role is provisioned
by the Robot release runner.

Staging retains every existing simulator and synthetic edge check. The identity
branch replaces session acquisition, reconnect and password confirmation with
shared sign-in and explicit fresh-MFA confirmation; it does not skip simulation,
safety, cancellation, replay, audit or administrator permission checks. All three
browser engines retain the seven inspection scenarios and accessibility checks.
Production keeps authenticated read-only observations for both administrator and
ordinary operator identities, including denial of administrator endpoints to the
operator. Sign-out must confirm central revocation. Existing source/provider,
database history, backup, rollback and monitor gates remain mandatory.
Signed observations also bind the exact backend container, immutable image,
source and configuration digest before and after acceptance. The digest covers
runtime configuration, host settings, mounts and network identities without
disclosing their private values. Frontend observations retain the actual verified
provider deployment ID; a changed runtime cannot inherit a prior PASS.

The profile cannot repair a journal latch or transfer legacy acceptance to an
identity-enabled runtime. Protected owner procedures still supply genuine
installation, registered-client configuration, source-bound qualification and
hosted evidence. The public source and synthetic tests establish none of those
operational outcomes.

The Robot-specific extension adds an immutable installer, API and frontend runners,
existing-operator credential confirmation, and synthetic hosted probes to that same
bridge. Its source is `scripts/release/oracle_install.py`, `oracle_robot.py`,
`oracle_robot_frontend.py` and `oracle_robot_probe.py`. It does not create a service
or grant authority to repository jobs. Three-engine tooling is disposable and remains
independent of the application images. Installation, commissioning, qualification
and verified production acceptance must each be recorded separately in the protected
guide; the presence of these modules does not establish any of those outcomes.

Owner HTTP observations identify the Onesa release client explicitly. This does
not impersonate a browser or crawler or change the ingress protection policy.

The commissioned staging observer runs the existing simulator suite inside its
pinned qualification image, including transitive module pins. All fourteen legacy
checks remain mandatory; production never invokes this mutating suite. Missing
modules produce bounded private failure metadata without disclosing exception text.
The Oracle adapter selects only the exact staging edge API; existing Render target
validation remains the default. Owner-run receipts never masquerade as Actions.
