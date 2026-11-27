"""raw -> clean. raw is never edited, so clean is always rebuilt from it in full.

What clean fixes:
  * times: everything to UTC. Sources that send local time without an offset (pos, legacy) are declared IST in
    events.NAIVE_TIMEZONE; the business date is the IST calendar date (the R4 time-zone defect must not return)
  * duplicates: the same business fact sent twice with different event_ids (e.g. a POS re-send)
  * codes: 'sku00003 ' -> 'SKU00003', 'st-12' -> 'ST012', legacy aliases from reference/sku_aliases.csv
  * personal details: already tokens (the load job swapped them before raw); clean.customers holds tokens only
  * contract checks: a SKU or store that does not exist after fixing goes to failed_records (stage=clean)
"""
import datetime as dt
import json
import os
import re

import pandas as pd

from vastra_data import config, db, events, governance
from vastra_data.alerts import alert

SPECIAL_LOCATIONS = {"ONLINE", "NETWORK", "DC01"}
_SKU = re.compile(r"^([A-Z]{3})0*(\d{1,5})$")
_LOC = re.compile(r"^ST0*(\d{1,3})$")


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _aliases() -> dict:
    p = os.path.join(config.settings.reference_dir, "sku_aliases.csv")
    return dict(pd.read_csv(p).values) if os.path.exists(p) else {}


def fix_sku(v: pd.Series, aliases: dict) -> pd.Series:
    s = v.astype(str).str.strip().str.upper()
    s = s.map(lambda x: aliases.get(x, x))
    m = s.str.extract(_SKU)
    return (m[0] + m[1].str.zfill(5)).where(m[0].notna(), s)


def fix_location(v: pd.Series) -> pd.Series:
    s = v.astype(str).str.strip().str.upper().str.replace(r"[\s\-_]", "", regex=True)
    m = s.str.extract(_LOC)
    return ("ST" + m[0].str.zfill(3)).where(m[0].notna(), s)


def to_utc(df: pd.DataFrame) -> tuple[pd.Series, int]:
    """occurred_at as received -> UTC timestamps. Returns (series, number of naive local times converted)."""
    t = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns, UTC]")
    naive = ~df["occurred_at"].str.contains(r"(?:Z|[+-]\d{2}:?\d{2})$", regex=True)
    if (~naive).any():
        t[~naive] = pd.to_datetime(df.loc[~naive, "occurred_at"], utc=True, format="ISO8601")
    for source, tz in events.NAIVE_TIMEZONE.items():
        m = naive & (df["source"] == source)
        if m.any():
            t[m] = (pd.to_datetime(df.loc[m, "occurred_at"], format="ISO8601")
                    .dt.tz_localize(tz).dt.tz_convert("UTC"))
    return t, int(naive.sum())


def ist_date(t: pd.Series) -> pd.Series:
    return t.dt.tz_convert("Asia/Kolkata").dt.strftime("%Y-%m-%d")


def iso(t: pd.Series) -> pd.Series:
    return t.dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _load_raw(con, types) -> pd.DataFrame:
    q = ("SELECT event_id, event_type, occurred_at, source, actor, payload FROM raw.events WHERE event_type IN ("
         + ",".join("?" * len(types)) + ")")
    df = pd.read_sql_query(q, con, params=list(types))
    if df.empty:
        return df
    p = pd.json_normalize(df["payload"].map(json.loads)).set_index(df.index)
    return pd.concat([df.drop(columns="payload"), p], axis=1)


def build() -> dict:
    db.init()
    aliases = _aliases()
    con = db.connect()
    stats, rejected = {}, []
    try:
        con.execute("BEGIN IMMEDIATE")
        # clean-stage rejects are recomputed on every rebuild
        con.execute("DELETE FROM ops.failed_records WHERE stage='clean' AND status='open'")
        for t in ("stores", "products", "sales_lines", "orders", "stock_movements", "stock_counts",
                  "purchase_orders", "goods_receipts", "prices", "customers"):
            con.execute(f"DELETE FROM clean.{t}")

        def record(table, rows_in, out_df, dups=0, times=0, codes=0, rej=0):
            stats[table] = {"rows_in": rows_in, "rows_out": len(out_df), "duplicates_removed": dups,
                            "times_fixed": times, "codes_fixed": codes, "rejected": rej}

        def write(table, df):
            if len(df):
                rows = df.astype(object).where(df.notna(), None).itertuples(index=False, name=None)
                cols = ",".join(df.columns)
                con.executemany(f"INSERT INTO clean.{table} ({cols}) VALUES ({','.join('?' * len(df.columns))})",
                                [tuple(v.item() if hasattr(v, "item") else v for v in r)
                                 for r in rows])

        def reject(df, mask, reason):
            for _, r in df[mask].iterrows():
                rejected.append((r["event_id"], r["event_type"], r["source"], reason))

        # ---- stores and products (reference data first: other tables are checked against them)
        st = _load_raw(con, ["store.upserted"])
        if len(st):
            st["t"], _ = to_utc(st)
            st = st.sort_values("t").drop_duplicates("location_id", keep="last")
            st["location_id"] = fix_location(st["location_id"])
            out = pd.DataFrame({"location_id": st["location_id"], "name": st["store_name"], "city": st["city"],
                                "region": st["region"], "tier": st["tier"], "opened_on": st["opened_on"],
                                "updated_at_utc": iso(st["t"])})
            write("stores", out); record("stores", len(st), out)
        known_locations = set(pd.read_sql_query("SELECT location_id FROM clean.stores", con)["location_id"]) \
            | SPECIAL_LOCATIONS

        pr = _load_raw(con, ["product.upserted"])
        if len(pr):
            pr["t"], times = to_utc(pr)
            raw_sku = pr["sku"].copy()
            pr["sku"] = fix_sku(pr["sku"], aliases)
            n = len(pr)
            pr = pr.sort_values("t").drop_duplicates(["sku", "channel"], keep="last")
            out = pd.DataFrame({"sku_id": pr["sku"], "channel": pr["channel"], "name": pr["product_name"],
                                "category": pr["category"], "colour": pr.get("colour"),
                                "top500": pr["top500"].astype(int), "updated_at_utc": iso(pr["t"])})
            write("products", out)
            record("products", n, out, n - len(pr), times, int((raw_sku.loc[pr.index] != pr["sku"]).sum()))
        known_skus = set(pd.read_sql_query("SELECT sku_id FROM clean.products WHERE channel='erp'", con)["sku_id"])

        def common(df, has_location=True):
            """UTC time, IST business date, fixed codes, and the existence checks every movement table needs."""
            df["t"], times = to_utc(df)
            df["business_date_ist"] = ist_date(df["t"])
            before_sku = df["sku"].astype(str)
            df["sku"] = fix_sku(df["sku"], aliases)
            codes = (before_sku != df["sku"])
            bad = ~df["sku"].isin(known_skus)
            reject(df, bad, "unknown sku after code fix (not in the ERP product master)")
            if has_location:
                before_loc = df["location_id"].astype(str)
                df["location_id"] = fix_location(df["location_id"])
                codes |= (before_loc != df["location_id"])
                bad_loc = ~df["location_id"].isin(known_locations) & ~bad
                reject(df, bad_loc, "unknown location after code fix (not in the ERP store master)")
                bad |= bad_loc
            return df[~bad].copy(), times, int(codes.sum()), int(bad.sum())

        # ---- POS daily sales
        sl = _load_raw(con, ["sales.daily"])
        if len(sl):
            n = len(sl)
            sl, times, codes, rej = common(sl)
            sl = sl.sort_values("t")
            before = len(sl)
            sl = sl.drop_duplicates(["business_date", "sku", "location_id"], keep="last")
            out = pd.DataFrame({"event_id": sl["event_id"], "business_date": sl["business_date"], "sku_id": sl["sku"],
                                "location_id": sl["location_id"], "units": sl["units"].astype(int),
                                "net_amount": sl["net_amount"].astype(int), "occurred_at_utc": iso(sl["t"]),
                                "source": sl["source"]})
            write("sales_lines", out); record("sales_lines", n, out, before - len(sl), times, codes, rej)

        # ---- online orders (Week 1 API)
        od = _load_raw(con, ["order.placed", "order.payment_pending"])
        if len(od):
            n = len(od)
            od, times, codes, rej = common(od, has_location=False)
            od["status"] = od["event_type"].map({"order.placed": "paid", "order.payment_pending": "payment_pending"})
            od = od.sort_values(["t", "status"]).drop_duplicates("order_id", keep="last")
            out = pd.DataFrame({"order_id": od["order_id"], "occurred_at_utc": iso(od["t"]),
                                "business_date": od["business_date_ist"], "sku_id": od["sku"], "qty": od["qty"],
                                "total": od["total"], "channel": od["channel"], "status": od["status"]})
            write("orders", out); record("orders", n, out, n - rej - len(od), times, codes, rej)

        # ---- stock movements, counts
        sm = _load_raw(con, ["stock.moved"])
        if len(sm):
            n = len(sm)
            sm, times, codes, rej = common(sm)
            sign = sm["kind"].map({"store_sale": -1, "online_sale": -1, "transfer_out": -1, "return": 1,
                                   "receipt": 1, "transfer_in": 1})
            before = len(sm)
            sm = sm.sort_values("t").drop_duplicates(["source", "ref", "sku", "location_id", "kind"], keep="first")
            out = pd.DataFrame({"event_id": sm["event_id"], "occurred_at_utc": iso(sm["t"]),
                                "business_date": sm["business_date_ist"], "sku_id": sm["sku"],
                                "location_id": sm["location_id"], "kind": sm["kind"],
                                "qty_signed": (sign.loc[sm.index] * sm["qty"]).astype(int), "ref": sm["ref"],
                                "source": sm["source"]})
            write("stock_movements", out); record("stock_movements", n, out, before - len(sm), times, codes, rej)

        sc = _load_raw(con, ["stock.counted"])
        if len(sc):
            n = len(sc)
            sc, times, codes, rej = common(sc)
            if "on_order" not in sc:
                sc["on_order"] = 0
            before = len(sc)
            sc = sc.sort_values("t").drop_duplicates(["sku", "location_id", "t", "source"], keep="last")
            out = pd.DataFrame({"event_id": sc["event_id"], "counted_at_utc": iso(sc["t"]),
                                "business_date": sc["business_date_ist"], "sku_id": sc["sku"],
                                "location_id": sc["location_id"], "available": sc["available"].astype(int),
                                "on_order": sc["on_order"].fillna(0).astype(int), "source": sc["source"]})
            write("stock_counts", out); record("stock_counts", n, out, before - len(sc), times, codes, rej)

        # ---- purchase orders and goods receipts (lead time where dates exist; filled-in values flagged)
        po = _load_raw(con, ["po.raised"])
        gr = _load_raw(con, ["goods.received"])
        if len(po):
            n = len(po)
            po, times, codes, rej = common(po)
            po = po.sort_values("t").drop_duplicates("po_id", keep="last")
            for c in ("lead_time_days", "expected_date"):
                if c not in po:
                    po[c] = None
            filled = po["lead_time_days"].isna()
            sup_median = po.groupby("supplier_id")["lead_time_days"].transform("median")
            po["lead_time_days"] = po["lead_time_days"].fillna(sup_median).fillna(7).round().astype(int)
            raised = pd.to_datetime(po["business_date_ist"])
            po["expected_date"] = po["expected_date"].where(
                po["expected_date"].notna(),
                (raised + pd.to_timedelta(po["lead_time_days"], unit="D")).dt.strftime("%Y-%m-%d"))
            out = pd.DataFrame({"po_id": po["po_id"], "sku_id": po["sku"], "location_id": po["location_id"],
                                "qty": po["qty"].astype(int), "raised_at_utc": iso(po["t"]),
                                "raised_date": po["business_date_ist"], "expected_date": po["expected_date"],
                                "supplier_id": po["supplier_id"], "lead_time_days": po["lead_time_days"],
                                "lead_time_filled": filled.astype(int)})
            write("purchase_orders", out); record("purchase_orders", n, out, 0, times, codes, rej)
        if len(gr):
            n = len(gr)
            gr, times, codes, rej = common(gr)
            before = len(gr)
            gr = gr.sort_values("t").drop_duplicates(["po_id"], keep="first")
            out = pd.DataFrame({"event_id": gr["event_id"], "po_id": gr["po_id"], "sku_id": gr["sku"],
                                "location_id": gr["location_id"], "qty": gr["qty"].astype(int),
                                "received_at_utc": iso(gr["t"]), "business_date": gr["business_date_ist"]})
            write("goods_receipts", out); record("goods_receipts", n, out, before - len(gr), times, codes, rej)

        # ---- prices (before the Week 1 cut-over: low trust, finding E21)
        pc = _load_raw(con, ["price.changed", "price.override.created"])
        if len(pc):
            ov = pc["event_type"] == "price.override.created"
            if ov.any():                                   # Week 1 override: a store price, kind 'override'
                pc.loc[ov, "location_id"] = pc.loc[ov, "store"]
                pc.loc[ov, "kind"] = "override"
            n = len(pc)
            pc["location_id"] = pc.get("location_id", pd.Series(None, index=pc.index)).fillna("ALL")
            pc, times, codes, rej = common(pc, has_location=False)
            pc["location_id"] = pc["location_id"].where(pc["location_id"] == "ALL", fix_location(pc["location_id"]))
            for c in ("valid_from", "valid_to"):
                if c not in pc:
                    pc[c] = None
            trust = (pc["business_date_ist"] >= config.settings.week1_cutover).map({True: "high", False: "low"})
            out = pd.DataFrame({"event_id": pc["event_id"], "sku_id": pc["sku"], "location_id": pc["location_id"],
                                "price": pc["price"].astype(int), "kind": pc["kind"], "valid_from": pc["valid_from"],
                                "valid_to": pc["valid_to"], "occurred_at_utc": iso(pc["t"]), "trust": trust})
            write("prices", out); record("prices", n, out, 0, times, codes, rej)

        # ---- customers (tokens only) and consent
        cu = _load_raw(con, ["customer.registered"])
        if len(cu):
            cu["t"], times = to_utc(cu)
            cu = cu.sort_values("t").drop_duplicates("customer_token", keep="first")
            out = pd.DataFrame({"customer_token": cu["customer_token"], "home_store": fix_location(cu["home_store"]),
                                "registered_at_utc": iso(cu["t"])})
            write("customers", out); record("customers", len(cu), out, 0, times)
        cs = _load_raw(con, ["consent.changed"])
        if len(cs):
            cs["t"], _ = to_utc(cs)
            for _, r in cs.iterrows():
                governance.record_consent(con, r["customer_token"], r["purpose"], r["status"], r["notice_version"],
                                          r["t"].isoformat(), r["source"], r["event_id"])

        for eid, etype, src, reason in rejected:
            con.execute("INSERT INTO ops.failed_records (stage, source, event_id, event_type, row, reason, kind, "
                        "failed_at) SELECT 'clean', ?, ?, ?, payload, ?, 'contract', ? FROM raw.events WHERE event_id=?",
                        (src, eid, etype, reason, _now(), eid))
        for t, s in stats.items():
            con.execute("INSERT INTO ops.clean_runs VALUES (?,?,?,?,?,?,?,?)",
                        (_now(), t, s["rows_in"], s["rows_out"], s["duplicates_removed"], s["times_fixed"],
                         s["codes_fixed"], s["rejected"]))
        con.execute("COMMIT")
    except Exception:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    finally:
        con.close()
    if rejected:
        alert("clean: records rejected by contract checks", count=len(rejected),
              reasons=sorted({r[3] for r in rejected}))
    return stats
