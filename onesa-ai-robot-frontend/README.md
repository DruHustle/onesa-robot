# Robot operator interface

The public `/` product page serves no scripts or operator telemetry. The `/console`
operator shell uses the shared pinned Onesa foundation and connects to an
independent authenticated software simulator through a fixed same-origin proxy.
No browser storage retains passwords, session tokens or control keys.

```sh
npm ci --ignore-scripts
npm run build
npm run typecheck
npm test
ROBOT_API_URL=http://127.0.0.1:8431 npm start
```

Use Node 24. PORT defaults to 3431. `ROBOT_API_URL` is server-only and must be a
credential-free HTTP(S) origin. Remote gateways require TLS. The proxy forwards only
the Robot session cookie and needed Origin/CSRF/content headers; provider bypass
headers/cookies are not forwarded. Backend `ROBOT_PUBLIC_ORIGIN` must match the
browser origin exactly. The operator state is software simulation, never physical robot telemetry.
Appearance follows browser/OS preference through CSS, including native controls;
there is no selector, appearance storage or clock schedule.

The local build manifest `design-system.consumer.json` pins the owner artifact and
build verifies its digest; the engineering manifest is an identical reviewed copy. No sibling source is a build input.

`npm run test:e2e` requires an isolated simulator, synthetic operator
`browser-test-operator-only`, and matching installed Chromium/Firefox/WebKit binaries.
The suite checks five viewport widths, keyboard entry, login/reset/proposal/approval/
completion/stop/logout and automated WCAG rules. Enumerate the current matrix with
`npm run test:e2e -- --list`; all three browser engines remain required. A
test-owned held-response fixture verifies independent Stop/Sign out and
originating-form duplicate suppression. Four offline
module regressions also exercise Stop, cancel, logout and duplicate submission.
Manual accessibility acceptance
remains separate. Do not use a real operator password or live hardware for tests.

`docker build -t onesa-robot-interface:local .` builds a separate non-root Node image.
The Vercel adapter reuses the same proxy boundary, but an actual Vercel build and
protected staged deployment still need qualification. The Git allowlist defaults to
false and permits only staging's protected native Preview. Production uses the
separately authorized prebuilt release; no public release is implied by a branch push.
API schema/types are generated from the backend, checked with
`python scripts/check-contracts.py` from the repository root using the backend venv.

Hosted staging and owner-approved release instructions are in the
[cloud release guide](../docs/engineering/AUTOMATED_QUALIFICATION.md). Configure a
server-only `ROBOT_API_URL` per Vercel environment. The monorepo CLI runs from repository
root and project root remains `onesa-ai-robot-frontend`. `npm run lint` checks source
syntax; build/types/unit, cross-browser flows and accessibility remain independent gates.

The inspection workspace consolidates planning, reports and read-only replay around
one owned run record. The scene is original SVG and calculated synthetic motion,
not a physics engine or perception model. Native scenario data is explicitly
synthetic. Optional file-import availability comes from authenticated runtime
configuration; ordinary AI accounts receive no Robot permission. Active/pending
polling is500ms, idle polling3s, and hidden pages suspend requests and replay.

The operator workspace also contains three locally packaged NVIDIA SimReady
reference models: transfer conveyor, mobile cart and warehouse rack. They load
automatically as the signed-in operator reaches the library. A rendered preview
remains available during loading or unsupported graphics, with an explicit retry.
Selection cancels old downloads, and sign-out disposes pending and GPU resources.
No continuous rotation, model service or third-party CDN is required. Mouse, touch,
focused arrow keys and Fit view provide inspection controls; browser/system
appearance changes preserve the selected model and operator session.

The build checks every model, preview, source record and license against the local
manifest. The browser bounds each GLB download, checks SHA256 and rejects external
resources or active model features before parsing. Three.js 0.186.1 is locked and
packaged from the installed MIT-licensed distribution; engines and assets remain
inside this independent frontend build. See [third-party notices](THIRD_PARTY_NOTICES.md).
These are static visual references. They add no physical behavior, sensing,
collision qualification, robot articulation or actuator authority. Inspection
plans and the 33-axis humanoid keep their existing simulation and safety controls.
