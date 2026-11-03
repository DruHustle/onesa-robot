"""Real simulator handlers and SQL behind an isolated fake Turso HTTP boundary."""

import asyncio
import json
import math
import sqlite3
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from robot_api.main import Settings, create_app
from robot_api.turso import COLUMNS, LEASE, PRODUCTION_DATABASE, TABLE, JournalFailure, TursoJournal
from tests.test_simulation import HASH, PASSWORD, approve, login, proposal, reset

URL = "libsql://synthetic-staging.example.turso.io"
TOKEN = "placeholder"


@pytest.mark.parametrize("delay, succeeds", [(1.2, True), (2.4, False)])
def test_real_http_read_uses_statement_budget_without_extending_it(monkeypatch, delay, succeeds):
    import robot_api.turso as module

    async def run():
        calls = []
        handlers = set()

        async def respond(reader, writer):
            task = asyncio.current_task()
            handlers.add(task)
            try:
                head = await reader.readuntil(b"\r\n\r\n")
                length = next(
                    int(line.split(b":", 1)[1])
                    for line in head.split(b"\r\n")
                    if line.lower().startswith(b"content-length:")
                )
                body = json.loads(await reader.readexactly(length))
                calls.append(body)
                assert body["requests"][0]["stmt"]["sql"] == "SELECT 1"
                await asyncio.sleep(delay)
                payload = json.dumps(
                    {
                        "results": [
                            {
                                "type": "ok",
                                "response": {
                                    "type": "execute",
                                    "result": {
                                        "cols": [{"name": "value"}],
                                        "rows": [[{"type": "integer", "value": "1"}]],
                                    },
                                },
                            },
                            {"type": "ok", "response": {"type": "close"}},
                        ]
                    }
                ).encode()
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Length: "
                    + str(len(payload)).encode()
                    + b"\r\nConnection: close\r\n\r\n"
                    + payload
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
                handlers.discard(task)

        server = await asyncio.start_server(respond, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr(module, "endpoint", lambda _: f"http://127.0.0.1:{port}/v2/pipeline")
        journal = TursoJournal(URL, SecretStr(TOKEN))
        started = time.monotonic()
        try:
            if succeeds:
                assert await journal.execute("SELECT 1") == [{"value": 1}]
            else:
                with pytest.raises(JournalFailure) as caught:
                    await journal.execute("SELECT 1")
                assert caught.value.category == "timeout"
            elapsed = time.monotonic() - started
            if succeeds:
                assert delay <= elapsed + 0.1
            else:
                assert elapsed < 2.3
            assert len(calls) == 1
        finally:
            await journal.client.aclose()
            server.close()
            await server.wait_closed()
            for task in list(handlers):
                task.cancel()
            await asyncio.gather(*handlers, return_exceptions=True)

    asyncio.run(run())


@pytest.fixture
def remote(tmp_path, monkeypatch):
    path = tmp_path / "cloud.sqlite3"
    state = {
        "offline": False,
        "delay": 0.0,
        "calls": 0,
        "redirect": False,
        "malformed": False,
        "after_execute": None,
        "before_execute": None,
        "round_float_json": False,
        "database_clock": None,
    }

    async def handler(request):
        state["calls"] += 1
        if state["delay"]:
            await asyncio.sleep(state["delay"])
        if state["offline"]:
            return httpx.Response(403, json={"secret": TOKEN})
        if state["redirect"]:
            return httpx.Response(307, headers={"Location": "https://attacker.example"})
        if state["malformed"]:
            return httpx.Response(200, json={"results": []})
        assert str(request.url) == "https://synthetic-staging.example.turso.io/v2/pipeline"
        assert request.headers["Authorization"] == "Bearer " + TOKEN
        body = json.loads(request.content)
        assert body["requests"][1] == {"type": "close"}
        statement = body["requests"][0]["stmt"]
        if state["before_execute"] is not None:
            state["before_execute"](statement["sql"])
        args = []
        for cell in statement["args"]:
            kind = cell["type"]
            value = int(cell["value"]) if kind == "integer" else cell.get("value")
            if state["round_float_json"] and kind == "float":
                if value == math.nextafter(0.1, 0):
                    value = 0.1
            args.append(value)
        with sqlite3.connect(path) as db:
            if state["database_clock"] is not None:
                # Lease boundary fixtures control database seconds independently
                # of the application's monotonic transport deadlines.
                db.create_function("unixepoch", 0, state["database_clock"])
            cursor = db.execute(statement["sql"], args)
            columns = [col[0] for col in cursor.description or []]
            rows = cursor.fetchall()
        if state["after_execute"] is not None:
            state["after_execute"](statement["sql"])

        def encode(value):
            if isinstance(value, int):
                return {"type": "integer", "value": str(value)}
            if isinstance(value, float):
                if state["round_float_json"] and value == math.nextafter(0.1, 0):
                    value = 0.1
                return {"type": "float", "value": value}
            return {"type": "text", "value": value}

        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "type": "ok",
                        "response": {
                            "type": "execute",
                            "result": {
                                "cols": [{"name": name} for name in columns],
                                "rows": [[encode(value) for value in row] for row in rows],
                            },
                        },
                    },
                    {"type": "ok", "response": {"type": "close"}},
                ]
            },
        )

    original = TursoJournal.__init__

    def initialize(self, url, token):
        original(self, url, token)
        self.client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer " + token.get_secret_value()},
            follow_redirects=False,
        )

    monkeypatch.setattr(TursoJournal, "__init__", initialize)
    return path, state


def config(tmp_path):
    return Settings(
        operator_password_hash=HASH,
        deployment_environment="staging",
        storage_mode="turso",
        turso_database_url=URL,
        turso_auth_token=TOKEN,
        database_path=tmp_path / "must-not-be-created.sqlite3",
        heartbeat_seconds=1,
    )


def client(settings):
    result = TestClient(create_app(settings))
    result.headers["Origin"] = settings.public_origin
    return result


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ReadError])
def test_lost_save_ack_observes_exact_commit_once_without_replaying(remote, failure):
    _, network = remote
    statements = []

    def lose_ack(sql):
        statements.append(sql)
        if sql.startswith(f"INSERT INTO {TABLE}"):
            raise failure("synthetic private provider detail")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network["after_execute"] = lose_ack
            row = dict(
                zip(COLUMNS, ("synthetic", "owner", "{}", "proposed", "", 1.5, 0.0), strict=True)
            )
            await journal.save(row)
            assert len(statements) == 2
            assert statements[0].startswith("INSERT")
            assert statements[1].startswith("SELECT")
            assert "expires>unixepoch()" in statements[1]
        finally:
            network["after_execute"] = None
            await journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("column", COLUMNS)
def test_lost_save_ack_rejects_any_different_persisted_field(remote, column):
    path, network = remote
    statements = []

    def lose_ack(sql):
        statements.append(sql)
        if sql.startswith(f"INSERT INTO {TABLE}"):
            with sqlite3.connect(path) as db:
                db.execute(
                    f"UPDATE {TABLE} SET {column}=?",
                    (2.5 if column in {"created", "position"} else "different",),
                )
            raise httpx.ReadTimeout("synthetic lost acknowledgement")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network["after_execute"] = lose_ack
            row = dict(
                zip(COLUMNS, ("synthetic", "owner", "{}", "proposed", "", 1.5, 0.0), strict=True)
            )
            with pytest.raises(JournalFailure) as caught:
                await journal.save(row)
            assert caught.value.category == "timeout"
            assert caught.value.phase == "task_save"
            assert len(statements) == 2
        finally:
            network["after_execute"] = None
            await journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("uncertainty", ["foreign_lease", "expired_lease", "read_timeout"])
def test_lost_save_ack_cannot_restore_uncertain_or_foreign_authority(remote, uncertainty):
    path, network = remote
    statements = []

    def lose_ack(sql):
        statements.append(sql)
        if sql.startswith(f"INSERT INTO {TABLE}"):
            with sqlite3.connect(path) as db:
                if uncertainty == "foreign_lease":
                    db.execute(f"UPDATE {LEASE} SET owner='replacement'")
                elif uncertainty == "expired_lease":
                    db.execute(f"UPDATE {LEASE} SET expires=unixepoch()-1")
            raise httpx.ReadTimeout("synthetic lost acknowledgement")
        if uncertainty == "read_timeout":
            raise httpx.ReadTimeout("synthetic uncertain read")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network["after_execute"] = lose_ack
            row = dict(
                zip(COLUMNS, ("synthetic", "owner", "{}", "proposed", "", 1.5, 0.0), strict=True)
            )
            with pytest.raises(JournalFailure) as caught:
                await journal.save(row)
            assert caught.value.category == "timeout"
            assert "synthetic" not in str(caught.value)
            assert len(statements) == 2
        finally:
            network["after_execute"] = None
            await journal.close()

    asyncio.run(run())


def test_explicit_provider_denial_does_not_attempt_reconciliation(remote):
    _, network = remote

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network["offline"] = True
            calls = network["calls"]
            row = dict(
                zip(COLUMNS, ("synthetic", "owner", "{}", "proposed", "", 1.5, 0.0), strict=True)
            )
            with pytest.raises(JournalFailure) as caught:
                await journal.save(row)
            assert caught.value.category == "http_status"
            assert network["calls"] == calls + 1
        finally:
            network["offline"] = False
            await journal.close()

    asyncio.run(run())


def test_committed_lost_ack_preserves_real_handlers_and_stop(remote, tmp_path):
    path, network = remote
    writes = 0

    def lose_ack(sql):
        nonlocal writes
        if sql.startswith(f"INSERT INTO {TABLE}"):
            writes += 1
            raise httpx.ReadTimeout("synthetic lost acknowledgement")

    with client(config(tmp_path)) as api:
        login(api)
        reset(api)
        network["after_execute"] = lose_ack
        identifier = approve(api, proposal(duration_seconds=5))
        assert api.get("/api/v1/ready").status_code == 200
        assert api.post("/api/v1/stop").status_code == 200
        status = api.get("/api/v1/status").json()
        assert status["stopped"] and status["active_task"] is None
        with sqlite3.connect(path) as db:
            assert db.execute(f"SELECT id,state,reason FROM {TABLE}").fetchall() == [
                (identifier, "cancelled", "operator_stop")
            ]
        assert writes == 3  # Proposal, approval and Stop; no write is replayed.
        network["after_execute"] = None


def test_durable_history_safe_restart_and_no_local_file(remote, tmp_path):
    path, _ = remote
    settings = config(tmp_path)
    with client(settings) as first:
        login(first)
        reset(first)
        task_id = approve(first, proposal(duration_seconds=5))
        old_cookie = first.cookies.get("robot_session")
        assert first.get("/api/v1/ready").json()["storage_mode"] == "turso"
        with sqlite3.connect(path) as db:
            assert db.execute(f"SELECT state FROM {TABLE} WHERE id=?", (task_id,)).fetchone() == (
                "running",
            )
    with client(settings) as second:
        second.cookies.set("robot_session", old_cookie)
        assert second.get("/api/v1/status").status_code == 401
        second.cookies.clear()
        login(second)
        assert second.get("/api/v1/status").json()["stopped"]
        row = second.get("/api/v1/tasks").json()["tasks"][0]
        assert row["id"] == task_id and row["state"] == "cancelled"
        assert (
            second.post(f"/api/v1/tasks/{task_id}/approve", json={"password": PASSWORD}).status_code
            == 409
        )
    assert not settings.database_path.exists()


def test_offline_write_stops_and_cannot_claim_success_or_recover_silently(remote, tmp_path):
    _, network = remote
    with client(config(tmp_path)) as api:
        login(api)
        reset(api)
        network["offline"] = True
        response = api.post("/api/v1/tasks", json=proposal())
        assert response.status_code == 503 and TOKEN not in response.text
        assert api.get("/api/v1/health").status_code == 200
        status = api.get("/api/v1/status").json()
        assert status["stopped"] and status["active_task"] is None
        assert api.get("/api/v1/ready").status_code == 503
        network["offline"] = False
        assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 503


def test_network_delay_does_not_block_watchdog_or_local_stop(remote, tmp_path):
    _, network = remote
    with client(config(tmp_path)) as api:
        login(api)
        reset(api)
        approve(api, proposal(duration_seconds=5))
        network["delay"] = 0.6
        # A journal HTTP request yields while the independent watchdog keeps running.
        for _ in range(2):
            api.portal.call(api.app.state.sim.journal.probe)
        status = api.get("/api/v1/status").json()
        assert status["stopped"] and status["active_task"] is None
        assert api.get("/api/v1/tasks").json()["tasks"][0]["reason"] == "operator_connection_lost"


def test_rejects_second_writer_and_expired_lease(remote, tmp_path):
    path, _ = remote
    with client(config(tmp_path)) as first:
        with client(config(tmp_path)) as standby:
            assert standby.get("/api/v1/health").status_code == 200
            assert standby.get("/api/v1/ready").status_code == 503
            assert (
                standby.post(
                    "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
                ).status_code
                == 503
            )
            assert standby.post("/api/v1/tasks", json=proposal()).status_code == 503
        assert first.get("/api/v1/ready").status_code == 200
        login(first)
        reset(first)
        with sqlite3.connect(path) as db:
            db.execute(f"UPDATE {LEASE} SET expires=0")
        assert first.post("/api/v1/tasks", json=proposal()).status_code == 503
        assert first.get("/api/v1/status").json()["stopped"]


@pytest.mark.parametrize("fault", ["offline", "redirect", "malformed"])
def test_startup_fails_honestly_and_redacts_remote_errors(remote, tmp_path, fault):
    _, network = remote
    network[fault] = True
    with pytest.raises(sqlite3.OperationalError) as error:
        with client(config(tmp_path)):
            pass
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize(
    "changes",
    [
        {"turso_auth_token": ""},
        {"deployment_environment": "production"},
        {"turso_database_url": "libsql://attacker.example"},
        {"turso_database_url": "libsql://name.turso.io/path"},
        {"turso_database_url": "libsql://user:password@name.turso.io"},
        {"storage_mode": "ephemeral-simulation"},
    ],
)
def test_remote_settings_require_explicit_safe_staging(changes, tmp_path):
    values = config(tmp_path).model_dump()
    values.update(changes)
    with pytest.raises(ValidationError):
        Settings(**values)


def test_production_journal_requires_owner_approved_identity_and_no_actuation(tmp_path):
    values = config(tmp_path).model_dump()
    values.update(deployment_environment="production", turso_database_url=PRODUCTION_DATABASE)
    assert Settings(**values).storage_mode == "turso"
    for changes in [
        {"turso_database_url": URL},
        {"turso_database_url": "libsql://other-production.turso.io"},
        {"turso_auth_token": ""},
        {"actuation_enabled": True},
        {"deployment_environment": "staging"},
        {"deployment_environment": "local"},
    ]:
        with pytest.raises(ValidationError):
            Settings(**{**values, **changes})


def test_stop_during_cloud_approval_cannot_grant_execution_authority(remote, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    _, network = remote
    with client(config(tmp_path)) as api:
        login(api)
        reset(api)
        value = proposal(duration_seconds=5)
        task_id = api.post("/api/v1/tasks", json=value).json()["id"]
        network["delay"] = 0.6
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                api.post, f"/api/v1/tasks/{task_id}/approve", json={"password": PASSWORD}
            )
            # Wait for the actual local approval boundary, not a guessed scheduling delay.
            for _ in range(100):
                state = api.portal.call(
                    lambda: api.app.state.sim.db.execute(
                        "SELECT state FROM tasks WHERE id=?", (task_id,)
                    ).fetchone()[0]
                )
                if state == "running":
                    break
                time.sleep(0.01)
            assert state == "running"
            assert api.app.state.sim.active is None
            assert api.post("/api/v1/stop").status_code == 200
            assert future.result(timeout=5).status_code == 409
        assert api.get("/api/v1/status").json()["active_task"] is None
        assert api.get("/api/v1/tasks").json()["tasks"][0]["state"] == "cancelled"


def test_event_journal_does_not_write_each_watchdog_tick(remote, tmp_path):
    path, _ = remote
    with client(config(tmp_path)) as api:
        login(api)
        reset(api)
        calls = 0
        sim = api.app.state.sim
        save = sim.journal.save

        async def record(row):
            nonlocal calls
            calls += 1
            await save(row)

        sim.journal.save = record
        task_id = approve(api, proposal(duration_seconds=5))
        time.sleep(0.3)
        assert calls == 2  # One proposal and one approval, not 20 Hz position writes.
        assert api.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 200
        assert calls == 3
        with sqlite3.connect(path) as db:
            assert db.execute(f"SELECT state FROM {TABLE}").fetchone()[0] == "cancelled"


def test_owner_lease_renewal_does_not_restore_an_expired_owner(remote, tmp_path):
    path, _ = remote
    with client(config(tmp_path)) as api:
        sim = api.app.state.sim
        api.portal.call(sim.refresh_journal_lease)
        with sqlite3.connect(path) as db:
            db.execute(f"UPDATE {LEASE} SET owner='replacement',expires=unixepoch()+30")
        with pytest.raises(sqlite3.OperationalError, match="ownership expired"):
            api.portal.call(sim.refresh_journal_lease)
        assert api.get("/api/v1/ready").status_code == 503


def test_anonymous_readiness_cannot_exhaust_database_pool(remote, tmp_path):
    _, network = remote
    with client(config(tmp_path)) as api:
        calls = network["calls"]
        for _ in range(20):
            assert api.get("/api/v1/ready").status_code == 200
        assert network["calls"] == calls
        sim = api.app.state.sim
        api.portal.call(lambda: setattr(sim, "journal_lease_confirmed_at", time.monotonic() - 16))
        assert api.get("/api/v1/ready").status_code == 503
        assert api.portal.call(lambda: sim.stopped)


def test_rolling_replacement_waits_without_stealing_then_restores_history(remote, tmp_path):
    path, _ = remote
    settings = config(tmp_path)
    prior = client(settings)
    prior.__enter__()
    closed = False
    try:
        login(prior)
        reset(prior)
        identifier = approve(prior, proposal(duration_seconds=5))
        prior.post("/api/v1/stop")
        with sqlite3.connect(path) as db:
            owner = db.execute(f"SELECT owner FROM {LEASE}").fetchone()[0]
        with client(settings) as replacement:
            assert replacement.get("/api/v1/health").status_code == 200
            assert replacement.get("/api/v1/ready").status_code == 503
            with sqlite3.connect(path) as db:
                assert db.execute(f"SELECT owner FROM {LEASE}").fetchone()[0] == owner
            prior.__exit__(None, None, None)
            closed = True
            deadline = time.monotonic() + 5
            while replacement.get("/api/v1/ready").status_code != 200:
                assert time.monotonic() < deadline
                time.sleep(0.05)
            login(replacement)
            state = replacement.get("/api/v1/status").json()
            assert state["stopped"] and state["active_task"] is None
            assert state["physical_actuation"] is False
            history = replacement.get("/api/v1/tasks").json()["tasks"]
            assert any(row["id"] == identifier and row["state"] == "cancelled" for row in history)
    finally:
        if not closed:
            prior.__exit__(None, None, None)


def test_standby_deadline_fails_closed_and_preserves_live_owner(remote, tmp_path, monkeypatch):
    import robot_api.main as main

    monkeypatch.setattr(main, "JOURNAL_STARTUP_SECONDS", 0)
    path, _ = remote
    with client(config(tmp_path)) as first:
        with client(config(tmp_path)) as standby:
            deadline = time.monotonic() + 2
            while standby.get("/api/v1/health").status_code != 503:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            assert standby.get("/api/v1/ready").status_code == 503
            assert (
                standby.post(
                    "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
                ).status_code
                == 503
            )
        assert first.get("/api/v1/ready").status_code == 200
        with sqlite3.connect(path) as db:
            assert (
                db.execute(f"SELECT count(*) FROM {LEASE} WHERE expires>unixepoch()").fetchone()[0]
                == 1
            )
