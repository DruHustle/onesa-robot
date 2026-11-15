# Onesa Robot mobile

Install Node 24, then run `npm ci --ignore-scripts`, `npm test` and `npm run prebuild`. Use `npm run android` with the Android SDK or `npm run ios` on macOS with Xcode. The Android and iOS targets use the pinned Expo template. Product build 0.1.0 (1) uses application ID `eu.onesa.robot`.

The API origin is selected at native build time with `EXPO_PUBLIC_ROBOT_API_ORIGIN`. If unset, the client targets the production `https://robot.onesa.eu` origin. Set it to the exact HTTPS staging origin (for example, `https://robot-staging.onesa.eu`) when building a staging candidate. Only a bare HTTPS origin under `onesa.eu` is accepted; paths, query strings, credentials, non-HTTPS schemes and unrelated hosts fail closed. Expo embeds this public routing value in the bundle; never place credentials or tokens in it.

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
`simulator_fault` response is never reported as a verified Stop. An unavailable
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
the product session and CSRF token. Continuous native running-task supervision
is still incomplete. Only the backend's bounded simulator and watchdog are
exercised; native availability and production identity/provider acceptance
remain separate release blockers.
No hardware, ROS actuation or store submission is authorized by this UI.

Run `npm test` for pure native network/Stop regressions. Required before
GO: full pinned Android/iOS builds, installed-device Stop during an in-flight
request, fresh/signed-out sign-in and deep-link return, actual MFA approval,
offline readback failure, role-denied mutation, session expiry and independent
backend operator/observer acceptance on the exact release candidate.
