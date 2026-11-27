"""P2 in one table (section 6.2) and the ten starter features, built point-in-time.

  One row        one SKU at one store on one day (top-500 SKUs only)
  Answer key     1 if available stock hits zero in the next 7 days (day d to d+6), else 0
  When we        06:00 IST each day, using only data up to 05:59, i.e. everything up to the end of day d-1
  predict

Three rules against leakage (6.3):
  1. Only use what was known at 05:59. No flags that exist because the stock-out happened (e.g. emergency orders).
  2. Split by time, not at random (model.time_split).
  3. Averages from past days only: the 28-day average for Monday stops at Sunday night (shift(1) below).
Festivals and promotions are allowed because they are published in advance (calendar, promotion announcements).

All reads go through the data catalogue with purpose 'replenishment', so every read is in gov.access_log.
"""
import datetime as dt
import os

import numpy as np
import pandas as pd

from vastra_data import config as dconfig
from vastra_data import governance

FEATURES = ["available", "on_order", "days_of_cover", "adj_sales_7d", "adj_sales_28d", "day_of_week",
            "festival_next_14d", "promo_active", "lead_time_days", "store_tier_code"]
LABEL = "stockout_next_7d"
TIER_CODE = {"metro": 0, "tier-2": 1, "tier-3": 2}
PURPOSE = "replenishment"


def load_inputs(actor=None) -> dict:
    r = lambda t, **kw: governance.read_table(t, PURPOSE, actor=actor, **kw)  # noqa: E731
    return {
        "sales": r("ready.sales_daily"),
        "stock": r("ready.stock_position", where="WHERE snapshot_at LIKE '%T18:29:59+00:00'"),   # IST day ends
        "po": r("ready.po_receipts"),
        "promo": r("ready.promo_calendar"),
        "stores": r("ready.store_master"),
        "products": r("ready.product_master"),
        "festivals": pd.read_csv(os.path.join(dconfig.settings.reference_dir, "festivals.csv")),
    }


def _festival_lookup(dates: pd.Series, regions: pd.Series, fest: pd.DataFrame, horizon=14):
    """For each (date, region): days to the next relevant festival within the horizon, and its name."""
    f = fest.copy()
    f["date"] = pd.to_datetime(f["date"])
    f["regions"] = f["regions"].str.split("|")
    keys = pd.DataFrame({"d": pd.to_datetime(dates), "region": regions}).drop_duplicates()
    out = []
    for region in keys["region"].unique():
        fr = f[f["regions"].map(lambda rs: "all" in rs or region in rs)].sort_values("date")
        ds = keys.loc[keys.region == region, "d"].sort_values()
        m = pd.merge_asof(ds.to_frame(), fr[["date", "festival"]].rename(columns={"date": "fd"}),
                          left_on="d", right_on="fd", direction="forward")
        m["days_to_festival"] = (m["fd"] - m["d"]).dt.days
        m.loc[m["days_to_festival"] >= horizon, ["days_to_festival", "festival"]] = [np.nan, None]
        m["region"] = region
        out.append(m[["d", "region", "days_to_festival", "festival"]])
    return pd.concat(out)


def build(inputs: dict, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Feature rows for prediction days in [start, end] (default: every day with 28 days of history)."""
    sales = inputs["sales"].copy()
    stores = inputs["stores"]
    sales = sales.sort_values(["sku_id", "location_id", "business_date"]).reset_index(drop=True)
    g = sales.groupby(["sku_id", "location_id"])
    # rule 3: averages from past days only. Value on row d = mean of d-7..d-1 / d-28..d-1.
    sales["adj_sales_7d"] = g["adjusted_demand"].transform(lambda s: s.rolling(7, min_periods=3).mean().shift(1))
    sales["adj_sales_28d"] = g["adjusted_demand"].transform(lambda s: s.rolling(28, min_periods=7).mean().shift(1))
    sales["available"] = g["closing_available"].shift(1)                       # stock at end of d-1
    # answer key: min closing stock over d..d+6 is zero (needs 7 future days, else unknown)
    fut_min = g["closing_available"].transform(lambda s: s[::-1].rolling(7, min_periods=7).min()[::-1])
    sales[LABEL] = (fut_min == 0).astype(float).where(fut_min.notna())

    stock = inputs["stock"][["snapshot_at", "sku_id", "location_id", "on_order"]].copy()
    stock["business_date"] = stock["snapshot_at"].str[:10]
    stock = stock.sort_values(["sku_id", "location_id", "business_date"])
    stock["next_day"] = (pd.to_datetime(stock["business_date"]) + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d")
    sales = sales.merge(stock[["sku_id", "location_id", "next_day", "on_order"]].rename(
        columns={"next_day": "business_date"}), on=["sku_id", "location_id", "business_date"], how="left")

    # lead time known at d: median of actual lead times of POs RECEIVED before d; else planned; else 7
    po = inputs["po"].dropna(subset=["received_date"]).copy()
    po = po.sort_values("received_date")
    po["lt_median"] = po.groupby(["sku_id", "location_id"])["lead_time_days"].transform(
        lambda s: s.expanding().median())
    po["avail_from"] = pd.to_datetime(po["received_date"]) + pd.Timedelta(days=1)
    sales["d"] = pd.to_datetime(sales["business_date"])
    sales = sales.sort_values("d")
    sales = pd.merge_asof(sales, po[["sku_id", "location_id", "avail_from", "lt_median"]].sort_values("avail_from"),
                          left_on="d", right_on="avail_from", by=["sku_id", "location_id"], direction="backward")
    planned = inputs["po"].groupby(["sku_id", "location_id"])["lead_time_days"].median().rename("lt_planned")
    sales = sales.merge(planned, on=["sku_id", "location_id"], how="left")
    sales["lead_time_days"] = sales["lt_median"].fillna(sales["lt_planned"]).fillna(7.0)

    promo = inputs["promo"]
    days = [pd.DataFrame({"sku_id": p.sku_id, "loc": p.location_id,
                          "business_date": pd.date_range(p.start_date, p.end_date).strftime("%Y-%m-%d")})
            for p in promo.itertuples()]
    pd_days = pd.concat(days) if days else pd.DataFrame(columns=["sku_id", "loc", "business_date"])
    chain = pd_days[pd_days["loc"] == "ALL"][["sku_id", "business_date"]].drop_duplicates().assign(_p1=1)
    local = pd_days[pd_days["loc"] != "ALL"].rename(columns={"loc": "location_id"}).drop_duplicates().assign(_p2=1)
    sales = sales.merge(chain, on=["sku_id", "business_date"], how="left")
    sales = sales.merge(local, on=["sku_id", "location_id", "business_date"], how="left")
    sales["promo_active"] = (sales.pop("_p1").fillna(0) + sales.pop("_p2").fillna(0) > 0).astype(int)

    sales = sales.merge(stores[["location_id", "region", "opened_on"]], on="location_id", how="left")
    fl = _festival_lookup(sales["business_date"], sales["region"], inputs["festivals"])
    sales = sales.merge(fl, on=["d", "region"], how="left")
    sales["festival_next_14d"] = sales["days_to_festival"].notna().astype(int)

    sales["day_of_week"] = sales["d"].dt.weekday
    sales["store_tier_code"] = sales["store_tier"].map(TIER_CODE)
    sales["days_of_cover"] = sales["available"] / sales["adj_sales_28d"].clip(lower=0.1)
    sales["new_store"] = ((sales["d"] - pd.to_datetime(sales["opened_on"])).dt.days < 365).astype(int)

    out = sales.dropna(subset=["available", "adj_sales_28d", "adj_sales_7d", "on_order"])
    if start:
        out = out[out["business_date"] >= start]
    if end:
        out = out[out["business_date"] <= end]
    keep = ["business_date", "sku_id", "location_id", "store_tier", "new_store", "festival", "days_to_festival"] \
        + FEATURES + [LABEL]
    out = out[keep].sort_values(["business_date", "location_id", "sku_id"]).reset_index(drop=True)
    for c in ("available", "on_order"):
        out[c] = out[c].astype(int)
    return out


def contest_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rows in the contest: label known, and not already sold out at 06:00 (those are already on the urgent list)."""
    return df[df[LABEL].notna() & (df["available"] > 0)].copy()


def prediction_rows(inputs: dict, day: str) -> pd.DataFrame:
    """Rows to score at 06:00 IST on `day`: needs data up to the end of day-1."""
    last = inputs["sales"]["business_date"].max()
    need = (dt.date.fromisoformat(day) - dt.timedelta(days=1)).isoformat()
    if last < need:
        raise ValueError(f"data ends {last}; scoring {day} needs data up to {need}")
    s = inputs["sales"]
    # add an empty row for `day` so the shift(1) features for that day exist
    tmpl = s[s["business_date"] == need].copy()
    tmpl["business_date"] = day
    for c in ("units_sold", "net_amount", "closing_available", "is_sold_out", "adjusted_demand"):
        tmpl[c] = np.nan
    ins = {**inputs, "sales": pd.concat([s[s["business_date"] <= need], tmpl], ignore_index=True)}
    return build(ins, start=day, end=day)
