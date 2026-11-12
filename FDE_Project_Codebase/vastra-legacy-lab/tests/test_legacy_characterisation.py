"""Characterisation tests: they PASS today because they pin down the legacy defects.

In S2 participants replace these with tests for the new behaviour (one pricing API, parallel checkout,
idempotent payments). Run: pytest -q
"""
import os, datetime as dt
os.environ["LATENCY_SCALE"] = "0.01"
os.environ["PAYMENT_FAIL_RATE"] = "0"
import pytest
from fastapi.testclient import TestClient
from app import db, pricing_web, app_api, monolith
from app.partners import stubs
from pos import pricing_pos

NOW = dt.datetime(2026, 11, 7, 12, 0, tzinfo=dt.timezone.utc)
SALE_ITEM = {"sku": "SKU00003", "base_price": 2000, "sale_price": 1600,
             "sale_ends_utc": (NOW + dt.timedelta(hours=2)).isoformat(), "member_discount_pct": 10}


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    db.DB_PATH = str(tmp_path_factory.mktemp("db") / "lab.db")
    db.init_and_seed(50)
    monolith._partners = TestClient(stubs.app)
    return TestClient(monolith.app)


def test_three_pricing_copies_disagree_on_the_same_cart():
    prices = {
        "web": pricing_web.price(SALE_ITEM, coupon_pct=10, member=True, now=NOW),
        "app": app_api.price(SALE_ITEM, coupon_pct=10, member=True, now=NOW),
        "pos": pricing_pos.price(SALE_ITEM, coupon_pct=10, member=True, now=NOW),
    }
    assert len(set(prices.values())) == 3, prices        # R4: three answers for one cart


def test_sale_ends_later_in_the_app_than_on_the_web():
    after_end = NOW + dt.timedelta(hours=3)               # sale ended 1 hour ago (UTC)
    assert pricing_web.price(SALE_ITEM, now=after_end) == 2000
    assert app_api.price(SALE_ITEM, now=after_end) == 1600   # app still shows the sale price


def test_pos_override_needs_only_a_shared_code_and_leaves_no_record():
    assert pricing_pos.price(SALE_ITEM, override_code="MGR-OVERRIDE", override_price=999) == 999
    assert not hasattr(pricing_pos, "AUDIT_LOG")         # R15a: nothing records who changed the price


def test_checkout_runs_steps_one_after_another(client):
    r = client.post("/checkout", json={"sku": "SKU00002", "qty": 1})
    assert r.status_code in (200, 409)
    if r.status_code == 200:
        tr = r.json()["trace"]
        steps = sum(v for k, v in tr.items() if k.startswith("w2_") and k != "w2_1_to_w2_5_total_ms")
        assert tr["w2_1_to_w2_5_total_ms"] >= steps        # R2: total is the sum of the steps


def test_payment_retry_charges_twice():
    pc = TestClient(stubs.app)
    before = len(stubs.CHARGES)
    for _ in range(2):                                     # the same order retried after a timeout
        pc.post("/payment/charge", json={"order_ref": "ORDER-1", "amount": 1500})
    assert len(stubs.CHARGES) - before == 2               # no idempotency key: two charges


def test_checkout_reads_stock_from_a_copy_that_can_be_hours_old(client):
    con = db.connect()
    copied = [dt.datetime.fromisoformat(r["copied_at"]) for r in con.execute("SELECT copied_at FROM stock_copy")]
    con.close()
    oldest = dt.datetime.now(dt.timezone.utc) - min(copied)
    assert oldest > dt.timedelta(hours=1)                 # R5: stock is a periodic copy, not live
