"""Lease acknowledgement recovery observes once and never replays a write."""

import asyncio
import sqlite3
import time
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from robot_api.turso import LEASE, JournalFailure, TursoJournal
from tests.test_turso import TOKEN, URL, client, config
from tests.test_turso import remote as remote


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ReadError])
def test_delayed_lost_renewal_ack_confirms_fresh_owner_without_replaying(remote, failure):
    _, network = remote
    statements = []

    def lose_ack(sql):
        statements.append(sql)
        if sql.startswith(f"UPDATE {LEASE}"):
            network["delay"] = 0
            raise failure("synthetic-private-response")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network.update(after_execute=lose_ack, delay=1.1)
            await journal.renew()
            assert len(statements) == 2
            assert statements[0].startswith("UPDATE")
            assert statements[1].startswith("SELECT")
            assert "owner=? AND expires>=unixepoch()+18" in statements[1]
        finally:
            network.update(after_execute=None, delay=0)
            await journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("uncertainty", ["foreign", "missing", "expired", "too_near"])
def test_uncertain_or_short_lived_lease_cannot_refresh_authority(remote, uncertainty):
    path, network = remote
    statements = []

    def lose_ack(sql):
        statements.append(sql)
        if sql.startswith(f"UPDATE {LEASE}"):
            with sqlite3.connect(path) as db:
                if uncertainty == "foreign":
                    db.execute(f"UPDATE {LEASE} SET owner='replacement'")
                elif uncertainty == "missing":
                    db.execute(f"DELETE FROM {LEASE}")
                else:
                    db.execute(
                        f"UPDATE {LEASE} SET expires=unixepoch()+?",
                        (-1 if uncertainty == "expired" else 17,),
                    )
            raise httpx.ReadTimeout("synthetic-private-response")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network["after_execute"] = lose_ack
            with pytest.raises(JournalFailure) as caught:
                await journal.renew()
            assert caught.value.category == "timeout"
            assert caught.value.phase == "lease_renew"
            assert caught.value.reconciliation == "not_confirmed"
            assert len(statements) == 2
            assert sum(sql.startswith("UPDATE") for sql in statements) == 1
        finally:
            network["after_execute"] = None
            await journal.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "remaining,elapsed,accepted", [(20, 0, True), (18, 0, True), (17, 0, False), (18, 1, False)]
)
def test_uncommitted_renewal_observes_only_safely_remaining_ownership(
    remote, remaining, elapsed, accepted
):
    path, network = remote
    statements = []
    clock = SimpleNamespace(now=2_000_000_000)
    network["database_clock"] = lambda: clock.now

    def lose_request(sql):
        statements.append(sql)
        if sql.startswith(f"UPDATE {LEASE}"):
            # The provider never executes this renewal. Reconciliation must
            # neither assume it committed nor rewrite the existing expiry.
            clock.now += elapsed
            raise httpx.ReadTimeout("synthetic-private-request")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            with sqlite3.connect(path) as db:
                db.execute(f"UPDATE {LEASE} SET expires=?", (clock.now + remaining,))
                before = db.execute(f"SELECT owner,expires FROM {LEASE}").fetchone()
            network["before_execute"] = lose_request
            if accepted:
                await journal.renew()
            else:
                with pytest.raises(JournalFailure) as caught:
                    await journal.renew()
                assert caught.value.reconciliation == "not_confirmed"
            assert len(statements) == 2
            assert statements[0].startswith("UPDATE") and statements[1].startswith("SELECT")
            with sqlite3.connect(path) as db:
                assert db.execute(f"SELECT owner,expires FROM {LEASE}").fetchone() == before
        finally:
            network["before_execute"] = None
            await journal.close()

    asyncio.run(run())


@pytest.mark.parametrize("uncertainty", ["timeout", "malformed"])
def test_unavailable_confirmation_preserves_redacted_failure(remote, uncertainty):
    _, network = remote
    statements = []

    def lose_ack(sql):
        statements.append(sql)
        if sql.startswith(f"UPDATE {LEASE}"):
            network["malformed"] = uncertainty == "malformed"
            raise httpx.ReadTimeout("synthetic-private-response")
        raise httpx.ReadError("synthetic-private-response")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network["after_execute"] = lose_ack
            with pytest.raises(JournalFailure) as caught:
                await journal.renew()
            assert caught.value.phase == "lease_renew"
            assert caught.value.transport_kind == "ReadTimeout"
            assert caught.value.reconciliation == "unavailable"
            assert "synthetic-private" not in str(caught.value) + repr(vars(caught.value))
            assert sum(sql.startswith("UPDATE") for sql in statements) == 1
        finally:
            network.update(after_execute=None, malformed=False)
            await journal.close()

    asyncio.run(run())


def test_renewal_confirmation_has_its_readonly_five_second_deadline(remote):
    _, network = remote

    def lose_ack(sql):
        if sql.startswith(f"UPDATE {LEASE}"):
            network["delay"] = 6
            raise httpx.ReadTimeout("synthetic-response")

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            network["after_execute"] = lose_ack
            calls, started = network["calls"], time.monotonic()
            with pytest.raises(JournalFailure) as caught:
                await asyncio.wait_for(journal.renew(), timeout=5.5)
            assert caught.value.reconciliation == "unavailable"
            assert network["calls"] == calls + 2
            assert 4.8 <= time.monotonic() - started < 5.5
        finally:
            network.update(after_execute=None, delay=0)
            await journal.close()

    asyncio.run(run())


def test_explicit_provider_denial_does_not_attempt_confirmation(remote):
    _, network = remote

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            calls = network["calls"]
            network["offline"] = True
            with pytest.raises(JournalFailure) as caught:
                await journal.renew()
            assert caught.value.category == "http_status"
            assert caught.value.reconciliation == "not_attempted"
            assert network["calls"] == calls + 1
        finally:
            network["offline"] = False
            await journal.close()

    asyncio.run(run())


def test_confirmed_ack_loss_keeps_development_runtime_ready_and_stopped(remote, tmp_path):
    _, network = remote
    with client(config(tmp_path)) as api:
        sim = api.app.state.sim

        def lose_ack(sql):
            if sql.startswith(f"UPDATE {LEASE}"):
                raise httpx.ReadTimeout("synthetic-response")

        network["after_execute"] = lose_ack
        try:
            api.portal.call(sim.refresh_journal_lease)
            assert api.get("/api/v1/ready").status_code == 200
            assert api.portal.call(lambda: sim.stopped and sim.active is None)
            assert api.portal.call(lambda: not sim.journal_failed)
        finally:
            network["after_execute"] = None


@pytest.mark.parametrize("committed", [False, True])
def test_full_write_timeout_keeps_a_bounded_readonly_confirmation_budget(remote, committed):
    _, network = remote

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            execute = journal.execute
            statements = []

            async def uncertain(sql, args=(), *, phase="query"):
                statements.append(sql)
                if phase == "lease_renew":
                    if committed:
                        await execute(sql, args, phase=phase)
                    await asyncio.sleep(2.01)
                    raise JournalFailure("timeout", phase, 2010, transport_kind="ReadTimeout")
                return await execute(sql, args, phase=phase)

            journal.execute = uncertain
            started = time.monotonic()
            await journal.renew()
            assert 2 <= time.monotonic() - started < 3
            assert len(statements) == 2
            assert statements[0].startswith("UPDATE")
            assert statements[1].startswith("SELECT")
            assert "owner=? AND expires>=unixepoch()+18" in statements[1]
        finally:
            await journal.close()

    asyncio.run(run())


def test_confirmation_after_full_write_timeout_still_fails_within_total_budget(remote):
    _, network = remote

    async def run():
        journal = TursoJournal(URL, SecretStr(TOKEN))
        try:
            await journal.initialize()
            execute = journal.execute
            statements = []

            async def uncertain(sql, args=(), *, phase="query"):
                statements.append(sql)
                if phase == "lease_renew":
                    await asyncio.sleep(2.01)
                    raise JournalFailure("timeout", phase, 2010, transport_kind="ReadTimeout")
                network["delay"] = 6
                return await execute(sql, args, phase=phase)

            journal.execute = uncertain
            started = time.monotonic()
            with pytest.raises(JournalFailure) as caught:
                await asyncio.wait_for(journal.renew(), timeout=7.5)
            assert 6.8 <= time.monotonic() - started < 7.5
            assert caught.value.reconciliation == "unavailable"
            assert len(statements) == 2
            assert sum(sql.startswith("UPDATE") for sql in statements) == 1
        finally:
            network["delay"] = 0
            await journal.close()

    asyncio.run(run())
