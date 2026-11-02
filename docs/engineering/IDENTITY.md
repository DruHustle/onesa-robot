# Shared Onesa account — Robot client

The account hub in the existing Company Portal is the central identity authority.
One account may have AI, Drone and Robot access in any approved combination. Robot
uses its own opaque session and retains its independent API, data and safety gates.
The integration is off by default and uses the existing Oracle/Vercel topology;
no new paid identity provider or runtime is introduced by this source change.

## User experience and permissions

The console reads its mode from `/api/v1/auth/identity/config`. Shared mode offers
**Continue with Onesa** and the issuer origin's `/account` hub. The user signs in
once centrally; opening another approved product reuses that central session.
Missing Robot access produces a clear route back to the account hub to request it.
It never silently creates a separate password account or grants a requested role.

| Central grant | Robot behavior |
| --- | --- |
| No Robot entitlement | Sign-in is refused; request access in the account hub. |
| Robot viewer | Read the account's workspace/history. No proposals, reset or approvals. Stop remains available. |
| Robot operator | Use the supervised simulator and own records after recent MFA and explicit review. |
| Robot admin | MFA is required for entry; existing software permission administration also requires fresh MFA. |
| Global administrator | Robot administration and centrally managed access across products; central MFA is required. |

Global administration does not automatically disclose another account's private task
history, link historical accounts, activate controllers or bypass exact-plan review.
An AI entitlement alone grants no Robot access. Ordinary product selections remain
access requests until an authorized administrator approves them centrally.

## Identity protocol v1

Register an exact confidential Robot client with the existing Portal issuer at
`<portal-origin>/api/v1/identity`. The protocol uses `/auth`, `/token`, `/session`
and `/revoke`. Authorization uses a one-use code, S256 PKCE, independent state and
nonce, and an exact callback. The browser never receives a client secret or bearer
access token. The code exchange uses `client_secret_basic`, including OAuth form
encoding of client credentials before Basic encoding.

The returned ID token is deliberately discarded. It is not parsed or trusted as an
identity assertion. Robot obtains identity and authorization only from a TLS-bound,
confidential `POST /session` request with `{ "token": "<server-held-token>" }`.
The response must bind the exact issuer, client audience, stable UUID subject,
verified email, expiry, actual authentication time, authentication methods, global
roles, product entitlements and product roles. Unknown/malformed assertions fail
closed. The copied `robot_api/onesa_identity.py` utility is independently owned by
this repository and tested with synthetic HTTP responses; no sibling code is a
build or runtime dependency. It uses the already locked httpx dependency.

All protected reads and operational writes revalidate central access. Own-session
logout clears local authority before requesting confidential central revocation.
Central outages cannot authorize an operation. Local Stop takes effect after the
existing local cookie/CSRF checks before a central network wait; the independent
watchdog continues running. Removing an entitlement revokes local authority and
stops owned active work. Demoting an administrator revokes its physical permission;
demoting an operator to viewer also stops owned active work.

### Sessions, owners and step-up

Sign-in transactions expire after ten minutes, are limited to 64 active records,
and are browser-bound with a short HttpOnly SameSite Lax cookie. The callback admits
only single `code`, `state`, `error`, `error_description` and `iss` parameters, with
a bounded total length. It only redirects to fixed local console outcomes. The
gateway preserves the narrow query/cookie allowance on these identity routes and
continues to reject queries and redirects on operational routes.
Starting sign-in also records the browser's existing local session for retirement.
A successful account switch revokes that session and its execution/permission
authority even when the Strict session cookie is absent on the callback. Only
step-up additionally requires the same subject and an unchanged live session.

Local Robot sessions retain HttpOnly, Secure-on-HTTPS, SameSite Strict cookies,
exact Origin and per-session CSRF checks. Sessions expire using the shorter of the
existing Robot lifetime and central token expiry. They are bounded to 100 total
and five per identity. Bearer tokens and identity transaction state remain in
process memory and never enter the SQLite or Turso journal, browser storage or logs.
Process restart and idle journal recovery clear local identity/session state.

Task owners use a namespaced SHA-256 of exact issuer plus stable subject, not email
or display name. Existing configured-operator history remains under its original
owner. No automatic linking, ownership rewrite or email-based merge occurs.
Migrating a historical operator's records requires a separately reviewed exact
identity binding and preservation procedure.
Humanoid terminal history is filtered by the same stable owner in shared identity
mode. Its ownership metadata follows the existing bounded in-memory history;
approval and execution remain bound to the current local session.
Shared inspection approval events record central MFA and explicit exact-plan review.
Their bounded actor field stores the full issuer/subject SHA-256 digest used by the
task owner, without the storage namespace prefix; it contains no email address.

Reset, task approval, inspection approval, humanoid approval and physical-permission
allow require central MFA authenticated within five minutes, plus explicit review
confirmation in the submitted body. The step-up request uses `prompt=login`,
`max_age=0`, and `acr_values=urn:onesa:mfa`; it must return the same subject and a
genuinely fresh authentication time. Pending or active work must be stopped/cancelled
before a step-up. The UI never replays a plan after sign-in. Existing 15-second
proposal expiry, exact plan hashes, session ownership, revision checks and safety
gate remain authoritative. Physical activation remains rejected at startup.

The **Sign out of Onesa** action requests `/revoke` with `all_sessions:true`. It
clears local authority even if central revocation fails and exposes whether the
issuer explicitly returned `all_sessions_revoked:true`. Local sign-out must not be
described as confirmed global sign-out during an issuer outage or when the central
token/grant has expired. Token replacement after step-up revokes only the
superseded Robot token. The central issuer independently controls whether its
source-session rotation also ends older grants in other products.

## Configuration and rollout

| Server-only variable | Meaning |
| --- | --- |
| `ONESA_IDENTITY_ENABLED` | Explicit feature activation; default false. |
| `ONESA_IDENTITY_ISSUER` | Exact Portal identity issuer; HTTPS outside local development. |
| `ONESA_IDENTITY_CLIENT_ID` | Registered Robot client identifier for this environment. |
| `ONESA_IDENTITY_CLIENT_SECRET` | Private confidential-client secret; never frontend configuration. |
| `ONESA_IDENTITY_CALLBACK_URL` | Exactly `ROBOT_PUBLIC_ORIGIN` plus `/api/v1/auth/identity/callback`. |

The existing operator name/hash remain unchanged for legacy rollback. Enabling shared
mode rejects legacy password login and cannot reuse legacy sessions. Disabling the
mode restores the existing login path without migrating central identities into
local credentials. Staging and production clients, secrets and exact callback URLs
must remain separate. Wildcard Preview callbacks are not accepted.

Promote reviewed source through dev, staging and main under the existing gates.
Commission the issuer, product grants, exact clients and settings through the
existing owner-controlled deployment path. Qualify actual hosted sign-in,
cross-product navigation, MFA, central revocation and tenant/owner isolation before
claiming activation. This document is a source contract, not a hosted acceptance
receipt or permission to deploy production/hardware.

## Verification

`tests/test_onesa_identity.py` exercises the confidential protocol with isolated
HTTP responses. `tests/test_shared_identity.py` exercises real Robot routes,
browser-state binding, replay rejection, immutable subject ownership, roles,
explicit confirmation, MFA freshness, revocation during an outstanding request,
global logout and outage behavior. Existing simulator, physical-permission,
humanoid, inspection and journal suites retain legacy-mode coverage.

Frontend build/types and unit tests include `tests/identity-gateway.test.mjs`, which
uses a disposable loopback gateway to check actual cookie/query/header handling,
allowed local redirects and refusal of external redirects. Browser acceptance must
exercise both sign-in modes, narrow layouts, focus, required review checkboxes and
truthful error states. Local synthetic evidence does not establish hosted acceptance.
