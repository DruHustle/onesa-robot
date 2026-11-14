# Onesa Robot mobile

Install Node 24, then run `npm ci --ignore-scripts`, `npm test` and `npm run prebuild`. Use `npm run android` with the Android SDK or `npm run ios` on macOS with Xcode. The Android and iOS targets use the pinned Expo template. Product build 0.1.0 (1) uses application ID `eu.onesa.robot`.

The app opens `https://auth.onesa.eu/authorize` in the system browser. Enable the managed Identity configuration in this product backend. Register its existing HTTPS callback with the provider. A mobile start retains the confidential PKCE verifier at the backend and stores a separate HttpOnly transaction cookie in the app HTTP client. The HTTPS callback forwards only the code and state to `onesarobot://account/callback`. The app redeems once in its original cookie jar and uses the product session, role checks and resource ownership for subsequent requests. An interrupted sign-in can be restarted.

Install the release build, sign in from both a fresh and an existing browser session, select a record, let its session expire, and confirm the return to that record. Repeat with a role that lacks access. For a store release, use the product owner’s signing identity and retain the provider’s upload receipt.
