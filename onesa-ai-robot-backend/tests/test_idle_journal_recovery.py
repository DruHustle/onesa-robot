"""Idle recovery uses new fenced ownership; never repairs or resumes task history."""

import asyncio
import sqlite3
import time
from uuid import uuid4

import pytest
from pydantic import SecretStr

from robot_api.main import State
from robot_api.turso import COLUMNS, LEASE, TABLE, JournalFailure, TursoJournal
from tests.test_simulation import login
from tests.test_turso import TOKEN, URL, client, config
from tests.test_turso import remote as remote


def history(path):
    with sqlite3.connect(path) as db:
        return db.execute(f"SELECT {','.join(COLUMNS)} FROM {TABLE} ORDER BY id").fetchall()


async def prepared(tmp_path):
    sim = State(config(tmp_path))
    await sim.initialize_journal()
    values = ("synthetic-terminal", "operator", "{}", "completed", "", 1.5, 0.25)
    sim.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?,?)", values)
    sim.db.commit()
    sim.dirty(values[0])
    await sim.flush_journal()
    return sim


def expire(path):
    with sqlite3.connect(path) as db:
        db.execute(f"UPDATE {LEASE} SET expires=unixepoch()-1")


def fail(sim, category="timeout", phase="lease_renew"):
    sim.fail_journal(JournalFailure(category, phase, 7000))


async def close(sim):
    await sim.journal.close()
    sim.db.close()


def test_idle_recovery_preserves_history_revokes_sessions_and_fences_late_old_writes(
    remote, tmp_path
):
    path, _ = remote

    async def run():
        sim = await prepared(tmp_path)
        previous = sim.journal
        original = history(path)
        sim.db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?)", ("old", "csrf", time.time() + 60, "operator")
        )
        sim.db.commit()
        try:
            fail(sim)
            assert sim.journal_failed and sim.journal_recovery_allowed and sim.stopped
            assert not await sim.recover_idle_journal()  # Wait for the bounded lease-expiry delay.
            assert sim.journal_recovery_attempts == 0
            expire(path)
            sim.journal_recovery_next_at = 0
            assert await sim.recover_idle_journal()
            assert sim.journal.owner != previous.owner
            assert not sim.journal_failed and sim.stopped and sim.active is None
            assert sim.physical_permission()["permission"] == "blocked"
            assert sim.db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
            assert history(path) == original
            stale = TursoJournal(URL, SecretStr(TOKEN))
            stale.owner = previous.owner
            stale.acknowledged = previous.acknowledged.copy()
            try:
                row = dict(zip(COLUMNS, original[0], strict=True))
                row["position"] = 0.5
                with pytest.raises(JournalFailure) as error:
                    await stale.save(row)
                assert error.value.category == "ownership"
            finally:
                await stale.close()
            assert history(path) == original
            with sqlite3.connect(path) as db:
                assert db.execute(f"SELECT owner FROM {LEASE}").fetchone()[0] == sim.journal.owner
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize(
    "category,phase",
    [
        ("ownership", "lease_renew"),
        ("schema", "lease_renew"),
        ("timeout", "task_save"),
        ("lease_stale", "ready"),
        ("protocol", "lease_renew"),
    ],
)
def test_non_transport_or_nonrenewal_failures_cannot_enable_automatic_recovery(
    remote, tmp_path, category, phase
):
    _, network = remote

    async def run():
        sim = await prepared(tmp_path)
        try:
            fail(sim, category, phase)
            calls = network["calls"]
            sim.journal_recovery_next_at = 0
            assert not await sim.recover_idle_journal()
            assert not sim.journal_recovery_allowed and sim.journal_failed
            assert network["calls"] == calls
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize("unsafe", ["armed", "active", "dirty", "pending", "physical"])
def test_nonidle_work_cannot_enable_recovery(remote, tmp_path, unsafe):
    _, network = remote

    async def run():
        sim = await prepared(tmp_path)
        try:
            if unsafe == "armed":
                sim.db.execute("UPDATE safety SET stopped=0")
                sim.db.commit()
            elif unsafe == "active":
                sim.active = {"synthetic": True}
            elif unsafe == "dirty":
                sim.dirty("synthetic-terminal")
            elif unsafe == "pending":
                sim.db.execute("UPDATE tasks SET state='proposed'")
                sim.db.commit()
            else:
                sim.settings.actuation_enabled = True
            fail(sim)
            sim.journal_recovery_next_at = 0
            calls = network["calls"]
            assert not await sim.recover_idle_journal()
            assert network["calls"] == calls and sim.journal_failed
            assert not sim.journal_recovery_allowed
        finally:
            await close(sim)

    asyncio.run(run())


@pytest.mark.parametrize("change", ["new", "missing", "modified", "pending"])
def test_recovery_never_repairs_changed_or_pending_remote_history(remote, tmp_path, change):
    path, _ = remote

    async def run():
        sim = await prepared(tmp_path)
        try:
            fail(sim)
            with sqlite3.connect(path) as db:
                if change == "new":
                    db.execute(
                        f"INSERT INTO {TABLE} VALUES(?,?,?,?,?,?,?)",
                        ("new", "operator", "{}", "completed", "", 2.0, 0.0),
                    )
                elif change == "missing":
                    db.execute(f"DELETE FROM {TABLE}")
                elif change == "modified":
                    db.execute(f"UPDATE {TABLE} SET position=0.7")
                else:
                    db.execute(f"UPDATE {TABLE} SET state='proposed'")
            baseline = history(path)
            expire(path)
            sim.journal_recovery_next_at = 0
            assert not await sim.recover_idle_journal()
            assert sim.journal_failed and not sim.journal_recovery_allowed
            assert history(path) == baseline
        finally:
            await close(sim)

    asyncio.run(run())


def test_live_other_owner_is_never_displaced_and_attempts_are_bounded(remote, tmp_path):
    path, _ = remote

    async def run():
        sim = await prepared(tmp_path)
        try:
            fail(sim)
            with sqlite3.connect(path) as db:
                db.execute(
                    f"UPDATE {LEASE} SET owner='healthy-other-owner',expires=unixepoch()+3600"
                )
            baseline = history(path)
            for attempt in range(3):
                sim.journal_recovery_next_at = 0
                assert not await sim.recover_idle_journal()
                assert sim.journal_recovery_attempts == attempt + 1
                with sqlite3.connect(path) as db:
                    assert (
                        db.execute(f"SELECT owner FROM {LEASE}").fetchone()[0]
                        == "healthy-other-owner"
                    )
            sim.journal_recovery_next_at = 0
            assert not await sim.recover_idle_journal()
            assert sim.journal_recovery_attempts == 3 and not sim.journal_recovery_allowed
            assert history(path) == baseline
        finally:
            await close(sim)

    asyncio.run(run())


def test_after_recovery_old_browser_session_is_refused_and_password_login_is_required(
    remote, tmp_path
):
    path, _ = remote
    with client(config(tmp_path)) as api:
        login(api)
        sim = api.app.state.sim
        api.portal.call(lambda: fail(sim))
        assert api.get("/api/v1/ready").status_code == 503
        expire(path)
        api.portal.call(lambda: setattr(sim, "journal_recovery_next_at", 0))
        api.portal.call(sim.recover_idle_journal)
        assert api.get("/api/v1/ready").status_code == 200
        assert api.get("/api/v1/auth/me").status_code == 401
        login(api)
        assert api.get("/api/v1/status").json()["stopped"] is True
        assert api.get("/api/v1/tasks").json()["tasks"] == []


@pytest.mark.parametrize("terminal", ["completed", "cancelled"])
def test_cancelling_terminal_task_after_latch_does_not_poison_idle_recovery(
    remote, tmp_path, terminal
):
    path, _ = remote
    with client(config(tmp_path)) as api:
        login(api)
        sim = api.app.state.sim
        task_id = str(uuid4())

        async def seed():
            sim.db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,?)",
                (task_id, "operator", "{}", terminal, "", 1.5, 0.25),
            )
            sim.db.commit()
            sim.dirty(task_id)
            await sim.flush_journal()

        api.portal.call(seed)
        baseline = history(path)
        api.portal.call(lambda: fail(sim))
        assert sim.journal_recovery_allowed and not sim.journal_dirty
        # The route remains unavailable while the journal is latched; an already
        # terminal task does not become a new, unacknowledged history mutation.
        assert api.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 503
        assert not sim.journal_dirty
        expire(path)
        api.portal.call(lambda: setattr(sim, "journal_recovery_next_at", 0))
        assert api.portal.call(sim.recover_idle_journal)
        assert history(path) == baseline
        assert api.portal.call(
            lambda: sim.stopped and sim.active is None and not sim.journal_failed
        )


def test_unacknowledged_local_terminal_change_stays_latched_without_a_database_request(
    remote, tmp_path
):
    _, network = remote

    async def run():
        sim = await prepared(tmp_path)
        try:
            sim.db.execute("UPDATE tasks SET position=0.6")
            sim.db.commit()
            fail(sim)
            sim.journal_recovery_next_at = 0
            calls = network["calls"]
            assert not await sim.recover_idle_journal()
            assert network["calls"] == calls and sim.journal_failed
            assert not sim.journal_recovery_allowed
        finally:
            await close(sim)

    asyncio.run(run())


def test_repeated_failure_does_not_renew_the_attempt_allowance(remote, tmp_path):
    path, _ = remote

    async def run():
        sim = await prepared(tmp_path)
        try:
            fail(sim)
            with sqlite3.connect(path) as db:
                db.execute(f"UPDATE {LEASE} SET owner='other',expires=unixepoch()+3600")
            for _ in range(3):
                sim.journal_recovery_next_at = 0
                await sim.recover_idle_journal()
            fail(sim)
            sim.journal_recovery_next_at = 0
            assert not sim.journal_recovery_allowed
            assert not await sim.recover_idle_journal()
            assert sim.journal_recovery_attempts == 3
        finally:
            await close(sim)

    asyncio.run(run())


def test_concurrent_recovery_observers_share_one_backoff_bound(remote, tmp_path):
    path, _ = remote

    async def run():
        sim = await prepared(tmp_path)
        try:
            fail(sim)
            with sqlite3.connect(path) as db:
                db.execute(f"UPDATE {LEASE} SET owner='other',expires=unixepoch()+3600")
            sim.journal_recovery_next_at = 0
            results = await asyncio.gather(sim.recover_idle_journal(), sim.recover_idle_journal())
            assert results == [False, False]
            assert sim.journal_recovery_attempts == 1
            assert sim.journal_recovery_next_at > time.monotonic()
        finally:
            await close(sim)

    asyncio.run(run())


def test_cancelled_recovery_never_clears_the_failure_or_replays_work(remote, tmp_path, monkeypatch):
    path, _ = remote

    async def run():
        sim = await prepared(tmp_path)
        original = TursoJournal.initialize
        entered = asyncio.Event()
        hold = asyncio.Event()

        async def wait(self):
            result = await original(self)
            entered.set()
            await hold.wait()
            return result

        try:
            fail(sim)
            expire(path)
            monkeypatch.setattr(TursoJournal, "initialize", wait)
            sim.journal_recovery_next_at = 0
            task = asyncio.create_task(sim.recover_idle_journal())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert sim.journal_failed and sim.stopped and sim.active is None
            assert len(history(path)) == 1
        finally:
            await close(sim)

    asyncio.run(run())
