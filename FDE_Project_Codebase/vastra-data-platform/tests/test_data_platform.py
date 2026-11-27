"""Week 2 data platform tests. Each test maps to a 'done when' line of the build plan or a demo step.

Run: python -m pytest -v      (from inside vastra-data-platform; no servers needed)
"""
import json
import os
import sqlite3

import httpx
import pandas as pd
import pytest

from vastra_data import (clean, config, contracts, db, demand, events, failed, fitness, governance, load, ready,
                         simulate, sources)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _world(tmp_path, monkeypatch, **sim):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "inbox"))
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setenv("LEGACY_DB_PATH", str(tmp_path / "no-legacy.db"))
    monkeypatch.setenv("CONTRACTS_DIR", os.path.join(ROOT, "contracts"))
    monkeypatch.setenv("REFERENCE_DIR", os.path.join(ROOT, "reference"))
    monkeypatch.setenv("LOAD_RETRY_BASE_S", "2")
    monkeypatch.setenv("VASTRA_API_URL", "http://127.0.0.1:9")      # no live API in unit tests
    monkeypatch.setenv("VASTRA_DATA_USER", "test.engineer")
    config.reload_settings()
    db.init()
    governance.sync_catalogue()
    if sim:
        simulate.simulate(**sim)


def _drop(source, rows, name="drop.jsonl"):
    folder = os.path.join(config.settings.inbox_dir, source)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _rec(event_id="e1", event_type="stock.counted", source="wms", occurred_at="2026-11-25T00:30:00Z", **payload):
    return {"event_id": event_id, "event_type": event_type, "occurred_at": occurred_at, "source": source,
            "actor": "test", "payload": payload or {"sku": "SKU00003", "location_id": "ST012", "available": 4}}


def _count(sql):
    con = db.connect(restricted=True)
    try:
        return con.execute(sql).fetchone()[0]
    finally:
        con.close()


@pytest.fixture()
def small(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch, end_date="2026-11-24", days=150, n_stores=6, n_skus=6, seed=7)
    for s in ("erp", "wms", "pos", "legacy"):
        load.run(s)
    clean.build()
    ready.build()
    return tmp_path


# ------------------------------------------------------------------ Lab 1 / demo 1-2: contracts, no silent failure
def test_renamed_field_is_rejected_alerted_fixed_and_loaded_exactly_once(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    bad = _rec(sku="SKU00003", location_id="ST012", avail=4, on_order=20)
    _drop("wms", [bad])
    r = load.run("wms")
    assert r["failed"] == 1 and r["loaded"] == 0
    [f] = failed.list_failed("open")
    assert "payload.avail: unknown field" in f["reason"] and "payload.available: Field required" in f["reason"]
    with open(config.settings.alert_log) as fh:
        assert "record failed its contract" in fh.read()
    failed.fix(f["failed_id"], renames={"payload.avail": "payload.available"})
    assert failed.rerun() == {"tried": 1, "loaded": 1, "already_present": 0, "still_failing": 0}
    assert failed.rerun()["tried"] == 0                      # nothing left to rerun
    _drop("wms", [{**bad, "payload": {"sku": "SKU00003", "location_id": "ST012", "available": 4}}], "resend.jsonl")
    assert load.run("wms")["duplicates"] == 1                # the source re-sends it: still counted once
    assert _count("SELECT COUNT(*) FROM raw.events") == 1


@pytest.mark.parametrize("change, reason", [
    ({"payload": {"sku": "SKU00003", "location_id": "ST012", "available": -3}}, "greater than or equal to 0"),
    ({"occurred_at": "25/11/2026 06:00"}, "must be ISO-8601"),
    ({"occurred_at": "2026-11-25 06:00:00"}, "has no time zone"),
    ({"payload": {"sku": "SKU00003", "location_id": "ST012"}}, "payload.available: Field required"),
    ({"source": "pos"}, "may not publish"),
])
def test_bad_data_goes_to_failed_records_without_retry(tmp_path, monkeypatch, change, reason):
    _world(tmp_path, monkeypatch)
    slept = []
    monkeypatch.setattr(load.time, "sleep", slept.append)
    _drop("wms", [{**_rec(), **change}])
    r = load.run("wms")
    assert r["failed"] == 1 and slept == []                  # bad data is never retried
    [f] = failed.list_failed("open")
    assert reason in f["reason"] and f["kind"] == "contract"


def test_database_busy_is_retried_with_2_4_8_backoff(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    slept, calls = [], {"n": 0}
    real_tx = db.tx
    monkeypatch.setattr(load.time, "sleep", slept.append)

    def flaky(restricted=False):
        calls["n"] += restricted
        if restricted and calls["n"] <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real_tx(restricted)
    monkeypatch.setattr(load.db, "tx", flaky)
    _drop("wms", [_rec()])
    r = load.run("wms")
    assert r["loaded"] == 1 and slept == [2.0, 4.0]


def test_database_down_after_three_tries_lands_in_failed_records(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    slept = []
    real_tx = db.tx
    monkeypatch.setattr(load.time, "sleep", slept.append)

    def down(restricted=False):
        if restricted:
            raise sqlite3.OperationalError("database is locked")
        return real_tx(restricted)
    monkeypatch.setattr(load.db, "tx", down)
    _drop("wms", [_rec()])
    r = load.run("wms")
    assert r["failed"] == 1 and slept == [2.0, 4.0]          # waits 2 and 4, the third try is the last
    [f] = failed.list_failed("open")
    assert f["kind"] == "database" and "after 3 tries" in f["reason"]
    monkeypatch.setattr(load.db, "tx", real_tx)
    assert failed.rerun()["loaded"] == 1                     # after the fix, rerun loads it


def test_loading_twice_never_counts_twice(small):
    before = _count("SELECT COUNT(*) FROM raw.events")
    with db.tx() as con:
        con.execute("DELETE FROM ops.watermarks")             # force a full re-read of every file
    r = load.run_all()
    assert sum(x["loaded"] for x in r) == 0
    assert _count("SELECT COUNT(*) FROM raw.events") == before


# ------------------------------------------------------------------ Lab 2: clean and protect
def test_clean_fixes_time_zones_codes_and_resent_duplicates(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    _drop("erp", [_rec("s1", "store.upserted", "erp", location_id="ST012", store_name="Mysuru", city="Mysuru",
                       region="South", tier="tier-3", opened_on="2022-01-10"),
                  _rec("p1", "product.upserted", "erp", sku="SKU00003", channel="erp", product_name="Linen kurta",
                       category="kurtas", top500=True)])
    sale = {"business_date": "2026-11-24", "sku": "sku00003 ", "location_id": "st-12", "units": 3, "net_amount": 5400}
    _drop("pos", [_rec("a", "sales.daily", "pos", "2026-11-24 22:30:00", **sale),
                  _rec("a:resend", "sales.daily", "pos", "2026-11-24 22:31:00", **sale),
                  _rec("b", "sales.daily", "pos", "2026-11-24 22:30:00", **{**sale, "sku": "SKU99999"})])
    for s in ("erp", "pos"):
        load.run(s)
    stats = clean.build()
    con = db.connect()
    rows = con.execute("SELECT * FROM clean.sales_lines").fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0]["sku_id"] == "SKU00003" and rows[0]["location_id"] == "ST012"
    assert rows[0]["occurred_at_utc"] == "2026-11-24T17:01:00+00:00"          # 22:31 IST is 17:01 UTC
    assert stats["sales_lines"]["duplicates_removed"] == 1 and stats["sales_lines"]["rejected"] == 1
    [f] = failed.list_failed("open")
    assert f["stage"] == "clean" and "unknown sku" in f["reason"]


def test_personal_details_never_reach_raw_or_failed_records(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    _drop("pos", [_rec("c1", "customer.registered", "pos", "2026-11-20 12:00:00", customer_ref="LOY1", name="Asha Rao",
                       phone="9876543210", email="asha@example.com", home_store="ST012"),
                  _rec("x1", "stock.moved", "pos", "2026-11-20 12:00:00", sku="SKU00003", location_id="ST012",
                       kind="return", qty=1, ref="call 9876543210")])
    load.run("pos")
    con = db.connect(restricted=True)
    raw = con.execute("SELECT payload FROM raw.events WHERE event_id='c1'").fetchone()[0]
    person = con.execute("SELECT * FROM restricted.person_tokens").fetchone()
    con.close()
    assert "Asha" not in raw and "9876543210" not in raw and json.loads(raw)["customer_token"].startswith("cust_")
    assert person["name"] == "Asha Rao"
    [f] = failed.list_failed("open")
    assert "9876543210" not in f["row"] and "looks like an email or phone" in f["reason"]


def test_erasure_unlinks_the_person_and_is_logged(small):
    con = db.connect(restricted=True)
    token = governance.token_for(con, "LOY-TEST-0001")
    con.close()
    assert governance.has_consent(token, "marketing")
    governance.withdraw("LOY-TEST-0001", ["marketing"])
    assert not governance.has_consent(token, "marketing")
    assert governance.erase("LOY-TEST-0001", request_ref="DPR-1")["rows_deleted"] == 1
    assert governance.who_is(token) is None                                   # token can no longer be linked
    assert _count(f"SELECT COUNT(*) FROM clean.customers WHERE customer_token='{token}'") == 1   # token stays
    log = governance.access_log(5)
    assert any(x["action"] == "erase" and token in x["detail"] and x["actor"] == "test.engineer" for x in log)


def test_purpose_limitation_refuses_and_logs(small):
    with pytest.raises(governance.PurposeNotAllowed):
        governance.read_table("clean.customers", "replenishment")
    assert len(governance.read_table("ready.sales_daily", "replenishment")) > 0
    log = governance.access_log(2)
    assert [x["allowed"] for x in log] == [1, 0]


# ------------------------------------------------------------------ ready contracts
def test_ready_tables_pass_their_contracts(small):
    con = db.connect()
    try:
        results = [r for c in contracts.all_ready() for r in contracts.check(con, c)]
    finally:
        con.close()
    assert contracts.blocking_failures(results) == []
    assert _count("SELECT COUNT(*) FROM ready.stock_position WHERE available < 0") == 0


def test_contract_violation_rolls_back_the_ready_build(small, monkeypatch):
    before = _count("SELECT COUNT(*) FROM ready.sales_daily")
    real = contracts.all_ready

    def stricter():
        out = real()
        for c in out:
            if c["table"] == "ready.sales_daily":
                c["columns"]["units_sold"]["max"] = 0                         # impossible promise
        return out
    monkeypatch.setattr(contracts, "all_ready", stricter)
    with pytest.raises(ready.ContractViolation):
        ready.build()
    assert _count("SELECT COUNT(*) FROM ready.sales_daily") == before         # previous good tables kept


def test_contract_changes_are_add_only():
    old = contracts.load("ready.stock_position") if os.path.exists(
        os.path.join(config.settings.contracts_dir, "ready.stock_position.yaml")) else None
    old = old or {"columns": {"available": {"type": "int"}}, "version": 1}
    renamed = {**old, "columns": {("avail" if k == "available" else k): v for k, v in old["columns"].items()}}
    added = {**old, "columns": {**old["columns"], "reserved": {"type": "int", "min": 0}}}
    assert contracts.compatible(old, added) == []
    assert "column 'available' removed or renamed without a new version" in contracts.compatible(old, renamed)
    assert contracts.compatible(old, {**renamed, "version": 2}) == []


# ------------------------------------------------------------------ Lab 3: is the data good enough?
def test_adjusted_demand_replaces_sold_out_zeros_with_past_in_stock_average():
    d = pd.DataFrame({"sku_id": "S", "location_id": "L", "business_date": [f"2026-11-{i:02d}" for i in range(1, 11)],
                      "units_sold": [10, 10, 10, 10, 10, 10, 10, 4, 0, 0],
                      "is_sold_out": [0, 0, 0, 0, 0, 0, 0, 1, 1, 1], "has_pos_data": 1})
    adj = demand.adjusted_demand(d)
    assert list(adj[:7]) == [10] * 7
    assert list(adj[7:]) == [10, 10, 10]           # sold out from day 8: demand was not 4, 0, 0
    d.loc[9, "units_sold"] = 99                    # a future value must not change earlier days
    assert list(demand.adjusted_demand(d)[:9]) == list(adj[:9])


def test_scorecard_matches_the_design_lights(small):
    card = {d["data"]: d for d in fitness.scorecard()["datasets"]}
    assert card["Legacy stock copy"]["light"] == "Red"
    assert card["Customer data"]["light"] == "Red"
    sales = card["Sales history (24 months)"]
    assert sales["questions"]["Representative"]["light"] == "Red"           # this test world has about 5 months
    assert "6 calendar months covered" in sales["questions"]["Representative"]["evidence"]
    assert sales["questions"]["Consistent"]["light"] == "Amber"               # IST times, duplicates, codes fixed
    assert card["Sales history (24 months)"]["questions"]["Allowed"]["light"] == "Green"
    tiers = {t["store_tier"]: t for t in fitness.scorecard()["tiers"]}
    assert tiers["tier-3"]["sold_out"] > tiers["metro"]["sold_out"]            # the feedback loop is visible


# ------------------------------------------------------------------ Week 1 outbox as a source
def test_outbox_feed_maps_to_the_envelope_for_v1_0_and_v1_1(tmp_path, monkeypatch):
    _world(tmp_path, monkeypatch)
    feed = [{"event_id": 1, "at": "2026-11-25T05:00:00+00:00", "type": "order.placed",
             "payload": {"order_id": "VO1", "sku": "SKU00003", "qty": 1, "total": 1296, "channel": "web",
                         "pincode": "560001", "request_id": "r1"}},
            {"event_id": 2, "at": "2026-11-25T05:01:00+00:00", "type": "stock.moved",
             "event_uid": "6f1c", "event_type": "stock.moved", "occurred_at": "2026-11-25T05:01:00+00:00",
             "source": "api-v1", "actor": "service:pos",
             "payload": {"sku": "SKU00003", "location_id": "ST012", "kind": "store_sale", "qty": 1, "ref": "T1"}}]

    def handler(request):
        after = int(request.url.params["after_id"])
        items = [e for e in feed if e["event_id"] > after]
        return httpx.Response(200, json={"events": items, "next_after_id": items[-1]["event_id"] if items else after})
    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api")
    r = load.run("api-v1", client)
    assert r["loaded"] == 2
    assert load.run("api-v1", client)["read"] == 0                             # watermark: nothing new
    con = db.connect()
    ids = {x[0]: x[1] for x in con.execute("SELECT event_id, actor FROM raw.events")}
    con.close()
    assert ids == {"api-v1:1": "system", "6f1c": "service:pos"}
