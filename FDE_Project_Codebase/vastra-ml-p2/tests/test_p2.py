"""P2 tests: leakage rules, the fair contest, the gate, reasons, kill switch. No servers needed."""
import os

import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORM = os.path.join(ROOT, "..", "vastra-data-platform")


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p2")
    os.environ.update({"DATA_DIR": str(tmp / "data"), "INBOX_DIR": str(tmp / "inbox"), "REPORTS_DIR": str(tmp / "rep"),
                       "P2_OUT_DIR": str(tmp / "out"), "LEGACY_DB_PATH": str(tmp / "none.db"), "P2_TOP_K": "10",
                       "CONTRACTS_DIR": os.path.join(PLATFORM, "contracts"),
                       "REFERENCE_DIR": os.path.join(PLATFORM, "reference")})
    from vastra_data import clean, config as dc, load, ready, simulate
    dc.reload_settings()
    simulate.simulate(end_date="2026-11-24", days=300, n_stores=6, n_skus=8, seed=11)
    for s in ("erp", "wms", "pos"):
        load.run(s)
    clean.build()
    ready.build()
    from vastra_p2 import config, features
    config.reload_settings()
    inputs = features.load_inputs()
    return {"inputs": inputs, "df": features.build(inputs)}


def test_features_use_only_data_known_at_0559(world):
    """Rule 1 and 3: features for day d are identical whether or not the data after d-1 exists."""
    from vastra_p2 import features
    day = "2026-10-15"
    full = world["df"][world["df"].business_date == day].set_index(["sku_id", "location_id"])
    trunc = features.prediction_rows(world["inputs"], day).set_index(["sku_id", "location_id"])
    pd.testing.assert_frame_equal(full[features.FEATURES].sort_index(), trunc[features.FEATURES].sort_index(),
                                  check_dtype=False)


def test_no_after_the_fact_features():
    from vastra_p2.features import FEATURES
    assert not [f for f in FEATURES if "emergency" in f or "sold_out" in f or f in ("sku_id", "location_id")]


def test_label_is_stock_hitting_zero_in_next_7_days(world):
    df, sales = world["df"], world["inputs"]["sales"]
    r = df[df.stockout_next_7d.notna()].iloc[100]
    window = sales[(sales.sku_id == r.sku_id) & (sales.location_id == r.location_id)
                   & (sales.business_date >= r.business_date)].sort_values("business_date").head(7)
    assert r.stockout_next_7d == float(window.closing_available.min() == 0)


def test_time_split_is_by_time_with_a_label_gap(world):
    from vastra_p2 import features, model as M
    train, test, cutoff = M.time_split(features.contest_rows(world["df"]))
    assert train.business_date.max() < cutoff <= test.business_date.min()
    assert (pd.Timestamp(cutoff) - pd.Timestamp(train.business_date.max())).days >= 7


def test_rule_formula_matches_the_design():
    from vastra_p2 import model as M
    d = pd.DataFrame({"days_of_cover": [3.0, 20.0], "lead_time_days": [7.0, 7.0]})
    assert list(M.rule(d).rule_flag) == [1, 0]                    # 3 < 7 + 2 ; 20 >= 9


def test_top_k_metrics_on_a_toy_day():
    from vastra_p2 import model as M
    d = pd.DataFrame({"business_date": "2026-11-01", "store_tier": ["metro", "tier-3", "tier-3", "metro"],
                      "new_store": 0, "stockout_next_7d": [1, 1, 0, 0],
                      "rule_score": [4, 3, 2, 1], "rule_flag": 1, "model_score": [0.1, 0.9, 0.8, 0.2]})
    ev = {r["group"]: r for r in M.evaluate(d, k=2)["results"]}
    assert ev["Overall"]["rule_recall"] == 1.0 and ev["Overall"]["model_recall"] == 0.5
    assert ev["Overall"]["model_precision"] == 0.5
    assert ev["Metro"]["model_recall"] == 0.0 and ev["Tier-3"]["model_recall"] == 1.0


def test_gate_needs_10_points_no_worse_tier_and_a_signature():
    from vastra_p2 import model as M
    res = lambda o, m: {"results": [{"group": "Overall", "rule_recall": o[0], "model_recall": o[1]},  # noqa: E731
                                    {"group": "Metro", "rule_recall": m[0], "model_recall": m[1]}]}
    assert M.gate(res((0.30, 0.42), (0.3, 0.4)), signed=True)["winner"] == "model"
    assert M.gate(res((0.30, 0.38), (0.3, 0.4)), signed=True)["winner"] == "rule"     # only +8 points
    assert M.gate(res((0.30, 0.42), (0.3, 0.2)), signed=True)["winner"] == "rule"     # a tier got worse
    assert M.gate(res((0.30, 0.42), (0.3, 0.4)), signed=False)["winner"] == "rule"    # card not signed


def test_contest_reasons_and_kill_switch(world, monkeypatch):
    from vastra_p2 import __main__ as cli, card, config, daily, model as M
    card.sign("Test Planner")
    assert cli.main(["contest"]) == 0
    metrics = M.load_metrics()
    assert {r["group"] for r in metrics["results"]} >= {"Metro", "Tier-2", "Tier-3", "New stores (< 12 months)"}
    assert os.path.exists(os.path.join(config.settings.model_dir, "model_card.md"))
    m = daily.make("2026-11-25", world["inputs"])
    lst = pd.read_csv(m["files"]["model"])
    assert len(lst) == 10 and lst[["reason_1", "reason_2", "reason_3"]].notna().all().all()
    assert m["in_force"] == metrics["gate"]["winner"]
    monkeypatch.setenv("P2_MODE", "rule")
    config.reload_settings()
    assert daily.make("2026-11-25", world["inputs"])["in_force"] == "rule"            # kill switch
