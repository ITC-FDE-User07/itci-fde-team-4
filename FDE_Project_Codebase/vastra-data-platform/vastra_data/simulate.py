"""Lab data: 24 months of Vastra history as POS, ERP, WMS and legacy file drops (inbox/<source>/*.jsonl).

The shop is simulated day by day so the data has the real problems Week 2 is about:
  * censored demand: when a SKU sells out, POS shows 0 sales although customers wanted it (section 5.2)
  * a legacy reorder rule that trusts those zeros, so small-town (tier-3) stores sell out more often
  * festivals (Diwali, Eid, Pongal, Onam, ...) by region, promotions, supplier delays before festivals
  * defects to catch: local IST times without an offset (POS), duplicates, messy SKU/store codes, missing
    lead times, a few negative or malformed records, inconsistent product attributes across channels (R12),
    and a legacy stock copy that is badly wrong (E-series findings)
The true demand is written to reports/sim_truth.csv.gz, ONLY for teaching (compare adjusted vs true demand).
It is never loaded into the platform.

Run:  python -m vastra_data simulate            (defaults: 16 stores x 25 best sellers, ending 2026-11-24)
"""
import datetime as dt
import json
import math
import os

import numpy as np
import pandas as pd

from vastra_data import config

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

STORES = [  # location_id, name, city, region, tier, opened_on
    ("ST001", "Vastra Bandra", "Mumbai", "West", "metro", "2015-04-01"),
    ("ST002", "Vastra Connaught Place", "Delhi", "North", "metro", "2014-08-15"),
    ("ST003", "Vastra Indiranagar", "Bengaluru", "South", "metro", "2016-01-10"),
    ("ST004", "Vastra T Nagar", "Chennai", "South", "metro", "2017-06-01"),
    ("ST005", "Vastra Park Street", "Kolkata", "East", "metro", "2018-02-01"),
    ("ST006", "Vastra FC Road", "Pune", "West", "tier-2", "2018-09-01"),
    ("ST007", "Vastra MI Road", "Jaipur", "North", "tier-2", "2019-03-01"),
    ("ST008", "Vastra MG Road Kochi", "Kochi", "South", "tier-2", "2019-07-01"),
    ("ST009", "Vastra Hazratganj", "Lucknow", "North", "tier-2", "2020-01-15"),
    ("ST010", "Vastra Saheed Nagar", "Bhubaneswar", "East", "tier-2", "2021-02-01"),
    ("ST011", "Vastra College Road", "Nashik", "West", "tier-3", "2021-08-01"),
    ("ST012", "Vastra Sayyaji Rao Road", "Mysuru", "South", "tier-3", "2022-01-10"),
    ("ST013", "Vastra Town Hall", "Madurai", "South", "tier-3", "2022-06-01"),
    ("ST014", "Vastra Hiran Magri", "Udaipur", "North", "tier-3", "2023-01-05"),
    ("ST015", "Vastra Round North", "Thrissur", "South", "tier-2", "2026-03-02"),   # new store (< 12 months)
    ("ST016", "Vastra Hill Cart Road", "Siliguri", "East", "tier-3", "2026-05-18"),  # new store (< 12 months)
]
TIER_SCALE = {"metro": 1.5, "tier-2": 1.0, "tier-3": 0.7}

SKUS = [  # sku, name, category, colour, base daily units at a tier-2 store
    ("SKU00001", "Cotton anarkali kurta", "kurtas", "indigo", 3.2),
    ("SKU00002", "Banarasi silk saree", "sarees", "maroon", 1.6),
    ("SKU00003", "Linen kurta", "kurtas", "off-white", 4.0),
    ("SKU00004", "Chikankari kurta", "kurtas", "white", 2.6),
    ("SKU00005", "Printed palazzo set", "ethnic sets", "mustard", 2.2),
    ("SKU00006", "Slim fit chinos", "trousers", "khaki", 1.8),
    ("SKU00007", "Denim jacket", "jeans", "blue", 1.2),
    ("SKU00008", "Straight jeans", "jeans", "dark blue", 2.4),
    ("SKU00009", "Bandhani dupatta", "dupattas", "red", 2.0),
    ("SKU00010", "Kanjeevaram saree", "sarees", "green", 1.0),
    ("SKU00011", "Nehru jacket", "ethnic sets", "beige", 1.1),
    ("SKU00012", "Cotton kurta pyjama set", "ethnic sets", "cream", 2.3),
    ("SKU00013", "Oxford shirt", "shirts", "light blue", 2.1),
    ("SKU00014", "Linen shirt", "shirts", "white", 2.0),
    ("SKU00015", "Georgette saree", "sarees", "pink", 1.4),
    ("SKU00016", "Block print kurti", "kurtas", "teal", 3.0),
    ("SKU00017", "Embroidered lehenga", "ethnic sets", "magenta", 0.6),
    ("SKU00018", "Chanderi dupatta", "dupattas", "gold", 1.3),
    ("SKU00019", "Graphic tee", "tops", "black", 2.8),
    ("SKU00020", "Rayon top", "tops", "coral", 2.2),
    ("SKU00021", "Silk kurta", "kurtas", "ivory", 1.5),
    ("SKU00022", "Mojari footwear", "footwear", "tan", 1.2),
    ("SKU00023", "Pathani suit", "ethnic sets", "grey", 0.9),
    ("SKU00024", "Kalamkari saree", "sarees", "rust", 0.9),
    ("SKU00025", "Cargo joggers", "trousers", "olive", 1.6),
]
ETHNIC = {"kurtas", "sarees", "ethnic sets", "dupattas"}
LIST_PRICE = {"kurtas": 1800, "sarees": 4200, "ethnic sets": 2600, "dupattas": 900, "trousers": 1700,
              "jeans": 2400, "shirts": 1500, "tops": 900, "footwear": 1400}
SUPPLIERS = {"SUP01": 5, "SUP02": 7, "SUP03": 8, "SUP04": 10, "SUP05": 12, "SUP06": 6}
DOW = np.array([0.85, 0.85, 0.9, 0.95, 1.05, 1.3, 1.25])   # Mon..Sun

FIRST = ["Aarav", "Diya", "Ishaan", "Ananya", "Kabir", "Meera", "Rohan", "Saanvi", "Vikram", "Lakshmi", "Arjun",
         "Fatima", "Joseph", "Priya", "Rahul", "Sneha", "Imran", "Kavya", "Nikhil", "Pooja"]
LAST = ["Sharma", "Iyer", "Nair", "Khan", "Das", "Reddy", "Patel", "Menon", "Gupta", "Fernandes", "Singh", "Rao"]


def festivals() -> pd.DataFrame:
    f = pd.read_csv(os.path.join(config.settings.reference_dir, "festivals.csv"), parse_dates=["date"])
    f["regions"] = f["regions"].str.split("|")
    return f


def _ist(day: dt.date, hh: int, mm: int = 0) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def _utc(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _naive_ist(t: dt.datetime) -> str:            # what the POS really sends: local time, no offset
    return t.strftime("%Y-%m-%d %H:%M:%S")


def env(event_id, event_type, occurred_at, source, actor, payload):
    return {"event_id": event_id, "event_type": event_type, "occurred_at": occurred_at, "source": source,
            "actor": actor, "payload": payload}


def simulate(end_date: str = "2026-11-24", days: int = 730, n_stores: int = 16, n_skus: int = 25, seed: int = 42,
             defects: bool = True, out_dir: str | None = None) -> dict:
    rng = np.random.default_rng(seed)
    out_dir = out_dir or config.settings.inbox_dir
    end = dt.date.fromisoformat(end_date)
    start = end - dt.timedelta(days=days - 1)
    # a smaller lab keeps every tier: pick stores round-robin across metro, tier-2 and tier-3
    pick = [0, 5, 11, 1, 6, 10, 2, 7, 12, 3, 8, 13, 4, 9, 14, 15]
    stores = sorted((STORES[i] for i in pick[:n_stores]), key=lambda s: s[0])
    skus = SKUS[:n_skus]
    S, K = len(stores), len(skus)
    N = S * K
    st_idx = np.repeat(np.arange(S), K)
    sk_idx = np.tile(np.arange(K), S)
    tier = np.array([stores[i][4] for i in st_idx])
    region = np.array([stores[i][3] for i in st_idx])
    opened = np.array([max(dt.date.fromisoformat(stores[i][5]), start) for i in st_idx])
    ethnic = np.array([skus[k][2] in ETHNIC for k in sk_idx])
    store_noise = rng.uniform(0.85, 1.15, S)[st_idx]
    base = np.array([skus[k][4] for k in sk_idx]) * np.array([TIER_SCALE[t] for t in tier]) * store_noise
    sup_names = list(SUPPLIERS)
    sku_supplier = [sup_names[k % len(sup_names)] for k in range(K)]
    nominal = np.array([SUPPLIERS[sku_supplier[k]] for k in sk_idx]) + np.where(tier == "tier-3", 2, 0)
    review_every_day = tier != "tier-3"          # tier-3 stores are reviewed Mondays and Thursdays only

    fest = festivals()
    diwalis = [d.date() for d in fest.loc[fest.festival == "Diwali", "date"]]

    def fest_factor(day: dt.date) -> np.ndarray:
        f = np.ones(N)
        for _, row in fest.iterrows():
            until = (row["date"].date() - day).days
            if -3 <= until <= 10:
                hit = np.isin(region, row["regions"]) | ("all" in row["regions"])
                if until >= 0:
                    peak = np.where(ethnic, 2.4, 1.5) if row["festival"] in ("Diwali", "Eid al-Fitr", "Onam",
                                                                            "Pongal", "Durga Puja") else \
                        np.where(ethnic, 1.7, 1.25)
                    f = np.where(hit, np.maximum(f, 1 + (peak - 1) * (1 - until / 11)), f)
                else:
                    f = np.where(hit & (f == 1), 0.75, f)
        return f

    # promotions: chain-wide, 7 days, roughly every 50-80 days per SKU, announced 7 days ahead
    promos = []
    for k, (sku, name, cat, colour, _b) in enumerate(skus):
        d = start + dt.timedelta(days=int(rng.integers(10, 60)))
        while d < end + dt.timedelta(days=14):
            promos.append((k, d, d + dt.timedelta(days=6), int(LIST_PRICE[cat] * 0.8)))
            d += dt.timedelta(days=int(rng.integers(50, 80)))

    def promo_factor(day: dt.date) -> np.ndarray:
        on = np.zeros(K, bool)
        for k, a, b, _p in promos:
            if a <= day <= b:
                on[k] = True
        return np.where(on[sk_idx], 1.5, 1.0)

    erp, wms, pos, legacy, truth = [], [], [], [], []
    t0 = _ist(start, 6)
    # ---- reference data (ERP)
    for loc, nm, city, reg, tr, op in stores:
        erp.append(env(f"erp:store:{loc}", "store.upserted", _utc(t0 - dt.timedelta(days=1)), "erp", "system",
                       {"location_id": loc, "store_name": nm, "city": city, "region": reg, "tier": tr,
                        "opened_on": op}))
    for k, (sku, name, cat, colour, _b) in enumerate(skus):
        erp.append(env(f"erp:product:{sku}:erp", "product.upserted", _utc(t0 - dt.timedelta(days=1)), "erp",
                       "system", {"sku": sku, "channel": "erp", "product_name": name, "category": cat,
                                  "colour": colour, "top500": True}))
        for ch in ("web", "app", "pos"):
            n2, c2 = name, colour
            if defects and rng.random() < 0.07:            # R12: channels disagree on some attributes
                c2 = rng.choice(["multi", colour.upper(), "na"])
            if defects and rng.random() < 0.04:
                n2 = name + " - new"
            rec = env(f"erp:product:{sku}:{ch}", "product.upserted", _utc(t0 - dt.timedelta(days=1)), "erp", "system",
                      {"sku": sku, "channel": ch, "product_name": n2, "category": cat, "colour": c2, "top500": True})
            (pos if ch == "pos" else erp).append(rec if ch != "pos" else {**rec, "source": "pos",
                                                                           "event_id": f"pos:product:{sku}"})
        erp.append(env(f"erp:price:{sku}:list", "price.changed", _utc(t0 - dt.timedelta(days=1)), "erp", "system",
                       {"sku": sku, "price": LIST_PRICE[cat], "kind": "list", "valid_from": str(start)}))
    for i, (k, a, b, p) in enumerate(promos):
        announce = a - dt.timedelta(days=7)
        erp.append(env(f"erp:promo:{skus[k][0]}:{a}", "price.changed",
                       _utc(_ist(max(announce, start - dt.timedelta(days=1)), 11)), "erp", "system",
                       {"sku": skus[k][0], "price": p, "kind": "promo", "valid_from": str(a), "valid_to": str(b)}))

    # ---- day-by-day shop
    stock = np.zeros(N, int)
    open_po = []                                     # dicts: idx, po_id, qty, arrive
    obs = np.tile(base, (28, 1))                     # observed (censored) sales, last 28 days
    po_seq = 0
    started = np.zeros(N, bool)
    for di in range(days):
        day = start + dt.timedelta(days=di)
        is_open = opened <= day
        newly = is_open & ~started
        if newly.any():                              # opening stock: 3 weeks of expected sales, counted by WMS
            stock[newly] = np.ceil(base[newly] * 21).astype(int)
            started |= newly
        # 06:00 cycle count: first Sunday of each month, and the first day of each store's history
        count_today = (day.weekday() == 6 and day.day <= 7) or di == 0
        on_order_qty = np.zeros(N, int)
        for po in open_po:
            on_order_qty[po["idx"]] += po["qty"]
        for i in np.where(newly | (is_open & count_today))[0]:
            sku, loc = skus[sk_idx[i]][0], stores[st_idx[i]][0]
            wms.append(env(f"wms:count:{loc}:{sku}:{day}", "stock.counted", _utc(_ist(day, 6)), "wms", "wms:cycle-count",
                           {"sku": sku, "location_id": loc, "available": int(stock[i]),
                            "on_order": int(on_order_qty[i])}))
        # 07:00 deliveries
        arriving = [po for po in open_po if po["arrive"] == day]
        for po in arriving:
            i = po["idx"]
            stock[i] += po["qty"]
            wms.append(env(f"wms:gr:{po['po_id']}", "goods.received", _utc(_ist(day, 7, int(rng.integers(0, 50)))),
                           "wms", "wms:dock", {"po_id": po["po_id"], "sku": skus[sk_idx[i]][0],
                                               "location_id": stores[st_idx[i]][0], "qty": po["qty"]}))
        open_po = [po for po in open_po if po["arrive"] != day]
        # trading day
        season = 1.15 if day.month in (11, 12, 1, 2) else (0.85 if day.month in (7, 8) else 1.0)
        pf = promo_factor(day)
        lam = base * DOW[day.weekday()] * season * fest_factor(day) * pf
        demand = rng.poisson(lam) * is_open
        sales = np.minimum(demand, stock)
        stock -= sales
        shrink = (rng.random(N) < 0.003) & (stock > 0)          # unrecorded loss: ledger vs count drift
        stock -= shrink
        returns = (rng.random(N) < 0.01) & (sales > 0)
        stock += returns
        truth.append(pd.DataFrame({"business_date": str(day), "sku_id": [skus[k][0] for k in sk_idx],
                                   "location_id": [stores[s][0] for s in st_idx], "true_demand": lam.round(3),
                                   "demand_units": demand, "units_sold": sales, "closing_true": stock,
                                   "open": is_open}).loc[is_open])
        outage_p = np.where(tier == "tier-3", 0.02, 0.006)
        outage = rng.random(S) < outage_p[[np.where(st_idx == s)[0][0] for s in range(S)]]
        for i in np.where(is_open & (sales > 0))[0]:
            if outage[st_idx[i]]:
                continue                                        # POS outage: the whole store-day is missing
            sku, loc = skus[sk_idx[i]][0], stores[st_idx[i]][0]
            when = _naive_ist(_ist(day, 22, int(rng.integers(0, 40))))
            amount = int(sales[i] * LIST_PRICE[skus[sk_idx[i]][2]] * (0.8 if pf[i] > 1 else 1))
            pos.append(env(f"pos:{loc}:{day}:{sku}", "sales.daily", when, "pos", f"pos:{loc}",
                           {"business_date": str(day), "sku": sku, "location_id": loc, "units": int(sales[i]),
                            "net_amount": amount}))
        for i in np.where(returns)[0]:
            sku, loc = skus[sk_idx[i]][0], stores[st_idx[i]][0]
            pos.append(env(f"pos:return:{loc}:{day}:{sku}", "stock.moved", _naive_ist(_ist(day, 18)), "pos",
                           f"pos:{loc}", {"sku": sku, "location_id": loc, "kind": "return", "qty": 1,
                                          "ref": f"RTN-{loc}-{day:%Y%m%d}-{sku[-3:]}"}))
        obs = np.vstack([obs[1:], np.where(is_open, sales, obs[-1])])
        # 20:00 replenishment review with the legacy rule (trusts censored sales: the feedback loop)
        on_order_qty = np.zeros(N, int)
        for po in open_po:
            on_order_qty[po["idx"]] += po["qty"]
        avg28 = obs.mean(axis=0)
        review = is_open & (review_every_day | (day.weekday() in (0, 3)))
        need = review & (stock + on_order_qty <= avg28 * (nominal + 3))
        pre_diwali = any(0 <= (dw - day).days <= 21 for dw in diwalis)
        for i in np.where(need)[0]:
            po_seq += 1
            qty = int(max(math.ceil(avg28[i] * 14), 6))
            delay_p = 0.4 if pre_diwali else 0.15
            delay = int(rng.integers(2, 7)) if rng.random() < delay_p else 0
            arrive = day + dt.timedelta(days=int(nominal[i]) + delay)
            po_id = f"PO{po_seq:07d}"
            open_po.append({"idx": i, "po_id": po_id, "qty": qty, "arrive": arrive})
            sku, loc = skus[sk_idx[i]][0], stores[st_idx[i]][0]
            payload = {"po_id": po_id, "sku": sku, "location_id": loc, "qty": qty,
                       "supplier_id": sku_supplier[sk_idx[i]],
                       "expected_date": str(day + dt.timedelta(days=int(nominal[i]))),
                       "lead_time_days": int(nominal[i])}
            if defects and rng.random() < 0.08:                # lead time missing in ERP: filled in and flagged
                payload.pop("lead_time_days")
                payload.pop("expected_date")
            erp.append(env(f"erp:po:{po_id}", "po.raised", _utc(_ist(day, 20, 30)), "erp", "erp:auto-replenish",
                           payload))
        # legacy stock copy (network total, stale and wrong) for the last 120 days
        if (end - day).days < 120:
            for k, (sku, *_x) in enumerate(skus):
                true_total = int(stock[sk_idx == k].sum())
                legacy.append(env(f"legacy:stock_copy:{sku}:{day}", "stock.counted", _naive_ist(_ist(day, 4)),
                                  "legacy", "system", {"sku": sku, "location_id": "NETWORK",
                                                       "available": int(true_total * rng.uniform(0.55, 1.6)),
                                                       "on_order": 0}))

    # ---- customers and consent (POS loyalty desk): the only personal data in the lab
    for c in range(1, 201):
        ref = "LOY-TEST-0001" if c == 1 else f"LOY{100000 + c}"
        fn, ln = (("Test", "Customer") if c == 1 else (rng.choice(FIRST), rng.choice(LAST)))
        loc = stores[int(rng.integers(0, S))][0]
        when = _ist(start + dt.timedelta(days=int(rng.integers(0, days - 1))), 13)
        pos.append(env(f"pos:cust:{ref}", "customer.registered", _naive_ist(when), "pos", f"pos:{loc}",
                       {"customer_ref": ref, "name": f"{fn} {ln}", "phone": f"9{rng.integers(100000000, 999999999)}",
                        "email": f"{str(fn).lower()}.{str(ln).lower()}{c}@example.com", "home_store": loc}))
        for purpose in ("orders", "loyalty") + (("marketing",) if rng.random() < 0.4 or c == 1 else ()):
            pos.append(env(f"pos:consent:{ref}:{purpose}", "consent.changed", _naive_ist(when + dt.timedelta(minutes=1)),
                           "pos", f"pos:{loc}", {"customer_ref": ref, "purpose": purpose, "status": "given",
                                                 "notice_version": "2025-01"}))

    # ---- defects the load job and clean layer must catch
    if defects:
        sales_idx = [i for i, e in enumerate(pos) if e["event_type"] == "sales.daily"]
        pick = rng.choice(sales_idx, size=min(len(sales_idx), 900), replace=False)
        dup_same, dup_new, messy_sku, messy_loc = pick[:250], pick[250:400], pick[400:700], pick[700:900]
        extra = [dict(pos[i]) for i in dup_same]                                   # re-sent: same event_id
        extra += [{**pos[i], "event_id": pos[i]["event_id"] + ":resend"} for i in dup_new]   # re-sent: new id
        for i in messy_sku:
            p = dict(pos[i]["payload"]); p["sku"] = p["sku"].lower() + " "; pos[i] = {**pos[i], "payload": p}
        for i in messy_loc:
            p = dict(pos[i]["payload"]); p["location_id"] = "st-" + p["location_id"][2:].lstrip("0")
            pos[i] = {**pos[i], "payload": p}
        bad = []
        for j in range(20):
            e = dict(pos[sales_idx[j * 97]]); p = dict(e["payload"]); p["units"] = -int(rng.integers(1, 5))
            bad.append({**e, "event_id": e["event_id"] + ":neg", "payload": p})
        for j in range(10):
            e = dict(pos[sales_idx[j * 131 + 7]]); p = dict(e["payload"]); p.pop("net_amount")
            bad.append({**e, "event_id": e["event_id"] + ":nonet", "payload": p})
        for j in range(10):
            e = dict(pos[sales_idx[j * 173 + 3]])
            d = dt.datetime.strptime(e["occurred_at"], "%Y-%m-%d %H:%M:%S")
            bad.append({**e, "event_id": e["event_id"] + ":badtime", "occurred_at": d.strftime("%d/%m/%Y %H:%M")})
        e = dict(pos[sales_idx[5]]); p = dict(e["payload"]); p["sku"] = "SKU99999"
        bad.append({**e, "event_id": e["event_id"] + ":unknownsku", "payload": p})   # passes load, fails clean
        e = dict(pos[sales_idx[6]]); p = dict(e["payload"]); p["sku"] = "KUR-LIN-003"
        bad.append({**e, "event_id": e["event_id"] + ":alias", "payload": p,
                    "occurred_at": e["occurred_at"]})                                 # legacy alias, fixed in clean
        pos += extra + bad
        rng.shuffle(pos)
    os.makedirs(out_dir, exist_ok=True)
    written = {}
    for name, rows in (("erp", erp), ("wms", wms), ("pos", pos), ("legacy", legacy)):
        folder = os.path.join(out_dir, name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{name}_history_{start:%Y%m%d}_{end:%Y%m%d}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, default=str) + "\n")
            if name == "pos" and defects:
                f.write("{this line is not json\n")
        written[name] = len(rows) + (1 if name == "pos" and defects else 0)
    os.makedirs(config.settings.reports_dir, exist_ok=True)
    pd.concat(truth).drop(columns="open").to_csv(os.path.join(config.settings.reports_dir, "sim_truth.csv.gz"),
                                                 index=False)
    return {"start": str(start), "end": str(end), "stores": S, "skus": K, "records": written}
