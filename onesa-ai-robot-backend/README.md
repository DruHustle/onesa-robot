# Robot simulation API

Hosted operator sessions require Company Portal. Local password login is limited to a loopback public origin. Existing authenticated stop and sign-out remain available during a cutover.

Python 3.11 / FastAPI. Install independently:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-dev.lock
.venv/bin/python -m ruff check robot_api tests
.venv/bin/python -m pytest -q
.venv/bin/python -m robot_api.security
```

Generate the operator password hash privately and store it as
`ROBOT_OPERATOR_PASSWORD_HASH`; do not paste the password/hash in chat or Git.
Populate the server settings in `.env.example` in an ignored local environment file
or approved secret manager. This app deliberately does not auto-load arbitrary .env
files. Export the settings securely in the process environment before `sh start.sh`.
For a development env file, use `.venv/bin/python -m uvicorn
robot_api.main:create_app --factory --env-file .env.local --host 127.0.0.1 --port 8431
--workers 1 --no-proxy-headers --no-access-log` as one command.

The production command is `sh /app/start.sh`; PORT defaults to 8431. One process
owns a persistent SQLite database at `ROBOT_DATABASE_PATH` (default
`storage/robot.sqlite3`), and the enclosing directory/volume must be private. Runtime
is non-root. `/api/v1/health` is process liveness, `/api/v1/ready` verifies database
and watchdog state for simulation, and authenticated `/api/v1/status` labels every
unconfigured capability honestly. Neither endpoint approves physical operation.

Cookie sessions are opaque, hashed in storage, scoped to `/api/v1`, HttpOnly,
SameSite Strict and Secure on HTTPS. Mutations need exact Origin plus session CSRF
proof. Stop is operator-authorized. Reset and approval also require password proof.
The default legacy mode retains one configured operator identity. The optional
[Onesa identity layer](../docs/engineering/IDENTITY.md) accepts a centrally approved
account with viewer/operator/admin access or global administrator authority. Shared
mode disables the legacy login route, retains existing credentials for rollback,
and revalidates live product access on protected reads and operations. Bearer tokens
stay in process memory. Identity-bound owners never use email matching. Operations
use explicit confirmation and central MFA authenticated within five minutes; legacy
mode still uses its existing password confirmation. Login
attempts and active sessions are bounded. Tasks/history are bounded and persisted.
Operational request bodies are limited to 16 KiB and a five-second total receive
deadline, including clients that trickle bytes. Incomplete requests return 408;
this does not replace frontend/network rate limits or authorize hardware control.

`docker build -t onesa-robot-api:local .` builds independently. Keep the API private
behind the approved same-origin frontend/TLS boundary. No credential is a frontend
public setting. Physical actuation=true always fails startup. No migration or test
uses either sibling product's database.

For durable remote task history on Render Free, use the explicitly selected
`ROBOT_STORAGE_MODE=turso`, `ROBOT_TURSO_DATABASE_URL` and server-only
`ROBOT_TURSO_AUTH_TOKEN` settings. See [Turso staging setup](../docs/engineering/TURSO_STAGING.md).
Local SQLite remains the default. This is a remote task journal with an in-memory
SQLite working store, not an embedded replica. Sessions and simulation authority
remain process-local. Production accepts only the owner's existing
`onesa-ai-robot-backend-druhustle.aws-eu-west-1.turso.io` database with separate
production credentials and `ROBOT_DEPLOYMENT_ENVIRONMENT=production`; staging
cannot use that identity. See [production configuration and qualification](../docs/engineering/DEPLOYMENT.md).
This does not migrate an existing local database. The runtime HTTP client is the already hash-locked httpx 0.28.1
(BSD-3-Clause); its existing transitive dependencies retain their pins/hashes.

For disposable Render staging, explicitly set `ROBOT_DEPLOYMENT_ENVIRONMENT=staging`
and `ROBOT_STORAGE_MODE=ephemeral-simulation`. Readiness reports that storage scope;
production rejects it. Use the [cloud release guide](../docs/engineering/AUTOMATED_QUALIFICATION.md)
for exact provider gates and environment settings. Optional edge telemetry is read-only,
synthetic, device-authenticated and independent of operator authority. It never grants
actuator control. `python -m mypy robot_api` now runs alongside lint/unit checks.

The bounded mission API (`/api/v1/missions`) extends the existing task journal;
no database migration or second execution authority is added. Catalog, plan,
exact-hash approval, owned detail/list and JSON export share existing session,
Origin/CSRF, password, gate and watchdog controls. The optional read-only
`/missions/context` import alone has a256KiB envelope (240KB source JSON,
16levels, three mapped metrics). `ROBOT_CONTEXT_IMPORT_ENABLED=false` is the
default. See the [inspection architecture](../docs/engineering/SIMULATION_ARCHITECTURE.md#versioned-industrial-inspection).
Production image builds bind `SOURCE_COMMIT` and `SOURCE_ARCHIVE_SHA256` labels;
verify the actual running image and qualified artifact independently of Compose
container labels. An unlabelled local test build is not release evidence.
