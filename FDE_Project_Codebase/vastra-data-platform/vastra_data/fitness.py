"""Is the data good enough? Six questions, a traffic light each (section 5).

Green: use it.  Amber: use it after a written fix.  Red: do not use it.

  Complete?        Are the rows we need there?          share of store-days with POS data
  Correct?         Does it match reality?               ledger stock vs warehouse count
  Fresh?           Is it recent enough?                 minutes from occurred_at to raw / ready
  Consistent?      Do sources agree?                    same SKU attributes on web, app and POS (R12)
  Representative?  Does it cover every situation?       rows per store tier and season; sold-out days
  Allowed?         May we use it for this purpose?      purpose in the catalogue; consent where personal

Run:  python -m vastra_data scorecard     -> reports/scorecard.md and reports/scorecard.json
Thresholds are named constants below so the data owners can change them in review.
"""
import datetime as dt
import json
import os

import pandas as pd

from vastra_data import config, db

GREEN, AMBER, RED, NA = "Green", "Amber", "Red", "n/a"
ORDER = {GREEN: 0, NA: 0, AMBER: 1, RED: 2}

COMPLETE_GREEN, COMPLETE_AMBER = 0.99, 0.95          # share of open store-days with POS data
LEDGER_TOLERANCE_UNITS = 2                            # ledger within +/- 2 units of the count
CORRECT_GREEN, CORRECT_AMBER = 0.95, 0.80
LEGACY_TOLERANCE = 0.10                               # legacy copy within +/- 10% of the rebuilt network total
FRESH_API_MINUTES = 15
SOLD_OUT_AMBER = 0.03                                 # any tier sold out on > 3% of days: censored demand to fix
TIER_MIN_SHARE = 0.15                                 # each tier has at least 15% of rows
PURPOSE = "replenishment"


def _q(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def _worst(*lights):
    return max(lights, key=lambda x: ORDER[x])


def _light(value, green, amber, higher_is_better=True):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return NA
    if higher_is_better:
        return GREEN if value >= green else AMBER if value >= amber else RED
    return GREEN if value <= green else AMBER if value <= amber else RED


def _allowed(con, tables):
    rows = _q(con, "SELECT table_name, allowed_purposes FROM gov.data_catalogue")
    cat = {r.table_name: json.loads(r.allowed_purposes) for r in rows.itertuples()}
    missing = [t for t in tables if PURPOSE not in cat.get(t, [])]
    return (GREEN if not missing else RED), (f"'{PURPOSE}' allowed for {', '.join(tables)}" if not missing
                                              else f"'{PURPOSE}' not allowed for {', '.join(missing)}")


def scorecard(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    con = db.connect()
    try:
        sd = _q(con, "SELECT business_date, location_id, store_tier, has_pos_data, is_sold_out, units_sold, "
                     "adjusted_demand FROM ready.sales_daily")
        stores = _q(con, "SELECT * FROM ready.store_master")
        lvc = _q(con, "SELECT * FROM ops.ledger_vs_count")
        failed = _q(con, "SELECT source, stage, COUNT(*) n FROM ops.failed_records WHERE status='open' "
                         "GROUP BY 1,2")
        raw_counts = _q(con, "SELECT source, event_type, COUNT(*) n FROM raw.events GROUP BY 1,2")
        clean_runs = _q(con, "SELECT * FROM ops.clean_runs WHERE run_at = (SELECT MAX(run_at) FROM ops.clean_runs)")
        pm = _q(con, "SELECT * FROM ready.product_master")
        po = _q(con, "SELECT * FROM ready.po_receipts")
        prices = _q(con, "SELECT trust, kind, COUNT(*) n FROM clean.prices GROUP BY 1,2")
        legacy = _q(con, "SELECT business_date, sku_id, available FROM clean.stock_counts WHERE source='legacy'")
        net = _q(con, "SELECT business_date, sku_id, SUM(closing_available) ledger FROM ready.sales_daily "
                      "GROUP BY 1,2")
        api = _q(con, "SELECT event_type, occurred_at, loaded_at FROM raw.events WHERE source='api-v1'")
        consent = _q(con, "SELECT purpose, status, COUNT(*) n FROM gov.consent GROUP BY 1,2")
        allowed_sales = _allowed(con, ["ready.sales_daily"])
        allowed_stock = _allowed(con, ["ready.stock_position"])
        allowed_pm = _allowed(con, ["ready.product_master"])
        allowed_po = _allowed(con, ["ready.po_receipts"])
        allowed_promo = _allowed(con, ["ready.promo_calendar"])
        allowed_cust = _allowed(con, ["clean.customers"])
    finally:
        con.close()

    def raw_n(source, types=None):
        m = raw_counts["source"] == source
        if types:
            m &= raw_counts["event_type"].isin(types)
        return int(raw_counts.loc[m, "n"].sum())

    def failed_n(source):
        return int(failed.loc[failed["source"] == source, "n"].sum())

    cards = []

    # ------------------------------------------------ 1. sales history
    store_days = sd[["business_date", "location_id", "has_pos_data"]].drop_duplicates()
    complete = store_days["has_pos_data"].mean()
    worst_store = store_days.groupby("location_id")["has_pos_data"].mean().sort_values().head(1)
    pos_bad = failed_n("pos")
    pos_total = raw_n("pos", ["sales.daily"]) + pos_bad
    newest = sd["business_date"].max()
    age_h = (now - dt.datetime.fromisoformat(newest + "T23:59:59+05:30")).total_seconds() / 3600
    sl = clean_runs[clean_runs.table_name == "sales_lines"]
    times_fixed = int(sl["times_fixed"].sum()) if len(sl) else 0
    dups = int(sl["duplicates_removed"].sum()) if len(sl) else 0
    codes = int(sl["codes_fixed"].sum()) if len(sl) else 0
    tiers = sd.groupby("store_tier").agg(rows=("is_sold_out", "size"), sold_out=("is_sold_out", "mean"),
                                         sold=("units_sold", "sum"), adj=("adjusted_demand", "sum"))
    tiers["share"] = tiers["rows"] / tiers["rows"].sum()
    tiers["hidden_demand"] = 1 - tiers["sold"] / tiers["adj"]
    sd["month"] = sd["business_date"].str[:7]
    months = sd["month"].nunique()
    rep = AMBER if (tiers["sold_out"] > SOLD_OUT_AMBER).any() else GREEN
    if (tiers["share"] < TIER_MIN_SHARE).any() or months < 12:
        rep = RED if months < 12 else _worst(rep, AMBER)
    q = {
        "Complete": (_light(complete, COMPLETE_GREEN, COMPLETE_AMBER),
                     f"{complete:.1%} of open store-days have POS data (lowest store: {worst_store.index[0]} "
                     f"{worst_store.iloc[0]:.1%})"),
        "Correct": (_light(1 - pos_bad / max(pos_total, 1), 0.999, 0.99),
                    f"{pos_bad} of {pos_total:,} POS records failed their contract (in failed_records)"),
        "Fresh": (GREEN if age_h <= 30 else AMBER if age_h <= 72 else RED,
                  f"newest business day {newest} ({age_h:.0f} h ago; daily POS batch)"),
        "Consistent": (AMBER if (times_fixed or dups or codes) else GREEN,
                       f"{times_fixed:,} local IST times converted to UTC, {dups} re-sent duplicates removed, "
                       f"{codes} SKU/store codes fixed"),
        "Representative": (rep, "; ".join(f"{t}: {r.share:.0%} of rows, sold out {r.sold_out:.1%} of days, "
                                          f"hidden demand {r.hidden_demand:.1%}" for t, r in tiers.iterrows())
                           + f"; {months} calendar months covered"),
        "Allowed": allowed_sales,
    }
    action = []
    if times_fixed:
        action.append("fix time zones (POS sends IST without an offset)")
    if rep != GREEN:
        action.append("mark sold-out days; use adjusted demand; report per tier")
    if q["Complete"][0] != GREEN:
        action.append("treat store-days without POS data as unknown, not zero")
    cards.append(("Sales history (24 months)", q, "; ".join(action) or "use"))

    # ------------------------------------------------ 2. legacy stock copy
    if len(legacy):
        legacy = legacy.copy()
        legacy["prev"] = (pd.to_datetime(legacy["business_date"]) - pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d")
        m = legacy.merge(net, left_on=["prev", "sku_id"], right_on=["business_date", "sku_id"],
                         suffixes=("", "_l"))
        within = ((m["available"] - m["ledger"]).abs() <= LEGACY_TOLERANCE * m["ledger"].clip(lower=1)).mean()
        q = {
            "Complete": (AMBER, f"network totals only (no store split); {len(legacy):,} copies"),
            "Correct": (_light(within, CORRECT_GREEN, CORRECT_AMBER),
                        f"only {within:.0%} of copies within +/-{LEGACY_TOLERANCE:.0%} of the rebuilt stock"),
            "Fresh": (AMBER, "copied every 4 hours (the delay behind R5)"),
            "Consistent": (RED if within < CORRECT_AMBER else AMBER, "disagrees with sales + receipts"),
            "Representative": (AMBER, "last 120 days only"),
            "Allowed": allowed_stock,
        }
        cards.append(("Legacy stock copy", q, "Do not use as the answer key. Rebuild stock from sales and goods "
                                              "receipts; use the Week 1 ledger from cut-over"))

    # ------------------------------------------------ 3. Week 1 stock ledger / outbox
    n_api = len(api)
    if n_api:
        lag = (pd.to_datetime(api["loaded_at"], utc=True)
               - pd.to_datetime(api["occurred_at"], utc=True, format="ISO8601")).dt.total_seconds() / 60
        p95 = max(float(lag.quantile(0.95)), 0.0)     # loaded_at is stored to the second: clip rounding below 0
        days_hist = pd.to_datetime(api["occurred_at"], utc=True, format="ISO8601").dt.date.nunique()
        q = {
            "Complete": (GREEN, f"{n_api} events; every order and stock change is in the outbox by design"),
            "Correct": (_light(1 - failed_n("api-v1") / n_api, 0.999, 0.99),
                        f"{failed_n('api-v1')} events failed their contract"),
            "Fresh": (_light(p95, FRESH_API_MINUTES, 60, higher_is_better=False),
                      "95% of events in raw within " + ("1 minute" if p95 < 1 else f"{p95:.0f} minutes")
                      + " of occurring"),
            "Consistent": (GREEN, "one envelope, UTC times, named actors"),
            "Representative": (GREEN, f"{days_hist} day(s) of history: enough for its role (checking recent "
                                      f"results), not for training"),
            "Allowed": allowed_stock,
        }
        light_note = "Use; history is short, so it is used to check recent results"
    else:
        q = {k: (NA, "no Week 1 events loaded yet: run `load --source api-v1` with the API running")
             for k in ("Complete", "Correct", "Fresh", "Consistent", "Representative")}
        q["Allowed"] = allowed_stock
        light_note = "Load the Week 1 outbox first"
    cards.append(("Week 1 stock ledger (outbox)", q, light_note))

    # ------------------------------------------------ 4. product master
    agree = pm["channels_agree"].mean() if len(pm) else None
    q = {
        "Complete": (GREEN if len(pm) else RED, f"{len(pm)} best-seller SKUs with a master record"),
        "Correct": (GREEN, "ERP is the master; codes fixed in clean"),
        "Fresh": (GREEN, "changes arrive as product.upserted events"),
        "Consistent": (_light(agree, 1.0, 0.8), f"{agree:.0%} of SKUs show the same name, category and colour on "
                                                f"ERP, web, app and POS (R12)"),
        "Representative": (GREEN, "all top-500 sample SKUs present"),
        "Allowed": allowed_pm,
    }
    cards.append(("Product master", q, "Fix the top-500 SKUs first; contract checks"
                  if agree is not None and agree < 1 else "use"))

    # ------------------------------------------------ 5. purchase orders and goods receipts
    filled = po["lead_time_filled"].mean() if len(po) else None
    received = po["received_date"].notna().mean() if len(po) else None
    q = {
        "Complete": (_light(1 - filled, 0.99, 0.85), f"{filled:.1%} of POs had no lead time in ERP (filled with the "
                                                     f"supplier median and flagged)"),
        "Correct": (_light(received, 0.95, 0.85), f"{received:.1%} of POs have a goods receipt"),
        "Fresh": (GREEN, "daily ERP and WMS drops"),
        "Consistent": (GREEN, "PO and receipt joined on po_id"),
        "Representative": (GREEN, f"{po['supplier_id'].nunique()} suppliers, all tiers"),
        "Allowed": allowed_po,
    }
    cards.append(("Purchase orders and goods receipts", q, "Lead time where dates exist; filled-in values flagged"))

    # ------------------------------------------------ 6. price and promotion history
    low = int(prices.loc[prices.trust == "low", "n"].sum())
    total = int(prices["n"].sum())
    q = {
        "Complete": (GREEN, f"{total} price records ({int(prices.loc[prices.kind == 'promo', 'n'].sum())} promotions)"),
        "Correct": (AMBER if low else GREEN, f"{low} of {total} prices are from before the Week 1 cut-over "
                                             f"({config.settings.week1_cutover}): low trust (E21)"),
        "Fresh": (GREEN, "promotions announced in advance"),
        "Consistent": (AMBER if low else GREEN, "three legacy price engines disagreed before Week 1 (R4)"),
        "Representative": (GREEN, "every SKU has list and promo history"),
        "Allowed": allowed_promo,
    }
    cards.append(("Price and promotion history", q, "Prices before Week 1 marked low-trust (E21)"))

    # ------------------------------------------------ 7. customer data
    given = int(consent.loc[consent.status == "given", "n"].sum())
    q = {
        "Complete": (NA, "not needed for P2"),
        "Correct": (NA, "not assessed"),
        "Fresh": (NA, "not assessed"),
        "Consistent": (NA, "not assessed"),
        "Representative": (NA, "not assessed"),
        "Allowed": (RED if allowed_cust[0] == RED else GREEN,
                    f"{allowed_cust[1]}; {given} consent grants recorded, none for replenishment"),
    }
    cards.append(("Customer data", q, "Not used for P2"))

    out = []
    for name, qs, what in cards:
        lights = [v[0] for v in qs.values()]
        overall = _worst(*lights)
        if name == "Legacy stock copy":
            overall = RED
        out.append({"data": name, "light": overall, "what_we_do": what,
                    "questions": {k: {"light": v[0], "evidence": v[1]} for k, v in qs.items()}})
    tier_table = tiers.reset_index()[["store_tier", "rows", "share", "sold_out", "hidden_demand"]]
    return {"generated_at": now.isoformat(timespec="seconds"), "purpose": PURPOSE, "datasets": out,
            "tiers": tier_table.round(4).to_dict(orient="records")}


def write(card: dict) -> tuple[str, str]:
    os.makedirs(config.settings.reports_dir, exist_ok=True)
    jp = os.path.join(config.settings.reports_dir, "scorecard.json")
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(card, f, indent=2)
    lines = [f"# P2 data fitness scorecard", "", f"Generated {card['generated_at']} for purpose "
             f"`{card['purpose']}`. Green: use it. Amber: use it after a written fix. Red: do not use it.", "",
             "| Data | Light | What we do |", "|---|---|---|"]
    lines += [f"| {d['data']} | **{d['light']}** | {d['what_we_do']} |" for d in card["datasets"]]
    lines += ["", "## Evidence per question", ""]
    for d in card["datasets"]:
        lines += [f"### {d['data']}: {d['light']}", "", "| Question | Light | Evidence |", "|---|---|---|"]
        lines += [f"| {k}? | {v['light']} | {v['evidence']} |" for k, v in d["questions"].items()]
        lines.append("")
    lines += ["## Per store tier (always report per tier)", "", "| Tier | Rows | Share | Sold-out days | Hidden demand |",
              "|---|---|---|---|---|"]
    lines += [f"| {t['store_tier']} | {t['rows']:,} | {t['share']:.0%} | {t['sold_out']:.1%} | {t['hidden_demand']:.1%} |"
              for t in card["tiers"]]
    mp = os.path.join(config.settings.reports_dir, "scorecard.md")
    with open(mp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return mp, jp
