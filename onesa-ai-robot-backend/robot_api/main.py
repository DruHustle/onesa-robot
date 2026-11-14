"""Authenticated simulator and administrator permission for future physical controllers."""

import asyncio
import contextlib
import hashlib
import json
import logging
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.security import HTTPBearer
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from robot_api import humanoid, inspection
from robot_api import onesa_identity as identity
from robot_api.edge import EdgeMonitor, Telemetry
from robot_api.safety import Proposal, SafetyGate, SafetyViolation
from robot_api.security import PasswordVerifier, VerificationBusy, token_hash
from robot_api.turso import (
    COLUMNS,
    LEASE_READINESS_SECONDS,
    PRODUCTION_DATABASE,
    JournalAlreadyOwned,
    JournalFailure,
    TursoJournal,
    endpoint,
)

REQUEST_BODY_SECONDS = 5
JOURNAL_STARTUP_SECONDS = 90
# Renewal plus its bounded confirmation must finish within the lease-readiness window.
JOURNAL_RENEW_SECONDS = 5
JOURNAL_IDLE_RECOVERY_DELAYS = (30, 60, 120)
JOURNAL_LOG = logging.getLogger("robot_api.journal")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ROBOT_", extra="ignore", hide_input_in_errors=True, populate_by_name=True
    )
    public_origin: str = "http://localhost:3431"
    operator_name: str = Field(default="operator", min_length=1, max_length=64)
    operator_password_hash: str
    database_path: Path = Path("storage/robot.sqlite3")
    edge_device_id: str = ""
    edge_token_sha256: str = ""
    actuation_enabled: bool = False
    deployment_environment: Literal["local", "staging", "production"] = "local"
    storage_mode: Literal["persistent", "ephemeral-simulation", "turso"] = "persistent"
    turso_database_url: str = ""
    turso_auth_token: SecretStr = SecretStr("")
    session_seconds: int = Field(default=900, ge=60, le=3600)
    heartbeat_seconds: float = Field(default=3, ge=1, le=10)
    administrator_name: str = Field(default="", max_length=64)
    context_import_enabled: bool = False
    identity_enabled: bool = Field(default=False, validation_alias="ONESA_IDENTITY_ENABLED")
    identity_issuer: str = Field(default="", validation_alias="ONESA_IDENTITY_ISSUER")
    identity_client_id: str = Field(default="", validation_alias="ONESA_IDENTITY_CLIENT_ID")
    identity_client_secret: SecretStr = Field(
        default=SecretStr(""), validation_alias="ONESA_IDENTITY_CLIENT_SECRET"
    )
    identity_callback_url: str = Field(default="", validation_alias="ONESA_IDENTITY_CALLBACK_URL")

    @model_validator(mode="after")
    def safe_runtime(self):
        from urllib.parse import urlsplit

        url = urlsplit(self.public_origin)
        if self.administrator_name and self.administrator_name != self.operator_name:
            raise ValueError("Administrator must be the explicitly designated configured operator")
        if bool(self.edge_device_id) != bool(self.edge_token_sha256):
            raise ValueError("Configure both synthetic edge identity and token hash")
        if self.edge_device_id:
            import re

            if not re.fullmatch(
                r"synthetic-[a-z0-9-]{1,48}", self.edge_device_id
            ) or not re.fullmatch(r"[a-f0-9]{64}", self.edge_token_sha256):
                raise ValueError("Only authenticated synthetic telemetry devices are supported")
        if self.deployment_environment == "production" and self.storage_mode not in (
            "persistent",
            "turso",
        ):
            raise ValueError("Production requires approved persistent simulation storage")
        if self.storage_mode == "turso":
            if self.deployment_environment not in ("staging", "production"):
                raise ValueError("Remote task journal requires an explicit cloud environment")
            endpoint(self.turso_database_url)
            if (self.deployment_environment == "production") != (
                self.turso_database_url == PRODUCTION_DATABASE
            ):
                raise ValueError(
                    "Production and staging require separate approved journal identities"
                )
            token = self.turso_auth_token.get_secret_value()
            if not token or len(token) > 8192 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError("Configure a database-scoped Turso auth token")
        elif self.turso_database_url or self.turso_auth_token.get_secret_value():
            raise ValueError("Select turso storage explicitly; remote settings cannot be ignored")
        if self.actuation_enabled:
            raise ValueError(
                "Integrate and qualify the physical controller before activating hardware"
            )
        if (
            url.scheme not in ("http", "https")
            or not url.netloc
            or url.path not in ("", "/")
            or url.query
            or url.fragment
            or url.username
            or url.password
        ):
            raise ValueError("Set an exact HTTP(S) public origin")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("Remote operator access requires HTTPS")
        self.public_origin = self.public_origin.rstrip("/")
        if self.identity_enabled:
            if self.identity_callback_url != (
                self.public_origin + "/api/v1/auth/identity/callback"
            ):
                raise ValueError("Onesa identity callback must use this Robot public origin")
            self.identity_configuration()
        if (
            not self.operator_password_hash.startswith("scrypt$")
            or len(self.operator_password_hash.split("$")) != 3
        ):
            raise ValueError("Configure a generated operator password hash")
        _, salt, digest = self.operator_password_hash.split("$")
        if (
            len(salt) != 32
            or len(digest) != 128
            or any(c not in "0123456789abcdef" for c in salt + digest)
        ):
            raise ValueError("Invalid operator password hash")
        return self

    identity_audience: str = Field(default="", validation_alias="ONESA_IDENTITY_AUDIENCE")
    identity_public_jwks: str = Field(default="", validation_alias="ONESA_IDENTITY_JWKS")
    identity_mapping_issuer: str = Field(
        default="", validation_alias="ONESA_IDENTITY_MAPPING_ISSUER"
    )
    identity_account_url: str = Field(default="", validation_alias="ONESA_IDENTITY_ACCOUNT_URL")

    def identity_configuration(self) -> identity.IdentityConfig:
        return identity.IdentityConfig(
            issuer=self.identity_issuer,
            client_id=self.identity_client_id,
            client_secret=self.identity_client_secret.get_secret_value(),
            redirect_uri=self.identity_callback_url,
            product="robot",
            audience=self.identity_audience,
            public_jwks=self.identity_public_jwks,
            mapping_issuer=self.identity_mapping_issuer,
            account_url=self.identity_account_url,
            allow_local_http=self.deployment_environment == "local",
        )


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str | None = Field(default=None, min_length=1, max_length=256)
    confirmed: Literal[True] | None = None


class IdentityStart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    step_up: bool = False


class IdentityConfigurationResponse(BaseModel):
    authorization_endpoint: str | None = None
    enabled: bool
    account_url: str | None = None


class IdentityStartResponse(BaseModel):
    authorization_url: str


@dataclass(frozen=True)
class IdentityFlow:
    nonce: str
    browser_hash: str
    verifier: str = field(repr=False)
    expires: float
    previous_session: str | None = None
    subject: str | None = None
    replaced_session: str | None = None


@dataclass
class IdentitySession:
    access_token: str = field(repr=False)
    principal: identity.Identity


class MissionConfirmation(Confirmation):
    expected_plan_sha256: str = Field(strict=True, pattern=r"^[a-f0-9]{64}$")


class MissionDetail(BaseModel):
    id: str
    state: Literal["proposed", "running", "completed", "cancelled"]
    record: inspection.Record
    report: dict[str, Any]


class CatalogScenario(BaseModel):
    id: inspection.Scenario
    label: str
    source: Literal["synthetic scenario/v1"]


class MissionCatalog(BaseModel):
    version: Literal["1.0.0"]
    mode: Literal["simulation"]
    physical_actuation: Literal[False]
    assets: list[inspection.Asset]
    robots: list[inspection.RobotProfile]
    skills: list[inspection.Skill]
    scenarios: list[CatalogScenario]
    asset_context_import: Literal["enabled", "disabled_by_configuration"]
    model_inference: Literal["not_configured"]


class SessionResponse(BaseModel):
    csrf: str
    name: str
    role: Literal["viewer", "operator", "administrator"] = "operator"
    authentication_method: Literal["operator", "onesa"] = "operator"
    confirmation_expires_at: float | None = None
    global_administrator: bool = False


class PhysicalPermissionConfirmation(Confirmation):
    expected_revision: int = Field(ge=0, strict=True)


class PhysicalPermissionResponse(BaseModel):
    permission: Literal["allowed", "blocked"]
    revision: int
    expires_at: float | None
    controller_connection: Literal["not_connected"] = "not_connected"
    controller_readiness: Literal["not_ready"] = "not_ready"
    physical_operation: Literal["inactive"] = "inactive"
    physical_actuation: Literal[False] = False
    sampled_at: float


class Capabilities(BaseModel):
    edge_telemetry: Literal["simulation_connected", "not_connected"] = "not_connected"
    ros2: Literal["not_connected"] = "not_connected"
    camera: Literal["not_connected"] = "not_connected"
    stt: Literal["not_configured"] = "not_configured"
    vlm: Literal["not_configured"] = "not_configured"
    tts: Literal["not_configured"] = "not_configured"
    vla: Literal["not_configured"] = "not_configured"


class StatusResponse(BaseModel):
    mode: Literal["simulation"]
    physical_actuation: Literal[False]
    stopped: bool
    position_radians: float
    operator: str
    active_task: str | None
    capabilities: Capabilities
    sampled_at: float
    deployment_environment: Literal["local", "staging", "production"]


class TaskSummary(BaseModel):
    id: str
    state: Literal["proposed", "running", "completed", "cancelled"]
    reason: str
    created: float
    position: float


class TaskList(BaseModel):
    tasks: list[TaskSummary]


class TaskResponse(BaseModel):
    id: str
    state: Literal["proposed", "running"]


class ActionResponse(BaseModel):
    status: str
    physical_actuation: Literal[False] = False


class ServiceError(BaseModel):
    detail: str


ACTUATOR_UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {
        "description": "Simulation service unavailable, including an actuator feedback fault.",
        "model": ServiceError,
    }
}


class LogoutResponse(ActionResponse):
    identity_signed_out: bool | None = None


class State:
    def __init__(self, settings: Settings):
        self.humanoid = humanoid.Humanoid()
        self.journal = (
            TursoJournal(settings.turso_database_url, settings.turso_auth_token)
            if settings.storage_mode == "turso"
            else None
        )
        self.journal_dirty: dict[str, int] = {}
        self.journal_version = 0
        self.journal_lock = asyncio.Lock()
        self.journal_failed = False
        self.journal_recovery_allowed = False
        self.journal_recovery_attempts = 0
        self.journal_recovery_next_at = 0.0
        self.journal_initialized = self.journal is None
        self.journal_lease_confirmed_at = 0.0
        self.stop_epoch = 0
        self.physical_permission_owner: str | None = None
        self.physical_permission_expires = 0.0
        self.physical_permission_revision = 0
        self.mutation_lock = asyncio.Lock()
        self.inspection_contexts: dict[str, tuple[str, float, inspection.Asset]] = {}
        # Bearer tokens and sign-in transactions are process-local only. The task
        # journal never receives credentials; restart revokes all local sessions.
        self.identity_flows: dict[str, IdentityFlow] = {}
        self.identity_sessions: dict[str, IdentitySession] = {}
        self.identity_humanoid_owners: dict[UUID, str] = {}
        if not self.journal:
            settings.database_path.parent.mkdir(parents=True, exist_ok=True)
        # Transactions, routes and watchdog run on one application event-loop thread.
        # Reject foreign-thread access rather than sharing an implicit transaction.
        self.db = sqlite3.connect(":memory:" if self.journal else settings.database_path, timeout=2)
        self.db.row_factory = sqlite3.Row
        if not self.journal:
            settings.database_path.chmod(0o600)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY, csrf TEXT, expires REAL, name TEXT);
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY, owner TEXT, proposal TEXT, state TEXT,
                reason TEXT, created REAL, position REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS safety (
                id INTEGER PRIMARY KEY CHECK(id=1), stopped INTEGER NOT NULL);
            INSERT OR IGNORE INTO safety VALUES(1,1);
        """)
        # Process restart never resumes a command or retains execution authority.
        self.db.execute("DELETE FROM sessions")
        self.db.execute("UPDATE safety SET stopped=1 WHERE id=1")
        self.interrupt_inspections("process_restart")
        self.db.execute(
            "UPDATE tasks SET state='cancelled',reason='process_restart' "
            "WHERE state IN ('proposed','running')"
        )
        self.db.commit()
        self.settings, self.gate = settings, SafetyGate()
        self.active: dict | None = None
        self.position = 0.0
        self.failures: dict[str, deque] = defaultdict(deque)

    async def initialize_journal(self) -> None:
        if not self.journal:
            return
        rows = await self.journal.initialize()
        self.journal_lease_confirmed_at = time.monotonic()
        self.db.executemany(
            f"INSERT INTO tasks({','.join(COLUMNS)}) VALUES(?,?,?,?,?,?,?)",
            [tuple(row[col] for col in COLUMNS) for row in rows],
        )
        self.db.commit()
        # Restore history only, never operator sessions or execution authority.
        self.cancel("process_restart", stop=True)
        await self.flush_journal()
        await self.refresh_journal_lease()
        self.journal_initialized = True

    async def refresh_journal_lease(self) -> None:
        if not self.journal or self.journal_failed:
            raise sqlite3.OperationalError("Remote simulation journal requires recovery")
        try:
            await self.journal.renew()
            self.journal_lease_confirmed_at = time.monotonic()
        except sqlite3.Error as error:
            self.fail_journal(error, phase="lease_renew")
            raise

    async def refresh_journal_when_due(self, renewed: float) -> float:
        if self.journal and time.monotonic() - renewed >= JOURNAL_RENEW_SECONDS:
            await self.refresh_journal_lease()
            return time.monotonic()
        return renewed

    def dirty(self, task_id: str) -> None:
        if self.journal:
            self.journal_version += 1
            self.journal_dirty[task_id] = self.journal_version

    def fail_journal(
        self,
        error: sqlite3.Error | None = None,
        *,
        category: str = "sqlite",
        phase: str = "unknown",
        elapsed_ms: int = 0,
    ) -> None:
        first = not self.journal_failed
        idle_before_failure = (
            self.journal is not None
            and self.journal_initialized
            and self.active is None
            and self.humanoid.active is None
            and self.humanoid.pending is None
            and self.stopped
            and not self.journal_dirty
            and not self.settings.actuation_enabled
            and self.db.execute(
                "SELECT 1 FROM tasks WHERE state IN ('running','proposed') LIMIT 1"
            ).fetchone()
            is None
        )
        self.journal_failed = True
        self.cancel("storage_unavailable", stop=True)
        if first:
            diagnostic = (
                JournalFailure(
                    error.category,
                    error.phase,
                    error.elapsed_ms,
                    error.http_status,
                    transport_kind=error.transport_kind,
                    reconciliation=error.reconciliation,
                )
                if isinstance(error, JournalFailure)
                else JournalFailure(category, phase, elapsed_ms)
            )
            # Only a known idle renewal transport failure permits a new lease
            # attempt. Never reset this budget on repeated readiness/flush errors.
            self.journal_recovery_allowed = (
                idle_before_failure
                and diagnostic.phase == "lease_renew"
                and diagnostic.category in {"timeout", "transport"}
            )
            self.journal_recovery_attempts = 0
            self.journal_recovery_next_at = time.monotonic() + JOURNAL_IDLE_RECOVERY_DELAYS[0]
            JOURNAL_LOG.warning(
                "Simulation journal latched category=%s phase=%s elapsed_ms=%d http_status=%d "
                "transport_kind=%s reconciliation=%s",
                diagnostic.category,
                diagnostic.phase,
                diagnostic.elapsed_ms,
                diagnostic.http_status,
                diagnostic.transport_kind,
                diagnostic.reconciliation,
            )

    async def recover_idle_journal(self) -> bool:
        """Acquire new ownership after idle failure; never repair or replay tasks."""
        if (
            not self.journal
            or not self.journal_failed
            or not self.journal_recovery_allowed
            or time.monotonic() < self.journal_recovery_next_at
        ):
            return False
        async with self.mutation_lock, self.journal_lock:
            # A queued observer must honor a preceding attempt's new backoff.
            if time.monotonic() < self.journal_recovery_next_at:
                return False
            if (
                not self.journal_failed
                or not self.journal_recovery_allowed
                or self.journal_recovery_attempts >= len(JOURNAL_IDLE_RECOVERY_DELAYS)
                or self.active is not None
                or self.humanoid.active is not None
                or self.humanoid.pending is not None
                or not self.stopped
                or self.journal_dirty
                or self.settings.actuation_enabled
                or self.db.execute(
                    "SELECT 1 FROM tasks WHERE state IN ('running','proposed') LIMIT 1"
                ).fetchone()
                is not None
            ):
                self.journal_recovery_allowed = False
                return False
            previous = self.journal
            local_rows = self.db.execute(f"SELECT {','.join(COLUMNS)} FROM tasks").fetchall()
            if {row["id"]: dict(row) for row in local_rows} != previous.acknowledged:
                self.journal_recovery_allowed = False
                return False
            self.journal_recovery_attempts += 1
            attempt = self.journal_recovery_attempts
            replacement = TursoJournal(
                self.settings.turso_database_url, self.settings.turso_auth_token
            )
            activated = False
            try:
                async with asyncio.timeout(20):
                    rows = await replacement.initialize()
                    # A competing live owner cannot be displaced by initialize().
                    # Changed/new/missing/pending history requires operator recovery.
                    if (
                        any(row["state"] not in {"completed", "cancelled"} for row in rows)
                        or {row["id"]: row for row in rows} != previous.acknowledged
                    ):
                        self.journal_recovery_allowed = False
                        JOURNAL_LOG.warning("Idle journal recovery blocked: history_changed")
                        return False
                    await replacement.renew()
                    # No await between final safety checks and replacement. Mutation
                    # and journal locks exclude a late old-session decision/write.
                    if (
                        self.journal is not previous
                        or self.active is not None
                        or self.humanoid.active is not None
                        or self.humanoid.pending is not None
                        or self.journal_dirty
                        or not self.stopped
                        or self.settings.actuation_enabled
                        or self.db.execute(
                            "SELECT 1 FROM tasks WHERE state IN ('running','proposed') LIMIT 1"
                        ).fetchone()
                        is not None
                    ):
                        self.journal_recovery_allowed = False
                        return False
                    self.db.execute("DELETE FROM sessions")
                    self.identity_sessions.clear()
                    self.identity_flows.clear()
                    self.cancel("journal_recovered", stop=True)
                    self.journal = replacement
                    self.journal_failed = False
                    self.journal_recovery_allowed = False
                    self.journal_lease_confirmed_at = time.monotonic()
                    activated = True
                    JOURNAL_LOG.info("Idle journal recovery completed attempt=%d", attempt)
                    return True
            except JournalAlreadyOwned:
                JOURNAL_LOG.warning("Idle journal recovery blocked: live_owner")
            except (sqlite3.Error, TimeoutError) as error:
                if not isinstance(error, (JournalFailure, TimeoutError)) or (
                    isinstance(error, JournalFailure)
                    and error.category not in {"timeout", "transport"}
                ):
                    self.journal_recovery_allowed = False
                JOURNAL_LOG.warning("Idle journal recovery attempt unavailable attempt=%d", attempt)
            finally:
                if activated:
                    # The obsolete owner cannot write again. No lease DELETE or
                    # history operation is needed against the recovered journal.
                    with contextlib.suppress(Exception):
                        async with asyncio.timeout(2):
                            await previous.client.aclose()
                else:
                    await replacement.close()  # Conditional release of its own nonce only.
                    if attempt >= len(JOURNAL_IDLE_RECOVERY_DELAYS):
                        self.journal_recovery_allowed = False
                    else:
                        self.journal_recovery_next_at = (
                            time.monotonic() + JOURNAL_IDLE_RECOVERY_DELAYS[attempt]
                        )
            return False

    async def flush_journal(self) -> None:
        if not self.journal:
            return
        if self.journal_failed:
            raise sqlite3.OperationalError("Remote simulation journal requires recovery")
        async with self.journal_lock:
            if self.journal_failed:
                raise sqlite3.OperationalError("Remote simulation journal requires recovery")
            try:
                # Versions prevent a pending running write from clearing a later stop.
                while self.journal_dirty:
                    task_id, version = next(iter(self.journal_dirty.items()))
                    row = self.db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
                    await self.journal.save(dict(row))
                    if self.journal_dirty.get(task_id) == version:
                        self.journal_dirty.pop(task_id)
            except sqlite3.Error as error:
                self.fail_journal(error, phase="flush")
                raise

    @property
    def stopped(self) -> bool:
        return bool(self.db.execute("SELECT stopped FROM safety WHERE id=1").fetchone()[0])

    def block_physical_permission(self) -> None:
        # Revise even an already-blocked permission: invalidate an in-flight allow.
        self.physical_permission_owner = None
        self.physical_permission_expires = 0.0
        self.physical_permission_revision += 1

    def physical_permission(self) -> dict:
        return {
            "permission": "allowed" if self.physical_permission_owner else "blocked",
            "revision": self.physical_permission_revision,
            "expires_at": self.physical_permission_expires
            if self.physical_permission_owner
            else None,
            "sampled_at": time.time(),
            # Administrative permission does not fabricate a connected controller.
            "controller_connection": "not_connected",
            "controller_readiness": "not_ready",
            "physical_operation": "inactive",
            "physical_actuation": False,
        }

    def save_inspection(
        self, identifier: str, proposal: Proposal, record: inspection.Record
    ) -> Proposal:
        updated = proposal.model_copy(update={"inspection": inspection.bounded(record)})
        raw = updated.model_dump_json()
        if len(raw.encode()) > 16384:
            raise SafetyViolation("Inspection exceeds task journal capacity")
        self.db.execute("UPDATE tasks SET proposal=? WHERE id=?", (raw, identifier))
        self.dirty(identifier)
        return updated

    def interrupt_inspection(self, row: sqlite3.Row, reason: str) -> None:
        # Legacy joint records keep their original JSON and cancellation behavior.
        # Only this versioned optional inspection extension needs interpretation.
        try:
            payload = json.loads(row["proposal"])
        except ValueError:
            return
        if not isinstance(payload, dict) or payload.get("inspection") is None:
            return
        proposal = Proposal.model_validate_json(row["proposal"])
        record = proposal.inspection
        if record is None:
            return
        active = getattr(self, "active", None)
        elapsed = record.elapsed_seconds
        if active is not None and active["id"] == row["id"]:
            # Prefer the last in-memory observation; a journal failure must not
            # erase already collected evidence or pretend an execution completed.
            proposal = active["proposal"]
            record = proposal.inspection
            if record is None:
                raise SafetyViolation("Active inspection lost its approved plan")
            elapsed = max(0.0, time.monotonic() - active["started"])
        updated = inspection.interrupt(record, reason, time.time(), elapsed)
        if updated != record:
            self.save_inspection(row["id"], proposal, updated)

    def interrupt_inspections(self, reason: str) -> None:
        for row in self.db.execute("SELECT * FROM tasks WHERE state IN ('running','proposed')"):
            self.interrupt_inspection(row, reason)

    def tick_inspection(self, active: dict) -> None:
        proposal: Proposal = active["proposal"]
        record = proposal.inspection
        if record is None:
            raise SafetyViolation("Missing inspection record")
        elapsed = max(0.0, time.monotonic() - active["started"])
        try:
            inspection.validate(
                record,
                proposal.command_id,
                active["start"],
                proposal.target_radians,
                proposal.duration_seconds,
            )
            self.position, _, _ = self.gate.sample(
                active["start"],
                proposal.target_radians,
                record.plan.motion_duration_seconds,
                elapsed - record.plan.motion_starts_at_seconds,
            )
            updated, failure = inspection.advance(record, elapsed, time.time())
        except SafetyViolation:
            self.cancel("safety_gate", stop=True)
            return
        except ValueError:
            self.cancel("inspection_integrity", stop=True)
            return
        # Compute motion in memory. Persist only completed steps and terminal
        # transitions, never animation frames or every telemetry sample.
        changed = updated.events != record.events or updated.outcome != record.outcome
        active["proposal"] = proposal.model_copy(update={"inspection": updated})
        if failure:
            self.cancel(failure, stop=True)
            return
        if changed:
            active["proposal"] = self.save_inspection(active["id"], proposal, updated)
            complete = updated.outcome in ("completed_normal", "completed_findings")
            self.db.execute(
                "UPDATE tasks SET state=?,reason=?,position=? WHERE id=?",
                (
                    "completed" if complete else "running",
                    updated.outcome,
                    self.position,
                    active["id"],
                ),
            )
            self.db.commit()
            if complete:
                self.active = None

    def cancel(self, reason: str, stop: bool = False) -> None:
        self.humanoid.stop(reason)
        self.interrupt_inspections(reason)
        self.active = None
        for row in self.db.execute("SELECT id FROM tasks WHERE state IN ('running','proposed')"):
            self.dirty(row[0])
        self.db.execute(
            "UPDATE tasks SET state='cancelled',reason=? WHERE state IN ('running','proposed')",
            (reason,),
        )
        if stop:
            self.block_physical_permission()
            self.stop_epoch += 1
            self.db.execute("UPDATE safety SET stopped=1 WHERE id=1")
        self.db.commit()
        self.active = None

    def tick(self) -> None:
        whole = self.humanoid
        owner = whole.active.owner if whole.active else whole.pending[0] if whole.pending else None
        if owner:
            authority = self.db.execute(
                "SELECT expires FROM sessions WHERE token=? AND expires>?", (owner, time.time())
            ).fetchone()
            if (
                self.stopped
                or authority is None
                or (
                    whole.active
                    and time.monotonic() - whole.active.heartbeat > self.settings.heartbeat_seconds
                )
            ):
                self.cancel("operator_connection_lost", stop=True)
            elif whole.pending and time.time() - whole.pending[1].request.issued_at > 15:
                whole.stop("proposal_expired")
                if whole.faulted:
                    self.cancel("actuator_failure", stop=True)
            else:
                try:
                    whole.tick(time.monotonic())
                except humanoid.ActuatorFault:
                    self.cancel("actuator_failure", stop=True)
        if self.physical_permission_owner is not None:
            permitted = self.db.execute(
                "SELECT expires FROM sessions WHERE token=? AND expires>?",
                (self.physical_permission_owner, time.time()),
            ).fetchone()
            if permitted is None or self.physical_permission_expires <= time.time():
                self.block_physical_permission()
        active = self.active
        if not active:
            return
        session = self.db.execute(
            "SELECT expires FROM sessions WHERE token=?", (active["session"],)
        ).fetchone()
        if (
            self.stopped
            or session is None
            or session[0] <= time.time()
            or time.monotonic() - active["heartbeat"] > self.settings.heartbeat_seconds
        ):
            self.cancel("operator_connection_lost", stop=True)
            return
        proposal = active["proposal"]
        if proposal.inspection is not None:
            self.tick_inspection(active)
            return
        try:
            self.position, _, _ = self.gate.sample(
                active["start"],
                proposal.target_radians,
                proposal.duration_seconds,
                time.monotonic() - active["started"],
            )
        except SafetyViolation:
            self.cancel("safety_gate", stop=True)
            return
        completed = time.monotonic() - active["started"] >= proposal.duration_seconds
        self.db.execute(
            "UPDATE tasks SET state=?,position=? WHERE id=?",
            ("completed" if completed else "running", self.position, active["id"]),
        )
        self.db.commit()
        if completed:
            self.dirty(active["id"])
            self.active = None


Session = dict[str, Any]


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    identity_config = config.identity_configuration() if config.identity_enabled else None
    local_simulator = urlsplit(config.public_origin).hostname in {"localhost", "127.0.0.1", "::1"}

    @asynccontextmanager
    async def lifespan(app):
        state = State(config)
        try:
            await state.initialize_journal()
        except JournalAlreadyOwned:
            # Process liveness lets Render retire the prior instance. All application
            # routes stay unavailable until this instance owns and restores history.
            pass
        except BaseException:
            state.db.close()
            if state.journal:
                await state.journal.close()
            raise
        app.state.sim = state
        app.state.edge = EdgeMonitor(config.edge_device_id, config.edge_token_sha256)
        app.state.passwords = PasswordVerifier()

        async def watch():
            while True:
                await asyncio.sleep(0.05)
                try:
                    state.tick()
                except (sqlite3.Error, SafetyViolation):
                    state.active = None
                    with contextlib.suppress(sqlite3.Error):
                        state.cancel("watchdog_failure", stop=True)
                    raise

        async def journal_watch():
            # Cloud writes occur on events, not on the 20 Hz simulation loop.
            renewed = time.monotonic()
            startup_deadline = renewed + JOURNAL_STARTUP_SECONDS
            while True:
                await asyncio.sleep(0.1)
                if state.journal_failed:
                    if not state.journal_recovery_allowed:
                        return
                    if await state.recover_idle_journal():
                        renewed = time.monotonic()
                    continue
                if not state.journal_initialized:
                    if time.monotonic() >= startup_deadline:
                        state.fail_journal(
                            category="startup_deadline",
                            phase="initialize",
                            elapsed_ms=int((time.monotonic() - renewed) * 1000),
                        )
                        return
                    try:
                        await state.initialize_journal()
                        renewed = time.monotonic()
                    except JournalAlreadyOwned:
                        await asyncio.sleep(1)
                    except sqlite3.Error as error:
                        state.fail_journal(error, phase="initialize")
                        return
                    continue
                try:
                    await state.flush_journal()
                    renewed = await state.refresh_journal_when_due(renewed)
                except sqlite3.Error as error:
                    state.fail_journal(error, phase="flush")
                    # The next loop may attempt only the bounded, idle recovery.
                    # Active, dirty, ownership/schema and exhausted failures stay latched.

        journal_task = asyncio.create_task(journal_watch()) if state.journal else None
        task = asyncio.create_task(watch())
        app.state.watch = task
        try:
            yield
        finally:
            try:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            finally:
                try:
                    if journal_task:
                        journal_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await journal_task
                    state.cancel("process_shutdown", stop=True)
                    with contextlib.suppress(sqlite3.Error):
                        await state.flush_journal()
                finally:
                    state.db.close()
                    if state.journal:
                        await state.journal.close()

    app = FastAPI(
        title="Onesa Robot Simulation API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(humanoid.ActuatorFault)
    async def actuator_fault(request: Request, error: humanoid.ActuatorFault):
        sim = request.app.state.sim
        if not sim.stopped or sim.physical_permission_owner:
            sim.cancel("actuator_failure", stop=True)
        return JSONResponse({"detail": str(error)}, status_code=503)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if (
                request.url.path != "/api/v1/edge/telemetry"
                and request.headers.get("origin") != config.public_origin
            ):
                return Response(status_code=403)
            body = b""
            body_limit = 262144 if request.url.path == "/api/v1/missions/context" else 16384
            try:
                # A total deadline also bounds a client that keeps trickling bytes.
                async with asyncio.timeout(REQUEST_BODY_SECONDS):
                    async for chunk in request.stream():
                        if len(body) + len(chunk) > body_limit:
                            return Response(status_code=413, headers={"Cache-Control": "no-store"})
                        body += chunk
            except TimeoutError:
                return Response(status_code=408, headers={"Cache-Control": "no-store"})
            request._body = body
        try:
            sim = request.app.state.sim
            if request.url.path != "/api/v1/health" and not sim.journal_initialized:
                return Response(status_code=503, headers={"Cache-Control": "no-store"})
            if sim.journal and request.method not in ("GET", "HEAD", "OPTIONS"):
                # Serialize operator mutations across acknowledgement yields. Stop
                # and heartbeat are exempt so cloud latency cannot delay local safety.
                if request.url.path in (
                    "/api/v1/stop",
                    "/api/v1/heartbeat",
                    "/api/v1/humanoid/heartbeat",
                    "/api/v1/auth/logout",
                    "/api/v1/admin/physical-operation/block",
                ) or request.url.path.endswith("/cancel"):
                    response = await call_next(request)
                    await sim.flush_journal()
                else:
                    async with sim.mutation_lock:
                        if sim.journal_failed:
                            raise sqlite3.OperationalError("Remote journal unavailable")
                        response = await call_next(request)
                        await sim.flush_journal()
            else:
                response = await call_next(request)
        except sqlite3.Error:
            response = Response(status_code=503)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
            }
        )
        return response

    def state(request: Request, require_watchdog: bool = False) -> State:
        sim = request.app.state.sim
        if request.app.state.watch.done():
            if sim.physical_permission_owner:
                sim.block_physical_permission()
            if sim.active or sim.humanoid.active or sim.humanoid.pending:
                sim.cancel("watchdog_failure", stop=True)
            if require_watchdog:
                raise HTTPException(503, "Safety watchdog unavailable")
        # Enforce expired authority before a late heartbeat can refresh it, even
        # when the background tick was delayed by event-loop scheduling.
        sim.tick()
        return sim

    async def password_valid(request: Request, password: str) -> bool:
        try:
            return await request.app.state.passwords.verify(password, config.operator_password_hash)
        except VerificationBusy:
            raise HTTPException(429, "Password verification busy; retry later") from None

    def remove_session(sim: State, key: str) -> IdentitySession | None:
        sim.inspection_contexts = {
            identifier: value
            for identifier, value in sim.inspection_contexts.items()
            if value[0] != key
        }
        if sim.physical_permission_owner == key:
            sim.block_physical_permission()
        if (
            (sim.active and sim.active["session"] == key)
            or (sim.humanoid.active and sim.humanoid.active.owner == key)
            or (sim.humanoid.pending and sim.humanoid.pending[0] == key)
        ):
            sim.cancel("session_revoked", stop=True)
        sim.db.execute("DELETE FROM sessions WHERE token=?", (key,))
        sim.db.commit()
        return sim.identity_sessions.pop(key, None)

    def identity_owner(principal: identity.Identity) -> str:
        # Email is a display/contact attribute, never an account-linking key.
        namespace = (identity_config.mapping_issuer if identity_config else "") or principal.issuer
        value = namespace + "\x00" + principal.subject
        return "sso:" + hashlib.sha256(value.encode()).hexdigest()

    def identity_role(
        principal: identity.Identity,
    ) -> Literal["viewer", "operator", "administrator"]:
        if principal.global_admin or principal.product_roles.get("robot") == "admin":
            return "administrator"
        return "operator" if principal.product_roles.get("robot") == "operator" else "viewer"

    async def confirm(sim: State, request: Request, value: Confirmation) -> None:
        if identity_config:
            current = await session(request)
            principal = sim.identity_sessions[current["token"]].principal
            if value.confirmed is not True:
                raise HTTPException(403, "Review and explicitly confirm this operation")
            if not principal.fresh(mfa=True):
                raise HTTPException(
                    403, "Confirm your Onesa identity with MFA before preparing this operation"
                )
            return
        failures = sim.failures["confirmation"]
        while failures and failures[0] < time.monotonic() - 60:
            failures.popleft()
        if len(failures) >= 5:
            raise HTTPException(429, "Confirmation attempts exceeded")
        if not value.password or not await password_valid(request, value.password):
            failures.append(time.monotonic())
            raise HTTPException(403, "Operator confirmation required")

    async def session(request: Request) -> Session:
        sim = state(request)
        key = token_hash(request.cookies.get("robot_session", ""))
        row = sim.db.execute(
            "SELECT * FROM sessions WHERE token=? AND expires>?", (key, time.time())
        ).fetchone()
        if row is None:
            sim.identity_sessions.pop(key, None)
            raise HTTPException(401, "Sign in required")
        if request.method != "GET" and not secrets.compare_digest(
            request.headers.get("x-csrf-token", ""), row["csrf"]
        ):
            raise HTTPException(403, "CSRF verification failed")
        result = dict(row)
        if not identity_config:
            if not local_simulator and request.url.path not in {
                "/api/v1/stop",
                "/api/v1/auth/logout",
            }:
                raise HTTPException(401, "Sign in through Onesa Company Portal")
            return result
        linked = sim.identity_sessions.get(key)
        if linked is None:
            raise HTTPException(401, "Sign in with your Onesa account")
        if request.url.path == "/api/v1/auth/logout":
            # Ending this locally authenticated session cannot grant access. The
            # revoke endpoint separately checks the token and confidential client.
            return result
        # Stop takes effect after local session/CSRF validation, before any identity
        # network wait. Its independent watchdog also continues during that wait.
        if request.url.path == "/api/v1/stop":
            sim.cancel("operator_stop", stop=True)
        elif request.url.path == "/api/v1/admin/physical-operation/block":
            if identity_role(linked.principal) == "administrator":
                sim.block_physical_permission()
        try:
            principal = await asyncio.to_thread(
                identity.validate_session, identity_config, linked.access_token
            )
            if identity_owner(principal) != row["name"]:
                raise identity.IdentityError("identity_rejected", "Sign in again")
        except identity.IdentityError as error:
            if error.status != 503:
                remove_session(sim, key)
            else:
                if sim.physical_permission_owner == key:
                    sim.block_physical_permission()
                if (
                    (sim.active and sim.active["session"] == key)
                    or (sim.humanoid.active and sim.humanoid.active.owner == key)
                    or (sim.humanoid.pending and sim.humanoid.pending[0] == key)
                ):
                    sim.cancel("identity_unavailable", stop=True)
            raise HTTPException(error.status, error.message) from None
        # A concurrent logout/expiry while the identity request was awaiting must
        # not resurrect the original local session or permit another mutation.
        current = sim.db.execute(
            "SELECT 1 FROM sessions WHERE token=? AND expires>?", (key, time.time())
        ).fetchone()
        if current is None or sim.identity_sessions.get(key) is not linked:
            raise HTTPException(401, "Sign in required")
        linked.principal = principal
        current_role = identity_role(principal)
        if current_role != "administrator" and sim.physical_permission_owner == key:
            sim.block_physical_permission()
        if current_role == "viewer" and (
            (sim.active and sim.active["session"] == key)
            or (sim.humanoid.active and sim.humanoid.active.owner == key)
            or (sim.humanoid.pending and sim.humanoid.pending[0] == key)
        ):
            sim.cancel("operator_access_revoked", stop=True)
        if (
            current_role == "viewer"
            and request.method not in ("GET", "HEAD")
            and request.url.path
            not in (
                "/api/v1/auth/logout",
                "/api/v1/auth/identity/start",
                "/api/v1/stop",
                "/api/v1/heartbeat",
            )
        ):
            raise HTTPException(403, "Operator access is required for simulation changes")
        result.update(
            identity_role=current_role,
            display_name=principal.name,
            authentication_method="onesa",
            global_administrator=principal.global_admin,
            confirmation_expires_at=(
                principal.auth_time + 300 if principal.fresh(mfa=True) else None
            ),
        )
        return result

    Operator = Annotated[Session, Depends(session)]

    def role(operator: Session) -> Literal["viewer", "operator", "administrator"]:
        if operator.get("authentication_method") == "onesa":
            return operator["identity_role"]
        return (
            "administrator"
            if config.administrator_name and operator["name"] == config.administrator_name
            else "operator"
        )

    async def administrator(operator: Operator) -> Session:
        if role(operator) != "administrator":
            raise HTTPException(403, "Administrator authorization required")
        return operator

    Administrator = Annotated[Session, Depends(administrator)]

    async def administrator_check(request: Request) -> None:
        if role(await session(request)) != "administrator":
            raise HTTPException(403, "Administrator authorization required")

    @app.get("/api/v1/admin/physical-operation", response_model=PhysicalPermissionResponse)
    async def physical_permission(request: Request, administrator: Administrator):
        return state(request).physical_permission()

    @app.post(
        "/api/v1/admin/physical-operation/allow",
        response_model=PhysicalPermissionResponse,
        responses=ACTUATOR_UNAVAILABLE,
    )
    async def allow_physical_permission(
        value: PhysicalPermissionConfirmation, request: Request, administrator: Administrator
    ):
        sim = state(request, require_watchdog=True)
        await confirm(sim, request, value)
        if identity_config:
            await administrator_check(request)
        # Password hashing yields. Recheck revocation, Stop and a concurrent block.
        sim = state(request, require_watchdog=True)
        current = sim.db.execute(
            "SELECT expires FROM sessions WHERE token=? AND expires>?",
            (administrator["token"], time.time()),
        ).fetchone()
        if current is None:
            raise HTTPException(401, "Sign in required")
        if value.expected_revision != sim.physical_permission_revision:
            raise HTTPException(409, "Operation permission changed; review its current state")
        if (
            not sim.stopped
            or sim.active
            or sim.humanoid.active
            or sim.humanoid.pending
            or sim.db.execute(
                "SELECT count(*) FROM tasks WHERE state IN ('proposed','running')"
            ).fetchone()[0]
        ):
            raise HTTPException(
                409, "Confirm an already-stopped, idle simulator before allowing operation"
            )
        sim.humanoid.verified_positions()
        sim.physical_permission_owner = administrator["token"]
        sim.physical_permission_expires = float(current[0])
        sim.physical_permission_revision += 1
        return sim.physical_permission()

    @app.post("/api/v1/admin/physical-operation/block", response_model=PhysicalPermissionResponse)
    async def block_physical_permission(request: Request, administrator: Administrator):
        sim = state(request)
        sim.block_physical_permission()
        return sim.physical_permission()

    @app.get("/api/v1/edge/challenge", dependencies=[Depends(HTTPBearer(auto_error=False))])
    async def edge_challenge(request: Request):
        monitor = request.app.state.edge
        monitor.authorize(request.headers.get("authorization", ""))
        return {
            "boot_challenge": monitor.challenge,
            "next_sequence": monitor.sequence + 1,
            "version": 1,
        }

    @app.post("/api/v1/edge/telemetry", dependencies=[Depends(HTTPBearer(auto_error=False))])
    async def edge_telemetry(value: Telemetry, request: Request):
        monitor = request.app.state.edge
        monitor.authorize(request.headers.get("authorization", ""))
        monitor.receive(value)
        return {"status": "received", "scope": "simulation-telemetry-only"}

    @app.get("/api/v1/edge/audit")
    async def edge_audit(request: Request, operator: Operator):
        return {"events": list(request.app.state.edge.audit), "retention": "bounded-process-memory"}

    @app.get("/api/v1/health")
    def health(request: Request):
        sim = request.app.state.sim
        if not sim.journal_initialized and sim.journal_failed:
            raise HTTPException(503, "Simulation startup requires recovery")
        return {"status": "live", "mode": "simulation"}

    @app.get("/api/v1/ready")
    async def ready(request: Request):
        sim = state(request)
        if request.app.state.watch.done():
            raise HTTPException(503, "Safety watchdog unavailable")
        if sim.journal:
            if sim.journal_failed:
                raise HTTPException(503, "Remote simulation journal requires recovery")
            # Anonymous health traffic must not consume remote connections. The
            # background lease renewal supplies a bounded, real database proof.
            if time.monotonic() - sim.journal_lease_confirmed_at > LEASE_READINESS_SECONDS:
                sim.fail_journal(
                    category="lease_stale",
                    phase="ready",
                    elapsed_ms=int((time.monotonic() - sim.journal_lease_confirmed_at) * 1000),
                )
                raise HTTPException(503, "Remote simulation journal lease check expired")
        if sim.db.execute("SELECT count(*) FROM safety WHERE id=1").fetchone()[0] != 1:
            raise HTTPException(503, "Safety state unavailable")
        return {
            "status": "ready",
            "mode": "simulation",
            "physical_actuation": False,
            "storage_mode": config.storage_mode,
            "deployment_environment": config.deployment_environment,
        }

    @app.get(
        "/api/v1/auth/identity/config",
        response_model=IdentityConfigurationResponse,
        response_model_exclude_unset=True,
    )
    async def identity_configuration():
        issuer = urlsplit(identity_config.issuer) if identity_config else None
        return {
            "enabled": identity_config is not None,
            **(
                {"authorization_endpoint": identity_config.issuer + "authorize"}
                if identity_config and identity_config.audience
                else {}
            ),
            "account_url": (
                identity_config.account_url or f"{issuer.scheme}://{issuer.netloc}/account"
            )
            if issuer and identity_config
            else None,
        }

    @app.post("/api/v1/auth/identity/start", response_model=IdentityStartResponse)
    async def identity_start(value: IdentityStart, request: Request, response: Response):
        if identity_config is None:
            raise HTTPException(404, "Onesa sign-in is not configured")
        sim = state(request)
        previous = await session(request) if value.step_up else None
        if previous is not None and (
            sim.active
            or sim.humanoid.active
            or sim.humanoid.pending
            or sim.db.execute(
                "SELECT count(*) FROM tasks WHERE state='proposed' AND owner=?",
                (previous["name"],),
            ).fetchone()[0]
        ):
            raise HTTPException(409, "Stop or cancel pending work before confirming your identity")
        now = time.time()
        old_key = token_hash(request.cookies.get("robot_session", ""))
        old_session = sim.db.execute(
            "SELECT token FROM sessions WHERE token=? AND expires>?", (old_key, now)
        ).fetchone()
        # Strict session cookies may be absent on the cross-site callback. Bind
        # retirement here; only a step-up also binds the replacement's subject.
        replaced_session = old_key if old_session and old_key in sim.identity_sessions else None
        sim.identity_flows = {
            key: flow for key, flow in sim.identity_flows.items() if flow.expires > now
        }
        attempts = sim.failures["identity_start"]
        while attempts and attempts[0] < time.monotonic() - 60:
            attempts.popleft()
        if len(sim.identity_flows) >= 64 or len(attempts) >= 30:
            raise HTTPException(429, "Sign-in is busy. Please wait before trying again")
        attempts.append(time.monotonic())
        state_value, nonce, verifier = identity.new_transaction()
        browser = secrets.token_urlsafe(32)
        sim.identity_flows[token_hash(state_value)] = IdentityFlow(
            nonce=nonce,
            browser_hash=token_hash(browser),
            verifier=verifier,
            expires=now + 600,
            previous_session=previous["token"] if previous else None,
            subject=(
                sim.identity_sessions[previous["token"]].principal.subject if previous else None
            ),
            replaced_session=replaced_session,
        )
        response.set_cookie(
            "robot_identity",
            browser,
            max_age=600,
            httponly=True,
            secure=config.public_origin.startswith("https:"),
            samesite="lax",
            path="/api/v1/auth/identity",
        )
        return {
            "authorization_url": identity.authorization_url(
                identity_config, state_value, nonce, verifier, reauthenticate=value.step_up
            )
        }

    @app.get("/api/v1/auth/identity/callback", include_in_schema=False)
    async def identity_callback(request: Request):
        if identity_config is None:
            raise HTTPException(404, "Onesa sign-in is not configured")
        sim = state(request)
        response = RedirectResponse("/console", status_code=303)
        response.delete_cookie("robot_identity", path="/api/v1/auth/identity")
        keys = list(request.query_params.keys())
        if (
            set(keys) - {"code", "state", "error", "error_description", "iss"}
            or any(len(request.query_params.getlist(key)) != 1 for key in keys)
            or len(str(request.url.query)) > 8192
        ):
            response.headers["location"] = "/console?identity=failed"
            return response
        state_value = request.query_params.get("state", "")
        flow = sim.identity_flows.pop(token_hash(state_value), None)
        if (
            flow is None
            or flow.expires <= time.time()
            or not secrets.compare_digest(
                flow.browser_hash, token_hash(request.cookies.get("robot_identity", ""))
            )
            or request.query_params.get("iss", identity_config.issuer) != identity_config.issuer
            or "error" in request.query_params
        ):
            response.headers["location"] = "/console?identity=failed"
            return response
        access_token: str | None = None
        try:
            if identity_config.audience:
                from robot_api.managed_oidc import exchange

                access_token = await asyncio.to_thread(
                    exchange,
                    identity_config,
                    request.query_params.get("code", ""),
                    flow.verifier,
                    flow.nonce,
                )
            else:
                access_token = await asyncio.to_thread(
                    identity.exchange_code,
                    identity_config,
                    request.query_params.get("code", ""),
                    flow.verifier,
                )
            principal = await asyncio.to_thread(
                identity.validate_session, identity_config, access_token
            )
            if flow.previous_session:
                previous = sim.identity_sessions.get(flow.previous_session)
                live = sim.db.execute(
                    "SELECT 1 FROM sessions WHERE token=? AND expires>?",
                    (flow.previous_session, time.time()),
                ).fetchone()
                if (
                    not previous
                    or live is None
                    or principal.subject != flow.subject
                    or previous.principal.subject != principal.subject
                    or not principal.fresh(mfa=True)
                ):
                    raise identity.IdentityError("identity_rejected", "Confirm the same account")
            for row in sim.db.execute(
                "SELECT token FROM sessions WHERE expires<=?", (time.time(),)
            ):
                sim.identity_sessions.pop(row["token"], None)
            sim.db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
            owner = identity_owner(principal)
            count = sim.db.execute("SELECT count(*) FROM sessions").fetchone()[0]
            owned = sim.db.execute(
                "SELECT count(*) FROM sessions WHERE name=?", (owner,)
            ).fetchone()[0]
            replaced = sim.db.execute(
                "SELECT name FROM sessions WHERE token=?", (flow.replaced_session,)
            ).fetchone()
            if replaced:
                count -= 1
                owned -= replaced["name"] == owner
            if count >= 100 or owned >= 5:
                raise identity.IdentityError("identity_busy", "Session limit reached", 429)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            expires = min(time.time() + config.session_seconds, principal.expires_at)
            key = token_hash(token)
            sim.db.execute("INSERT INTO sessions VALUES(?,?,?,?)", (key, csrf, expires, owner))
            sim.db.commit()
            sim.identity_sessions[key] = IdentitySession(access_token, principal)
            if flow.replaced_session:
                previous = remove_session(sim, flow.replaced_session)
                if previous:
                    with contextlib.suppress(identity.IdentityError):
                        await asyncio.to_thread(
                            identity.revoke_session, identity_config, previous.access_token
                        )
            response.set_cookie(
                "robot_session",
                token,
                max_age=max(1, int(expires - time.time())),
                httponly=True,
                secure=config.public_origin.startswith("https:"),
                samesite="strict",
                path="/api/v1",
            )
        except identity.IdentityError as error:
            if access_token:
                with contextlib.suppress(identity.IdentityError):
                    await asyncio.to_thread(identity.revoke_session, identity_config, access_token)
            outcome = (
                "access"
                if error.status == 403
                else ("unavailable" if error.status == 503 else "failed")
            )
            response.headers["location"] = "/console?identity=" + outcome
        return response

    @app.post("/api/v1/auth/login", response_model=SessionResponse)
    async def login(value: Login, request: Request, response: Response):
        if identity_config or not local_simulator:
            raise HTTPException(403, "Use your Onesa account to sign in")
        sim = state(request)
        # No forwarded header trust. A single configured operator; failures are global
        # as well as per network peer, preventing spoofed peer/rate-limit bypass.
        failures = sim.failures["operator"]
        while failures and failures[0] < time.monotonic() - 60:
            failures.popleft()
        if len(failures) >= 5:
            raise HTTPException(429, "Try again later")
        valid = await password_valid(request, value.password)
        if value.name != config.operator_name or not valid:
            failures.append(time.monotonic())
            raise HTTPException(401, "Invalid credentials")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sim.db.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
        if sim.db.execute("SELECT count(*) FROM sessions").fetchone()[0] >= 10:
            raise HTTPException(429, "Session limit reached")
        sim.db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)",
            (token_hash(token), csrf, time.time() + config.session_seconds, value.name),
        )
        sim.db.commit()
        response.set_cookie(
            "robot_session",
            token,
            max_age=config.session_seconds,
            httponly=True,
            secure=config.public_origin.startswith("https:"),
            samesite="strict",
            path="/api/v1",
        )
        return {"csrf": csrf, "name": value.name, "role": role({"name": value.name})}

    @app.get("/api/v1/auth/me", response_model=SessionResponse)
    async def me(operator: Operator):
        return {
            "csrf": operator["csrf"],
            "name": operator.get("display_name", operator["name"]),
            "role": role(operator),
            "authentication_method": operator.get("authentication_method", "operator"),
            "confirmation_expires_at": operator.get("confirmation_expires_at"),
            "global_administrator": operator.get("global_administrator", False),
        }

    @app.post("/api/v1/auth/logout", response_model=LogoutResponse)
    async def logout(request: Request, response: Response, operator: Operator):
        sim = state(request)
        linked = remove_session(sim, operator["token"])
        identity_signed_out = None
        if linked and identity_config:
            identity_signed_out = False
            try:
                identity_signed_out = await asyncio.to_thread(
                    identity.revoke_session,
                    identity_config,
                    linked.access_token,
                    all_sessions=True,
                )
            except identity.IdentityError:
                pass
        response.delete_cookie("robot_session", path="/api/v1")
        return {"status": "signed_out", "identity_signed_out": identity_signed_out}

    @app.get("/api/v1/status", response_model=StatusResponse)
    async def status(request: Request, operator: Operator):
        sim = state(request)
        return {
            "mode": "simulation",
            "physical_actuation": False,
            "stopped": sim.stopped,
            "position_radians": sim.position,
            "operator": operator.get("display_name", operator["name"]),
            "active_task": sim.active["id"] if sim.active else None,
            "sampled_at": time.time(),
            "deployment_environment": config.deployment_environment,
            "capabilities": {
                "edge_telemetry": request.app.state.edge.status(),
                "ros2": "not_connected",
                "camera": "not_connected",
                "stt": "not_configured",
                "vlm": "not_configured",
                "tts": "not_configured",
                "vla": "not_configured",
            },
        }

    @app.get("/api/v1/tasks", response_model=TaskList)
    async def tasks(request: Request, operator: Operator):
        sim = state(request)
        await sim.flush_journal()
        rows = (
            state(request)
            .db.execute(
                "SELECT id,state,reason,created,position FROM tasks WHERE owner=? "
                "ORDER BY created DESC LIMIT 50",
                (operator["name"],),
            )
            .fetchall()
        )
        return {"tasks": [dict(row) for row in rows]}

    async def create_task(value: Proposal, request: Request, operator: Session):
        sim = state(request, require_watchdog=True)
        if sim.journal_failed:
            raise HTTPException(503, "Remote simulation journal requires recovery")
        if sim.stopped:
            raise HTTPException(409, "Reset the simulation stop state first")
        if sim.humanoid.active or sim.humanoid.pending:
            raise HTTPException(409, "Complete or cancel the humanoid motion first")
        try:
            sim.gate.validate(value, sim.position)
        except SafetyViolation as exc:
            raise HTTPException(422, str(exc)) from None
        if sim.db.execute("SELECT count(*) FROM tasks").fetchone()[0] >= 10000:
            raise HTTPException(429, "History capacity reached; operator retention review required")
        if sim.db.execute("SELECT count(*) FROM tasks WHERE state='proposed'").fetchone()[0] >= 5:
            raise HTTPException(429, "Pending proposal limit reached")
        try:
            sim.db.execute(
                "INSERT INTO tasks(id,owner,proposal,state,reason,created) "
                "VALUES(?,?,?,'proposed','awaiting_operator',?)",
                (str(value.command_id), operator["name"], value.model_dump_json(), time.time()),
            )
            sim.db.commit()
            sim.dirty(str(value.command_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Command ID already used") from None
        return {"id": str(value.command_id), "state": "proposed"}

    @app.post("/api/v1/tasks", status_code=201, response_model=TaskResponse)
    async def propose(value: Proposal, request: Request, operator: Operator):
        if value.inspection is not None:
            raise HTTPException(422, "Use the bounded mission planner to create an inspection")
        return await create_task(value, request, operator)

    async def approve_task(
        task_id: str,
        value: Confirmation,
        request: Request,
        operator: Session,
        expected_plan_sha256: str | None = None,
    ):
        sim = state(request, require_watchdog=True)
        stop_epoch = sim.stop_epoch
        await confirm(sim, request, value)
        # Confirmation yields to the watchdog/logout; recheck authority afterwards.
        sim = state(request, require_watchdog=True)
        operator = await session(request)
        if (
            sim.stop_epoch != stop_epoch
            or sim.stopped
            or sim.active
            or sim.humanoid.active
            or sim.humanoid.pending
        ):
            raise HTTPException(409, "Stopped or already running")
        row = sim.db.execute(
            "SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, operator["name"])
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Task not found")
        if row["state"] != "proposed":
            raise HTTPException(409, "Task cannot be replayed")
        proposal = Proposal.model_validate_json(row["proposal"])
        record = proposal.inspection
        if record is not None:
            if expected_plan_sha256 is None or not secrets.compare_digest(
                expected_plan_sha256, record.plan_sha256
            ):
                raise HTTPException(409, "Review and approve this exact mission plan")
            if record.outcome != "awaiting_approval":
                raise HTTPException(409, "Inspection cannot be replayed or approved again")
        elif expected_plan_sha256 is not None:
            raise HTTPException(404, "Inspection mission not found")
        try:
            sim.gate.validate(proposal, sim.position)
        except SafetyViolation as exc:
            raise HTTPException(422, str(exc)) from None
        if record is not None:
            # The bounded audit actor carries the same full issuer/subject hash
            # as the task owner, without its storage namespace prefix.
            actor = (
                operator["name"].removeprefix("sso:") if identity_config else "configured_operator"
            )
            proposal = sim.save_inspection(
                task_id,
                proposal,
                inspection.approve(
                    record,
                    time.time(),
                    actor,
                    authentication_method="onesa_mfa" if identity_config else "password",
                ),
            )
        sim.db.execute(
            "UPDATE tasks SET state='running',reason='operator_approved' WHERE id=?", (task_id,)
        )
        sim.db.commit()
        sim.dirty(task_id)
        await sim.flush_journal()
        sim = state(request, require_watchdog=True)
        try:
            await session(request)
        except HTTPException:
            sim.cancel("approval_authority_lost", stop=True)
            await sim.flush_journal()
            raise
        # Stop followed by an explicit Reset must still invalidate this earlier
        # approval across both journal and central-identity acknowledgement waits.
        if (
            sim.stop_epoch != stop_epoch
            or sim.stopped
            or sim.active
            or sim.humanoid.active
            or sim.humanoid.pending
        ):
            sim.cancel("approval_authority_lost", stop=True)
            await sim.flush_journal()
            raise HTTPException(409, "Stopped or already running")
        try:
            sim.gate.validate(proposal, sim.position)
        except SafetyViolation:
            sim.cancel("approval_expired", stop=True)
            await sim.flush_journal()
            raise HTTPException(409, "Proposal expired during storage acknowledgement") from None
        if proposal.inspection is not None:
            record = proposal.inspection
            record = record.model_copy(
                update={
                    "started_at": time.time(),
                    "events": (
                        *record.events,
                        inspection.Event(
                            at=time.time(),
                            elapsed_seconds=0.0,
                            kind="execution_started",
                            detail="Durable approval acknowledged; software execution started",
                        ),
                    ),
                }
            )
            proposal = sim.save_inspection(task_id, proposal, record)
            sim.db.commit()
        sim.active = {
            "id": task_id,
            "session": operator["token"],
            "proposal": proposal,
            "start": sim.position,
            "started": time.monotonic(),
            "heartbeat": time.monotonic(),
        }
        return {"id": task_id, "state": "running"}

    @app.post("/api/v1/tasks/{task_id}/approve", response_model=TaskResponse)
    async def approve(task_id: str, value: Confirmation, request: Request, operator: Operator):
        return await approve_task(task_id, value, request, operator)

    def mission_detail(task_id: str, request: Request, operator: Session) -> dict:
        sim = state(request)
        row = sim.db.execute(
            "SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, operator["name"])
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Inspection mission not found")
        try:
            proposal = Proposal.model_validate_json(row["proposal"])
            if sim.active and sim.active["id"] == task_id:
                proposal = sim.active["proposal"]
            record = proposal.inspection
            if record is None:
                raise HTTPException(404, "Inspection mission not found")
            if inspection.digest(record.plan) != record.plan_sha256:
                raise ValueError("Inspection plan checksum mismatch")
            return {
                "id": task_id,
                "state": row["state"],
                "record": record,
                "report": inspection.report(record),
            }
        except ValueError:
            raise HTTPException(503, "Stored inspection evidence could not be verified") from None

    @app.get("/api/v1/missions/catalog", response_model=MissionCatalog)
    async def mission_catalog(operator: Operator):
        return {
            "version": inspection.VERSION,
            "mode": "simulation",
            "physical_actuation": False,
            "assets": [inspection.PUMP.model_dump(mode="json")],
            "robots": [profile.model_dump(mode="json") for profile in inspection.PROFILES],
            "skills": [skill.model_dump(mode="json") for skill in inspection.SKILLS],
            "scenarios": [
                {"id": key, "label": label, "source": "synthetic scenario/v1"}
                for key, label in (
                    ("normal", "Normal readings"),
                    ("threshold", "Threshold violation"),
                    ("discrepancy", "Observation / telemetry discrepancy"),
                    ("interrupted", "Interrupted inspection"),
                    ("missing", "Missing observation"),
                    ("stale", "Stale telemetry"),
                    ("units", "Incompatible observation units"),
                )
            ],
            "asset_context_import": "enabled"
            if config.context_import_enabled
            else "disabled_by_configuration",
            "model_inference": "not_configured",
        }

    @app.post("/api/v1/missions/context")
    async def import_mission_context(
        value: inspection.ContextRequest, request: Request, operator: Operator
    ):
        sim = state(request)
        if not config.context_import_enabled:
            raise HTTPException(
                409, "Context import is disabled; the standalone synthetic demo remains available"
            )
        clock = time.time()
        sim.inspection_contexts = {
            key: item for key, item in sim.inspection_contexts.items() if item[1] > clock
        }
        if (
            sum(item[0] == operator["token"] for item in sim.inspection_contexts.values()) >= 4
            or len(sim.inspection_contexts) >= 16
        ):
            raise HTTPException(429, "Context review capacity reached; wait for expiry or sign out")
        try:
            asset = inspection.import_context(value, clock)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        identifier = str(uuid4())
        sim.inspection_contexts[identifier] = (operator["token"], clock + 600, asset)
        return {
            "id": identifier,
            "expires_at": clock + 600,
            "asset": asset,
            "authority": "untrusted_export_operator_review_required",
        }

    @app.get("/api/v1/missions")
    async def missions(request: Request, operator: Operator):
        sim = state(request)
        await sim.flush_journal()
        # Preserve the existing bounded recent-history window, including legacy
        # tasks. Exact owned mission IDs remain readable/exportable separately.
        rows = sim.db.execute(
            "SELECT id,proposal,state,created FROM tasks WHERE owner=? "
            "ORDER BY created DESC LIMIT 50",
            (operator["name"],),
        ).fetchall()
        result = []
        for row in rows:
            try:
                payload = json.loads(row["proposal"])
            except ValueError:
                continue  # Preserve historical legacy payloads without interpreting them.
            if isinstance(payload, dict) and payload.get("inspection") is not None:
                detail = mission_detail(row["id"], request, operator)
                record = detail["record"]
                result.append(
                    {
                        "id": row["id"],
                        "state": row["state"],
                        "created": row["created"],
                        "asset": record.plan.asset.name,
                        "scenario": record.plan.scenario,
                        "outcome": record.outcome,
                        "finding_count": len(record.findings),
                    }
                )
        return {"missions": result, "history_window": "most_recent_50_tasks"}

    @app.post("/api/v1/missions", status_code=201, response_model=MissionDetail)
    async def plan_mission(value: inspection.MissionRequest, request: Request, operator: Operator):
        sim = state(request, require_watchdog=True)
        asset = inspection.PUMP
        if value.context_id is not None:
            cached = sim.inspection_contexts.get(str(value.context_id))
            if (
                not config.context_import_enabled
                or cached is None
                or cached[0] != operator["token"]
                or cached[1] <= time.time()
            ):
                raise HTTPException(
                    409, "Context is unavailable or expired; import and review it again"
                )
            asset = cached[2]
        try:
            record = inspection.make_record(value, sim.position, time.time(), asset)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        proposal = Proposal(
            command_id=value.command_id,
            issued_at=record.plan.created_at,
            target_radians=record.plan.target_radians,
            duration_seconds=record.plan.duration_seconds,
            inspection=record,
        )
        await create_task(proposal, request, operator)
        return mission_detail(str(value.command_id), request, operator)

    @app.get("/api/v1/missions/{task_id}", response_model=MissionDetail)
    async def read_mission(task_id: UUID, request: Request, operator: Operator):
        return mission_detail(str(task_id), request, operator)

    @app.post("/api/v1/missions/{task_id}/approve", response_model=MissionDetail)
    async def approve_mission(
        task_id: UUID, value: MissionConfirmation, request: Request, operator: Operator
    ):
        await approve_task(str(task_id), value, request, operator, value.expected_plan_sha256)
        return mission_detail(str(task_id), request, operator)

    @app.get("/api/v1/missions/{task_id}/export", response_model=MissionDetail)
    async def export_mission(
        task_id: UUID, request: Request, response: Response, operator: Operator
    ):
        response.headers["Content-Disposition"] = (
            f'attachment; filename="inspection-{task_id}.json"'
        )
        return mission_detail(str(task_id), request, operator)

    @app.post("/api/v1/heartbeat", response_model=ActionResponse)
    async def heartbeat(request: Request, operator: Operator):
        sim = state(request)
        if sim.active and sim.active["session"] == operator["token"]:
            sim.active["heartbeat"] = time.monotonic()
        return {"status": "received"}

    @app.post("/api/v1/humanoid/heartbeat", response_model=ActionResponse)
    async def humanoid_heartbeat(value: humanoid.Heartbeat, request: Request, operator: Operator):
        # A background legacy-console poll is not supervision of whole-body motion.
        sim = state(request, require_watchdog=True)
        active = sim.humanoid.active
        if (
            active is None
            or active.owner != operator["token"]
            or active.plan.request.command_id != value.command_id
        ):
            raise HTTPException(409, "Humanoid command is not running in this session")
        active.heartbeat = time.monotonic()
        return {"status": "received"}

    @app.get("/api/v1/humanoid/model", response_model=humanoid.Model)
    async def humanoid_model(operator: Operator):
        return humanoid.Model()

    @app.get("/api/v1/humanoid/model.urdf", response_class=Response)
    async def humanoid_urdf(operator: Operator):
        return Response(
            humanoid.urdf(),
            media_type="application/xml",
            headers={"Content-Disposition": 'attachment; filename="onesa-humanoid-reference.urdf"'},
        )

    @app.get(
        "/api/v1/humanoid/state",
        response_model=humanoid.Observation,
        responses=ACTUATOR_UNAVAILABLE,
    )
    async def humanoid_state(request: Request, operator: Operator):
        sim = state(request)
        observed = sim.humanoid.observe(operator["token"], time.time(), sim.stopped)
        if identity_config:
            trim_humanoid_owners(sim)
            observed = observed.model_copy(
                update={
                    "history": [
                        plan
                        for plan in observed.history
                        if sim.identity_humanoid_owners.get(plan.request.command_id)
                        == operator["name"]
                    ]
                }
            )
        return observed

    def trim_humanoid_owners(sim: State) -> None:
        # Keep only the bounded in-memory history and the current command. The
        # session token still owns approval/execution; stable identity owns history.
        retained = {plan.request.command_id for plan in sim.humanoid.history}
        if sim.humanoid.pending:
            retained.add(sim.humanoid.pending[1].request.command_id)
        if sim.humanoid.active:
            retained.add(sim.humanoid.active.plan.request.command_id)
        sim.identity_humanoid_owners = {
            command: owner
            for command, owner in sim.identity_humanoid_owners.items()
            if command in retained
        }

    def humanoid_ready(request: Request) -> State:
        sim = state(request, require_watchdog=True)
        sim.humanoid.require_healthy()
        if sim.journal_failed:
            raise HTTPException(503, "Remote simulation journal requires recovery")
        if (
            sim.stopped
            or sim.active
            or sim.db.execute(
                "SELECT count(*) FROM tasks WHERE state IN ('proposed','running')"
            ).fetchone()[0]
        ):
            raise HTTPException(409, "Reset Stop and complete or cancel legacy tasks first")
        return sim

    @app.post(
        "/api/v1/humanoid/proposals",
        status_code=201,
        response_model=humanoid.MotionPlan,
        responses=ACTUATOR_UNAVAILABLE,
    )
    async def humanoid_propose(value: humanoid.MotionRequest, request: Request, operator: Operator):
        sim = humanoid_ready(request)
        try:
            plan = sim.humanoid.propose(value, operator["token"], time.time())
            if identity_config:
                trim_humanoid_owners(sim)
                sim.identity_humanoid_owners[value.command_id] = operator["name"]
            return plan
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

    @app.post(
        "/api/v1/humanoid/proposals/{command_id}/approve",
        response_model=humanoid.MotionPlan,
        responses=ACTUATOR_UNAVAILABLE,
    )
    async def humanoid_approve(
        command_id: UUID, value: MissionConfirmation, request: Request, operator: Operator
    ):
        sim = humanoid_ready(request)
        epoch = sim.stop_epoch
        await confirm(sim, request, value)
        operator = await session(request)
        sim = humanoid_ready(request)
        if sim.stop_epoch != epoch:
            raise HTTPException(409, "Stop changed during confirmation")
        try:
            return sim.humanoid.approve(
                command_id,
                operator["token"],
                value.expected_plan_sha256,
                time.time(),
                time.monotonic(),
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from None

    @app.post("/api/v1/tasks/{task_id}/cancel", response_model=ActionResponse)
    async def cancel(task_id: str, request: Request, operator: Operator):
        sim = state(request)
        row = sim.db.execute(
            "SELECT * FROM tasks WHERE id=? AND owner=?", (task_id, operator["name"])
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Task not found")
        if row["state"] == "running" or (sim.active and sim.active["id"] == task_id):
            sim.cancel("operator_cancel", stop=True)
        elif row["state"] == "proposed":
            sim.interrupt_inspection(row, "operator_cancel")
            sim.db.execute(
                "UPDATE tasks SET state='cancelled',reason='operator_cancel' "
                "WHERE id=? AND state='proposed'",
                (task_id,),
            )
            sim.db.commit()
            sim.dirty(task_id)
        return {"status": "cancelled"}

    @app.post("/api/v1/stop", response_model=ActionResponse)
    async def stop(request: Request, operator: Operator):
        state(request).cancel("operator_stop", stop=True)
        return {"status": "stopped"}

    @app.post("/api/v1/reset", response_model=ActionResponse, responses=ACTUATOR_UNAVAILABLE)
    async def reset(value: Confirmation, request: Request, operator: Operator):
        sim = state(request, require_watchdog=True)
        stop_epoch = sim.stop_epoch
        await confirm(sim, request, value)
        sim = state(request, require_watchdog=True)
        await session(request)
        if sim.journal_failed:
            raise HTTPException(503, "Remote simulation journal requires recovery")
        if sim.stop_epoch != stop_epoch:
            raise HTTPException(409, "Stop changed during reset confirmation")
        if sim.active or sim.humanoid.active or sim.humanoid.pending:
            raise HTTPException(409, "Cancel the current task first")
        sim.humanoid.verified_positions()
        sim.db.execute("UPDATE safety SET stopped=0 WHERE id=1")
        sim.db.commit()
        return {"status": "reset", "physical_actuation": False}

    return app
