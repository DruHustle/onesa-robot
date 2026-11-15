import json
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from robot_api import inspection
from robot_api.main import Settings, create_app
from robot_api.security import password_hash

PASSWORD = "inspection-test-only-password"
HASH = password_hash(PASSWORD)


@pytest.fixture
def config(tmp_path):
    return Settings(operator_password_hash=HASH, database_path=tmp_path / "inspection.sqlite3")


@pytest.fixture
def api(config):
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        response = client.post(
            "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
        )
        assert response.status_code == 200
        client.headers["X-CSRF-Token"] = response.json()["csrf"]
        assert client.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 200
        yield client


def plan(api, scenario="normal", **changes):
    body = {
        "command_id": str(uuid4()),
        "robot_profile_id": "sim-inspector",
        "scenario": scenario,
        "seed": 101,
        **changes,
    }
    result = api.post("/api/v1/missions", json=body)
    assert result.status_code == 201, result.text
    return result.json()


def approve(api, detail):
    result = api.post(
        f"/api/v1/missions/{detail['id']}/approve",
        json={
            "password": PASSWORD,
            "expected_plan_sha256": detail["record"]["plan_sha256"],
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def advance_clock(api, seconds):
    # Only the isolated test's simulator monotonic origin is advanced. SQLite
    # remains on its owning application thread; no sleep, replay or hardware.
    def advance():
        sim = api.app.state.sim
        sim.active["started"] = time.monotonic() - seconds
        sim.active["heartbeat"] = time.monotonic()
        sim.tick()

    api.portal.call(advance)


@pytest.mark.parametrize(
    "scenario,outcome,kinds",
    [
        ("normal", "completed_normal", []),
        ("threshold", "completed_findings", ["threshold"]),
        ("discrepancy", "completed_findings", ["discrepancy"]),
        ("missing", "completed_findings", ["missing"]),
        ("stale", "completed_findings", ["stale", "stale", "stale"]),
        ("units", "completed_findings", ["unit_mismatch"]),
    ],
)
def test_real_planner_approval_execution_report_and_read_only_replay(api, scenario, outcome, kinds):
    detail = plan(api, scenario)
    approved = approve(api, detail)
    assert approved["record"]["started_at"] is not None
    advance_clock(api, 6.1)
    final = api.get(f"/api/v1/missions/{detail['id']}").json()
    assert final["state"] == "completed"
    assert final["record"]["outcome"] == outcome
    assert [f["kind"] for f in final["report"]["findings"]] == kinds
    assert final["report"]["execution_complete"] is True
    assert final["report"]["replay"]["read_only"] is True
    assert final["report"]["metrics"]["scope"] == "synthetic scenario evaluation"
    assert len(final["record"]["observations"]) == 3
    assert all(o["source"] == "scenario_observation" for o in final["record"]["observations"])
    assert {e["kind"] for e in final["record"]["events"]} >= {
        "planned",
        "approved",
        "execution_started",
        "step_completed",
        "completed",
    }
    exported = api.get(f"/api/v1/missions/{detail['id']}/export")
    assert exported.status_code == 200 and exported.json() == final
    assert "attachment" in exported.headers["content-disposition"]
    before = api.get("/api/v1/status").json()
    assert api.get(f"/api/v1/missions/{detail['id']}").json() == final
    after = api.get("/api/v1/status").json()
    assert before["position_radians"] == after["position_radians"]
    assert before["active_task"] is after["active_task"] is None
    assert (
        api.post(
            f"/api/v1/missions/{detail['id']}/approve",
            json={
                "password": PASSWORD,
                "expected_plan_sha256": detail["record"]["plan_sha256"],
            },
        ).status_code
        == 409
    )


def test_interrupted_scenario_is_not_a_completed_anomaly_and_has_no_late_report_step(api):
    detail = plan(api, "interrupted")
    approve(api, detail)
    advance_clock(api, 6.1)
    final = api.get(f"/api/v1/missions/{detail['id']}").json()
    assert final["state"] == "cancelled" and final["record"]["outcome"] == "interrupted"
    assert final["report"]["execution_complete"] is False
    assert final["record"]["events"][-1]["detail"] == "scenario_interrupted"
    assert not any(e["detail"] in ("compare_numeric", "report") for e in final["record"]["events"])
    assert api.get("/api/v1/status").json()["stopped"] is True
    assert final["report"]["metrics"]["recovery"] == "explicit_new_run_required"


def test_capability_plan_hash_expiry_duplicate_and_legacy_bypass_refused(api):
    bad = api.post(
        "/api/v1/missions",
        json={
            "command_id": str(uuid4()),
            "robot_profile_id": "display-only",
            "scenario": "normal",
        },
    )
    assert bad.status_code == 422
    detail = plan(api)
    assert (
        api.post(
            "/api/v1/missions",
            json={
                "command_id": detail["id"],
                "robot_profile_id": "sim-inspector",
                "scenario": "normal",
            },
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/api/v1/missions/{detail['id']}/approve",
            json={
                "password": PASSWORD,
                "expected_plan_sha256": "0" * 64,
            },
        ).status_code
        == 409
    )
    assert (
        api.post(
            f"/api/v1/tasks/{detail['id']}/approve",
            json={
                "password": PASSWORD,
            },
        ).status_code
        == 409
    )
    forged = {
        "command_id": str(uuid4()),
        "issued_at": time.time(),
        "target_radians": 0.1,
        "duration_seconds": 6,
        "inspection": detail["record"],
    }
    assert api.post("/api/v1/tasks", json=forged).status_code == 422
    assert api.get("/api/v1/status").json()["active_task"] is None


def test_cancel_stop_timeout_and_persistence_survive_application_restart(api, config):
    detail = plan(api)
    approve(api, detail)
    advance_clock(api, 3.6)
    assert api.post("/api/v1/stop").status_code == 200
    stopped = api.get(f"/api/v1/missions/{detail['id']}").json()
    assert stopped["record"]["outcome"] == "interrupted"
    assert len(stopped["record"]["observations"]) == 3
    assert stopped["record"]["events"][-1]["detail"] == "operator_stop"
    assert api.post("/api/v1/reset", json={"password": PASSWORD}).status_code == 200
    timed = plan(api)
    approve(api, timed)
    advance_clock(api, 9.1)
    failed = api.get(f"/api/v1/missions/{timed['id']}").json()
    assert failed["record"]["outcome"] == "failed"
    assert failed["record"]["events"][-1]["detail"] == "mission_timeout"
    with TestClient(create_app(config)) as second:
        second.headers["Origin"] = config.public_origin
        assert second.get(f"/api/v1/missions/{detail['id']}").status_code == 401
        response = second.post(
            "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
        )
        second.headers["X-CSRF-Token"] = response.json()["csrf"]
        assert second.get(f"/api/v1/missions/{detail['id']}").json() == stopped
        assert second.get(f"/api/v1/missions/{timed['id']}").json() == failed
        assert second.get("/api/v1/status").json()["stopped"] is True


@pytest.mark.parametrize("phase", ["proposed", "running"])
def test_corrupt_inspection_record_cannot_prevent_operator_stop(api, phase):
    detail = plan(api)
    if phase == "running":
        approve(api, detail)
    # A valid JSON object with malformed inspection content bypasses the legacy
    # JSON-decode guard. Stop must remain usable and preserve the bad source bytes.
    corrupt = '{"inspection":{"invalid":"synthetic-test-only"}}'

    def corrupt_journal():
        sim = api.app.state.sim
        sim.db.execute("UPDATE tasks SET proposal=? WHERE id=?", (corrupt, detail["id"]))
        sim.db.commit()

    api.portal.call(corrupt_journal)
    response = api.post("/api/v1/stop")
    assert response.status_code == 200, response.text
    status = api.get("/api/v1/status").json()
    assert status["stopped"] and status["active_task"] is None
    assert status["physical_actuation"] is False

    def stored_record():
        sim = api.app.state.sim
        return tuple(
            sim.db.execute(
                "SELECT proposal,state,reason FROM tasks WHERE id=?", (detail["id"],)
            ).fetchone()
        )

    assert api.portal.call(stored_record) == (
        corrupt,
        "cancelled",
        "inspection_integrity",
    )
    assert (
        api.post(f"/api/v1/tasks/{detail['id']}/approve", json={"password": PASSWORD}).status_code
        == 409
    )


def test_foreign_owner_aggregate_detail_and_export_are_denied(api):
    detail = plan(api)

    def change_owner():
        sim = api.app.state.sim
        sim.db.execute("UPDATE tasks SET owner='different-operator' WHERE id=?", (detail["id"],))
        sim.db.commit()

    api.portal.call(change_owner)
    assert not api.get("/api/v1/missions").json()["missions"]
    assert api.get(f"/api/v1/missions/{detail['id']}").status_code == 404
    assert api.get(f"/api/v1/missions/{detail['id']}/export").status_code == 404


def test_validation_and_catalog_are_strict_and_bounded(api):
    assert api.get("/api/v1/missions/catalog").json()["physical_actuation"] is False
    for changes in [{"seed": True}, {"seed": -1}, {"scenario": "shell"}, {"command": "run"}]:
        response = api.post(
            "/api/v1/missions",
            json={
                "command_id": str(uuid4()),
                "robot_profile_id": "sim-inspector",
                "scenario": "normal",
                **changes,
            },
        )
        assert response.status_code == 422
    with pytest.raises(ValidationError):
        inspection.MissionRequest(
            command_id=uuid4(), robot_profile_id="sim-inspector", scenario="normal", seed=True
        )
    detail = plan(api)
    assert len(json.dumps(detail["record"]).encode()) < inspection.RECORD_BYTES


def context_fixture(clock=None):
    from datetime import UTC, datetime

    stamp = datetime.fromtimestamp(clock or time.time(), UTC).isoformat()
    return {
        "contract_version": 1,
        "asset": {"id": str(uuid4()), "name": "Synthetic contract fixture"},
        "capabilities": {"read_only": True, "actuation": False},
        "operating_state": [
            {
                "source_id": str(uuid4()),
                "binding": {"tag": metric, "unit": unit},
                "freshness": "good",
                "connection": "connected",
                "reading": {"value": value, "unit": unit, "mode": "live", "observed_at": stamp},
            }
            for metric, unit, value in [
                ("temperature", "Cel", 46.0),
                ("pressure", "bar", 3.2),
                ("vibration", "mm/s", 2.1),
            ]
        ],
    }


def context_request(data):
    return {
        "document": json.dumps(data),
        "mapping": {metric: metric for metric in ["temperature", "pressure", "vibration"]},
    }


def test_context_adapter_matches_actual_v1_shape_and_preserves_snapshot_provenance(api):
    assert (
        api.post("/api/v1/missions/context", json=context_request(context_fixture())).status_code
        == 409
    )
    raw = context_fixture()
    request = inspection.ContextRequest(**context_request(raw))
    asset = inspection.import_context(request, time.time())
    import hashlib

    assert asset.source_sha256 == hashlib.sha256(request.document.encode()).hexdigest()
    assert asset.source_asset_id == raw["asset"]["id"]
    assert len(asset.imported_telemetry) == 3
    assert asset.imported_telemetry[0].source_unit == "Cel"
    assert asset.imported_telemetry[0].unit == "°C"
    assert all(
        o.source == "reviewed_import" and o.source_ref.startswith("ai-robotics/v1/")
        for o in asset.imported_telemetry
    )
    record = inspection.make_record(
        inspection.MissionRequest(
            command_id=uuid4(), robot_profile_id="sim-inspector", scenario="normal"
        ),
        0.0,
        time.time(),
        asset,
    )
    assert inspection.scenario_readings(record.plan)[1] == asset.imported_telemetry
    assert record.plan_sha256 == inspection.digest(record.plan)


@pytest.mark.parametrize(
    "change",
    [
        "units",
        "future",
        "synthetic",
        "actuation",
        "duplicate_tag",
        "invalid_number",
        "missing_tag",
        "version",
    ],
)
def test_context_rejects_unsafe_or_ambiguous_mapping(change):
    raw = context_fixture()
    first = raw["operating_state"][0]
    if change == "units":
        first["binding"]["unit"] = "rpm"
    elif change == "future":
        raw = context_fixture(time.time() + 600)
    elif change == "synthetic":
        first["reading"]["mode"] = "simulated"
    elif change == "actuation":
        raw["capabilities"]["actuation"] = True
    elif change == "duplicate_tag":
        raw["operating_state"].append(first)
    elif change == "invalid_number":
        first["reading"]["value"] = True
    elif change == "missing_tag":
        raw["operating_state"].pop()
    elif change == "version":
        raw["contract_version"] = True
    with pytest.raises(ValueError):
        inspection.import_context(inspection.ContextRequest(**context_request(raw)), time.time())


def test_context_malformed_duplicate_deep_json_and_unavailable_quality_fail_closed():
    for document in [
        '{"contract_version":1,"contract_version":1}',
        "[" * 17 + "0" + "]" * 17,
        '{"x":NaN}',
        "null",
    ]:
        with pytest.raises(ValueError):
            inspection.import_context(
                inspection.ContextRequest(
                    document=document,
                    mapping={metric: metric for metric in ["temperature", "pressure", "vibration"]},
                ),
                time.time(),
            )
    raw = context_fixture()
    raw["operating_state"][0]["connection"] = "revoked"
    raw["operating_state"][1]["reading"] = None
    asset = inspection.import_context(
        inspection.ContextRequest(**context_request(raw)), time.time()
    )
    assert asset.imported_telemetry[0].quality == "unknown"
    assert asset.imported_telemetry[1].value is None


def test_context_cache_is_session_bound_expires_and_is_discarded_on_logout(config):
    config.context_import_enabled = True
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin
        login = client.post(
            "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
        ).json()
        client.headers["X-CSRF-Token"] = login["csrf"]
        client.post("/api/v1/reset", json={"password": PASSWORD})
        imported = client.post("/api/v1/missions/context", json=context_request(context_fixture()))
        assert imported.status_code == 200
        identifier = imported.json()["id"]
        detail = plan(client, context_id=identifier)
        assert detail["record"]["plan"]["asset"]["context_kind"] == "reviewed_import"
        original = detail["record"]["plan_sha256"]
        assert client.post("/api/v1/auth/logout").status_code == 200
        assert client.portal.call(lambda: len(client.app.state.sim.inspection_contexts)) == 0
        login = client.post(
            "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
        ).json()
        client.headers["X-CSRF-Token"] = login["csrf"]
        assert (
            client.post(
                "/api/v1/missions",
                json={
                    "command_id": str(uuid4()),
                    "robot_profile_id": "sim-inspector",
                    "scenario": "normal",
                    "context_id": identifier,
                },
            ).status_code
            == 409
        )
        assert (
            client.get(f"/api/v1/missions/{detail['id']}").json()["record"]["plan_sha256"]
            == original
        )


def test_expired_and_changed_execution_conditions_invalidate_approval(api):
    detail = plan(api)

    def expire():
        sim = api.app.state.sim
        raw = json.loads(
            sim.db.execute("SELECT proposal FROM tasks WHERE id=?", (detail["id"],)).fetchone()[0]
        )
        raw["issued_at"] -= 30
        sim.db.execute("UPDATE tasks SET proposal=? WHERE id=?", (json.dumps(raw), detail["id"]))
        sim.db.commit()

    api.portal.call(expire)
    response = api.post(
        f"/api/v1/missions/{detail['id']}/approve",
        json={"password": PASSWORD, "expected_plan_sha256": detail["record"]["plan_sha256"]},
    )
    assert response.status_code == 422
    assert api.get("/api/v1/status").json()["active_task"] is None
    second = plan(api)
    api.portal.call(lambda: setattr(api.app.state.sim, "position", 0.2))
    assert (
        api.post(
            f"/api/v1/missions/{second['id']}/approve",
            json={"password": PASSWORD, "expected_plan_sha256": second["record"]["plan_sha256"]},
        ).status_code
        == 422
    )


def test_running_mission_excludes_concurrent_work_and_heartbeat_loss_interrupts(api):
    first, second = plan(api), plan(api)
    approve(api, first)
    assert (
        api.post(
            f"/api/v1/missions/{second['id']}/approve",
            json={"password": PASSWORD, "expected_plan_sha256": second["record"]["plan_sha256"]},
        ).status_code
        == 409
    )

    def lose_heartbeat():
        sim = api.app.state.sim
        sim.active["heartbeat"] = time.monotonic() - 20
        sim.tick()

    api.portal.call(lose_heartbeat)
    final = api.get(f"/api/v1/missions/{first['id']}").json()
    assert final["record"]["outcome"] == "interrupted"
    assert not final["report"]["execution_complete"]
    assert api.get("/api/v1/status").json()["stopped"]


def test_context_session_isolation_expiry_and_finite_cache_budget(config):
    config.context_import_enabled = True
    with TestClient(create_app(config)) as client:
        client.headers["Origin"] = config.public_origin

        def login():
            response = client.post(
                "/api/v1/auth/login", json={"name": "operator", "password": PASSWORD}
            )
            assert response.status_code == 200
            client.headers["X-CSRF-Token"] = response.json()["csrf"]

        def submit(identifier):
            return client.post(
                "/api/v1/missions",
                json={
                    "command_id": str(uuid4()),
                    "robot_profile_id": "sim-inspector",
                    "scenario": "normal",
                    "context_id": identifier,
                },
            )

        login()
        client.post("/api/v1/reset", json={"password": PASSWORD})
        identifiers = []
        for _ in range(4):
            response = client.post(
                "/api/v1/missions/context", json=context_request(context_fixture())
            )
            assert response.status_code == 200
            identifiers.append(response.json()["id"])
        assert (
            client.post(
                "/api/v1/missions/context", json=context_request(context_fixture())
            ).status_code
            == 429
        )
        login()  # Same operator, distinct session; old context grants no access.
        assert submit(identifiers[0]).status_code == 409
        response = client.post("/api/v1/missions/context", json=context_request(context_fixture()))
        identifier = response.json()["id"]

        def expire():
            sim = client.app.state.sim
            owner, _, asset = sim.inspection_contexts[identifier]
            sim.inspection_contexts[identifier] = (owner, 0.0, asset)

        client.portal.call(expire)
        assert submit(identifier).status_code == 409


def test_import_retains_declared_freshness_revision_and_original_times():
    raw = context_fixture(time.time() - 120)
    raw["asset"]["revision"] = 7
    raw["model_revision"] = {"id": str(uuid4())}
    for source in raw["operating_state"]:
        source["freshness"] = "stale"
    asset = inspection.import_context(
        inspection.ContextRequest(**context_request(raw)), time.time()
    )
    assert asset.source_asset_revision == 7
    assert str(asset.source_model_id) == raw["model_revision"]["id"]
    assert asset.context_imported_at - asset.source_observed_at >= 119
    assert all(o.declared_freshness == "stale" for o in asset.imported_telemetry)
    record = inspection.make_record(
        inspection.MissionRequest(
            command_id=uuid4(), robot_profile_id="sim-inspector", scenario="normal"
        ),
        0.0,
        time.time(),
        asset,
    )
    record = inspection.approve(record, time.time(), "operator")
    completed, failure = inspection.advance(record, 6.1, time.time())
    assert failure is None
    assert [finding.kind for finding in completed.findings] == ["stale"] * 3
