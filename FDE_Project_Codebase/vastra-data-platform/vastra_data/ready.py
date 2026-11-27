"""clean -> ready: the tables P2 uses, each with a contract, an owner and a freshness target.

  ready.store_master, ready.product_master, ready.po_receipts, ready.promo_calendar
  ready.stock_position   rebuilt from sales and goods receipts (the legacy stock copy is Red: not the answer key),
                         anchored to WMS cycle counts; end-of-day snapshots plus one current snapshot
  ready.sales_daily      full SKU x store x day grid with is_sold_out and adjusted demand (section 5.2)

The whole build is one transaction. If a blocking contract check fails, it rolls back and the previous ready
tables stay in place; the failure is alerted and stored in ops.contract_results.
"""
import datetime as dt

import numpy as np
import pandas as pd

from vastra_data import contracts, db, demand, governance
from vastra_data.alerts import alert


class ContractViolation(RuntimeError):
    pass


def _now():
    return dt.datetime.now(dt.timezone.utc)


def day_end_utc(dates: pd.Series) -> pd.Series:
    """IST day end (23:59:59 +05:30) written in UTC: 2026-11-24 -> 2026-11-24T18:29:59+00:00."""
    return (pd.to_datetime(dates) + pd.Timedelta(hours=18, minutes=29, seconds=59)).dt.strftime(
        "%Y-%m-%dT%H:%M:%S+00:00")


def _q(con, sql, params=()):
    return pd.read_sql_query(sql, con, params=params)


def rebuild_ledger(grid: pd.DataFrame) -> tuple[np.ndarray, list]:
    """Closing stock per row (grid sorted by sku, location, date). A WMS count at 06:00 resets the balance.
    Returns closing balances and the (row, ledger_before_count, counted) list for the Correct? check."""
    key = (grid["sku_id"] + "|" + grid["location_id"]).to_numpy()
    net = grid["net"].to_numpy()
    cnt = grid["count"].to_numpy()
    out = np.zeros(len(grid), dtype=np.int64)
    checks = []
    bal, prev = 0, None
    for i in range(len(grid)):
        if key[i] != prev:
            bal, prev, first = 0, key[i], True
        else:
            first = False
        if not np.isnan(cnt[i]):
            if not first:
                checks.append((i, bal, int(cnt[i])))
            bal = int(cnt[i])
        bal = max(bal + int(net[i]), 0)
        out[i] = bal
    return out, checks


def build(as_of: dt.datetime | None = None) -> dict:
    db.init()
    governance.sync_catalogue()
    now = as_of or _now()
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        for t in ("store_master", "product_master", "sales_daily", "stock_position", "po_receipts", "promo_calendar"):
            con.execute(f"DELETE FROM ready.{t}")
        con.execute("DELETE FROM ops.ledger_vs_count")

        # ---- masters
        stores = _q(con, "SELECT location_id, tier, region, city, opened_on FROM clean.stores "
                         "WHERE location_id LIKE 'ST%'")
        _insert(con, "store_master", stores)
        prods = _q(con, "SELECT * FROM clean.products")
        master = prods[prods.channel == "erp"].copy()

        def norm(df):
            return (df["name"].str.strip().str.lower() + "|" + df["category"].str.lower() + "|"
                    + df["colour"].fillna("").str.strip().str.lower())
        prods["sig"] = norm(prods)
        agree = prods.groupby("sku_id")["sig"].nunique().eq(1).astype(int)
        master["channels_agree"] = master["sku_id"].map(agree).fillna(0).astype(int)
        pm = master[["sku_id", "name", "category", "colour", "top500", "channels_agree"]]
        _insert(con, "product_master", pm)

        # ---- POs and receipts
        po = _q(con, "SELECT p.*, g.business_date AS received_date FROM clean.purchase_orders p "
                     "LEFT JOIN (SELECT po_id, MIN(business_date) business_date FROM clean.goods_receipts "
                     "GROUP BY po_id) g USING (po_id)")
        actual = (pd.to_datetime(po["received_date"]) - pd.to_datetime(po["raised_date"])).dt.days
        po_out = pd.DataFrame({"po_id": po["po_id"], "sku_id": po["sku_id"], "location_id": po["location_id"],
                               "supplier_id": po["supplier_id"], "qty": po["qty"], "raised_date": po["raised_date"],
                               "expected_date": po["expected_date"], "received_date": po["received_date"],
                               "lead_time_days": actual.fillna(po["lead_time_days"]).astype("Int64"),
                               "lead_time_filled": po["lead_time_filled"]})
        _insert(con, "po_receipts", po_out)

        promo = _q(con, "SELECT sku_id, location_id, valid_from AS start_date, valid_to AS end_date, price, trust "
                        "FROM clean.prices WHERE kind='promo' AND valid_from IS NOT NULL AND valid_to IS NOT NULL")
        promo = promo.drop_duplicates(["sku_id", "location_id", "start_date"], keep="last")
        _insert(con, "promo_calendar", promo)

        # ---- the daily grid
        sales = _q(con, "SELECT business_date, sku_id, location_id, units, net_amount FROM clean.sales_lines")
        if sales.empty:
            raise ContractViolation("no sales in clean.sales_lines: run load and clean first")
        top = set(pm.loc[pm.top500 == 1, "sku_id"])
        start, end = sales["business_date"].min(), sales["business_date"].max()
        days = pd.date_range(start, end).strftime("%Y-%m-%d")
        frames = []
        for _, s in stores.iterrows():
            d = days[days >= s["opened_on"]]
            frames.append(pd.MultiIndex.from_product([sorted(top), [s["location_id"]], d],
                                                     names=["sku_id", "location_id", "business_date"]).to_frame(
                index=False))
        grid = pd.concat(frames, ignore_index=True)
        grid = grid.merge(stores[["location_id", "tier"]], on="location_id").rename(columns={"tier": "store_tier"})
        grid = grid.merge(sales, on=["business_date", "sku_id", "location_id"], how="left")
        store_days = sales[["business_date", "location_id"]].drop_duplicates().assign(has_pos_data=1)
        grid = grid.merge(store_days, on=["business_date", "location_id"], how="left")
        grid["has_pos_data"] = grid["has_pos_data"].fillna(0).astype(int)
        grid["units_sold"] = grid.pop("units").fillna(0).astype(int)
        grid["net_amount"] = grid["net_amount"].fillna(0).astype(int)

        rec = _q(con, "SELECT business_date, sku_id, location_id, SUM(qty) receipts FROM clean.goods_receipts "
                      "GROUP BY 1,2,3")
        mov = _q(con, "SELECT business_date, sku_id, location_id, SUM(qty_signed) moves FROM clean.stock_movements "
                      "WHERE kind IN ('return','transfer_in','transfer_out') GROUP BY 1,2,3")
        # first WMS count of the day (SQLite returns the row that holds the MIN)
        cnt = _q(con, "SELECT business_date, sku_id, location_id, available AS count, MIN(counted_at_utc) AS _t "
                      "FROM clean.stock_counts WHERE source='wms' GROUP BY 1,2,3").drop(columns="_t")
        for extra in (rec, mov, cnt):
            grid = grid.merge(extra, on=["business_date", "sku_id", "location_id"], how="left")
        grid["net"] = grid["receipts"].fillna(0) + grid["moves"].fillna(0) - grid["units_sold"]
        grid = grid.sort_values(["sku_id", "location_id", "business_date"]).reset_index(drop=True)
        closing, checks = rebuild_ledger(grid)
        grid["closing_available"] = closing
        if checks:
            ck = grid.loc[[c[0] for c in checks], ["business_date", "sku_id", "location_id"]].copy()
            ck["ledger"] = [c[1] for c in checks]
            ck["counted"] = [c[2] for c in checks]
            _insert(con, "ledger_vs_count", ck, schema="ops")

        # on order at end of day: + PO qty on the day it is raised, - when it is received
        ev = pd.concat([po[["sku_id", "location_id", "raised_date", "qty"]].rename(columns={"raised_date": "d"}),
                        po.loc[po.received_date.notna(), ["sku_id", "location_id", "received_date", "qty"]]
                       .rename(columns={"received_date": "d"}).assign(qty=lambda x: -x["qty"])])
        ev = ev.groupby(["sku_id", "location_id", "d"], as_index=False)["qty"].sum().rename(
            columns={"d": "business_date", "qty": "oo_delta"})
        grid = grid.merge(ev, on=["sku_id", "location_id", "business_date"], how="left")
        grid["on_order"] = grid.groupby(["sku_id", "location_id"])["oo_delta"].transform(
            lambda s: s.fillna(0).cumsum()).clip(lower=0).astype(int)

        grid["is_sold_out"] = (grid["closing_available"] <= 0).astype(int)
        grid["adjusted_demand"] = demand.adjusted_demand(grid)
        sd = grid[["business_date", "sku_id", "location_id", "store_tier", "units_sold", "net_amount",
                   "has_pos_data", "closing_available", "is_sold_out", "adjusted_demand"]]
        _insert(con, "sales_daily", sd)

        sp = pd.DataFrame({"snapshot_at": day_end_utc(grid["business_date"]), "sku_id": grid["sku_id"],
                           "location_id": grid["location_id"], "available": grid["closing_available"],
                           "on_order": grid["on_order"]})
        current = grid.sort_values("business_date").groupby(["sku_id", "location_id"]).tail(1)
        sp_now = pd.DataFrame({"snapshot_at": now.strftime("%Y-%m-%dT%H:%M:%S+00:00"), "sku_id": current["sku_id"],
                               "location_id": current["location_id"], "available": current["closing_available"],
                               "on_order": current["on_order"]})
        _insert(con, "stock_position", pd.concat([sp, sp_now]))

        # ---- contract checks on the new tables, inside the same transaction
        results = []
        for c in contracts.all_ready():
            results += contracts.check(con, c, now=now)
        blocking = contracts.blocking_failures(results)
        if blocking:
            con.execute("ROLLBACK")
            with db.tx() as c2:
                _save_results(c2, results)
            alert("ready build rolled back: contract checks failed",
                  failures=[f"{r['table']} {r['check']} ({r['failing_rows']} rows)" for r in blocking])
            raise ContractViolation("; ".join(f"{r['table']} {r['check']}: {r['failing_rows']} rows"
                                              for r in blocking))
        _save_results(con, results)
        con.execute("COMMIT")
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    late = [r for r in results if r["check"].startswith("fresh") and not r["passed"]]
    for r in late:
        alert("ready table is not fresh", table=r["table"], detail=r["detail"])
    return {"rows": {"sales_daily": len(sd), "stock_position": len(sp) + len(sp_now), "product_master": len(pm),
                     "store_master": len(stores), "po_receipts": len(po_out), "promo_calendar": len(promo)},
            "period": [start, end], "checks_passed": sum(r["passed"] for r in results), "checks": len(results),
            "not_fresh": [f"{r['table']}: {r['detail']}" for r in late]}


def _save_results(con, results):
    at = _now().isoformat(timespec="seconds")
    con.executemany("INSERT INTO ops.contract_results (run_at, table_name, check_name, passed, detail) "
                    "VALUES (?,?,?,?,?)",
                    [(at, r["table"], r["check"], int(r["passed"]),
                      f"{r['detail']}; failing rows {r['failing_rows']}") for r in results])


def _insert(con, table, df, schema="ready"):
    if df is None or not len(df):
        return
    cols = list(df.columns)
    data = df.astype(object).where(df.notna(), None).itertuples(index=False, name=None)
    con.executemany(f"INSERT INTO {schema}.{table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    [tuple(v.item() if hasattr(v, "item") else v for v in r) for r in data])
