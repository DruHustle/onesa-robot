# Onesa Robot mobile

Install Node 24, then run `npm ci --ignore-scripts`, `npm test` and `npm run prebuild`. Use `npm run android` with the Android SDK or `npm run ios` on macOS with Xcode. The Android and iOS targets use the pinned Expo template. Product build 0.1.0 (1) uses application ID `eu.onesa.robot`.

The API origin is selected at native build time with `EXPO_PUBLIC_ROBOT_API_ORIGIN`. If unset, the client targets the production `https://robot.onesa.eu` origin. Set it to the exact HTTPS staging origin (for example, `https://onesa-ai-robot-backend-staging.onrender.com`) when building a staging candidate. Only the exact production origin `https://robot.onesa.eu` and approved staging API origin `https://onesa-ai-robot-backend-staging.onrender.com` are accepted; paths, query strings, credentials, non-HTTPS schemes and unrelated hosts fail closed. Expo embeds this public routing value in the bundle; never place credentials or tokens in it.

The app opens `https://auth.onesa.eu/authorize` in the system browser. Enable the managed Identity configuration in this product backend. Register its existing HTTPS callback with the provider. A mobile start retains the confidential PKCE verifier at the backend and stores a separate HttpOnly transaction cookie in the app HTTP client. The HTTPS callback forwards only the code and state to `onesarobot://account/callback`. The app redeems once in its original cookie jar and uses the product session, role checks and resource ownership for subsequent requests. An interrupted sign-in can be restarted.

Install the release build, sign in from both a fresh and an existing browser session, select a record, let its session expire, and confirm the return to that record. Repeat with a role that lacks access. For a store release, use the product owner’s signing identity and retain the provider’s upload receipt.

## Independent operator Stop and simulation controls

Once authenticated, every role (including viewer) can press **STOP simulator now**
even while another proposal, approval or history request is in flight. The
native Stop bypasses the ordinary mutation lock and sends a fresh POST to the
same-origin Robot API using the current CSRF token. It does **not** retry an
uncertain request or replay pending tasks. After acknowledgement it verifies
`stopped=true`, `active_task=null` and `physical_actuation=false` using
the authenticated status endpoint, then requires the separate 33-axis humanoid
observation to confirm `stopped=true`, no active command, no pending proposal,
`physical_actuation=false` and a healthy `simulated_actuators` connection. A
`simulator_fault` response is never reported as a verified Stop. Reset also verifies simulation mode, an empty active-task state, terminal task history and an idle, healthy humanoid view with physical actuation explicitly false before reporting readiness. An unavailable
or contradictory readback
is shown as **unverified**, never as a completed Stop. The watchdog still
independently cancels unsupervised tasks.

Operators can explicitly review and approve a proposed **software-only**
joint task, cancel a proposal, and reset the simulator Stop latch. Approve and
reset require an explicit second confirmation and the backend's fresh Onesa
MFA check; native UI confirmation is not a substitute for MFA. A rejected
operation never overrides the server's stop epoch, access grants or task
ownership. The native UI performs same-account Onesa MFA step-up in the system browser
before approval or Stop reset when the current confirmation has expired. The
backend verifies the original subject and a fresh MFA assertion before rotating
the product session and CSRF token. After browser MFA, the operator reviews the
fresh state and explicitly confirms again; the earlier operation is not replayed.
Native availability and production identity/provider acceptance remain separate
release gates.
No hardware, ROS actuation or store submission is authorized by this UI.

## Foreground supervision and account-bound return

While active, the client reads the current account, status, owned task history and
independent humanoid state in one non-overlapping observation batch. Only a complete
observation less than 1.5 seconds old permits ordinary mutations. A heartbeat is
sent only for the current operator's observed running joint task; it never renews
a humanoid command. Subsequent reads begin after 500 milliseconds. Missing,
contradictory, late or failed reads lock controls and suspend supervision. Moving
the app into the background (or losing Android window focus) immediately invalidates observations and pending UI
intents. The independent backend watchdog stops a task when supervision is lost;
returning to the app reads current state and never replays or resumes that task.

Stop remains independent of a stalled ordinary request. Its generation change
also prevents a delayed proposal or approval response from restoring old controls.
Session expiry clears private task data; role changes are taken from each fresh
authenticated observation. Reset requires an observed stopped simulator with no
active command, pending humanoid proposal or nonterminal journal row. The backend
rechecks those conditions after confirmation and identity waits, including its
existing Stop generation fence.

The additive `/auth/me` and legacy-login response `id` is an opaque stable Robot
account key. In managed mode it derives from the existing issuer/mapping-namespace
and stable subject ownership hash; display name and email are not linking keys.
The mobile client requires this field and fails closed against an older backend,
so qualify the backend contract before rolling out the new client. Existing task
owners and session rows are unchanged. The old unbound SecureStore `csrf` entry is
deleted at startup, and every CSRF proof comes from the current authenticated API.

An exact `onesarobot://tasks/<UUID>` link selects only a task returned by the
authenticated owned-history endpoint. SecureStore retains a selected task together
with its stable account key. Expiry and a same-account sign-in restore that exact
task; an account switch clears it even when display names match. Unknown task IDs
remain unavailable. Explicit sign-out clears the selection and stores a local
signed-out marker, so restart cannot silently restore a session whose network
logout failed. Only deliberate sign-in clears that marker. Provider SSO termination
remains a separate backend-reported capability.

Every native API request has its own ten-second deadline, including reading its
JSON response. A stalled request is aborted and reported as unverified; it never
replays a mutation or accepts a late success. Stop keeps its separate request and
all acknowledgement, task-history and humanoid readbacks. A timeout does not prove
that the server cancelled an operation: refresh the current state before trying
again. The network regression uses actual cancellation signals to cover a hung
mutation and body read while an independent Stop completes and remains verified.

Run `npm test` for native network, lifecycle, account-bound return, MFA, observation
and Stop regressions. `tests/test_native_operator.py` in the backend starts a
disposable real HTTP application and signed synthetic issuer, then runs the same
native controller through MFA, completion, background watchdog cancellation,
in-flight Stop, offline denial, expiry, restoration, viewer denial and logout. Its
storage adapter is in memory; it does not qualify native Keychain, browser UI or a
physical device. Required before
GO: full pinned Android/iOS builds, installed-device Stop during an in-flight
request, fresh/signed-out sign-in and deep-link return, actual MFA approval,
offline readback failure, role-denied mutation, session expiry and independent
backend operator/observer acceptance on the exact release candidate.
