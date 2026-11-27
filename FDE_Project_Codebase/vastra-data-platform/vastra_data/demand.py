"""Censored demand: the bias to look for (section 5.2).

A tier-3 store usually sells 10 units a day of a best-selling kurta. It sells out on Thursday and the next
delivery arrives on Monday. The data shows 0 sales for Friday to Sunday. A model reading that data learns
"demand fell to zero", sends less stock, and the store sells out even sooner: a feedback loop.

The simple fix, in three steps:
  1. is_sold_out on every SKU-store-day, from available stock (closing stock 0).
  2. On sold-out days (and days with no POS data), replace the observed sales with the average sales of the same
     SKU and store on its last 7 in-stock days. That is adjusted demand. Past days only: no leakage.
  3. Always report results per store tier (metro, tier-2, tier-3), never only as one overall number.
"""
import os

import numpy as np
import pandas as pd

from vastra_data import config


def adjusted_demand(df: pd.DataFrame, window: int = 7) -> pd.Series:
    """df: one row per sku_id, location_id, business_date with units_sold, is_sold_out, has_pos_data.
    Returns adjusted demand aligned to df.index."""
    d = df.sort_values(["sku_id", "location_id", "business_date"])
    in_stock = (d["is_sold_out"] == 0) & (d["has_pos_data"] == 1)
    obs = d["units_sold"].where(in_stock)

    def last_n_in_stock_mean(s: pd.Series) -> pd.Series:
        valid = s.dropna()
        r = valid.rolling(window, min_periods=1).mean()     # mean of the last n in-stock days up to and incl. row
        return r.reindex(s.index).ffill().shift(1)           # shift: strictly before the day itself

    prior = obs.groupby([d["sku_id"], d["location_id"]], group_keys=False).apply(last_n_in_stock_mean)
    adjusted = np.where(in_stock, d["units_sold"], np.fmax(d["units_sold"], prior.fillna(d["units_sold"])))
    return pd.Series(adjusted, index=d.index).reindex(df.index).round(3)


def tier_summary(sales_daily: pd.DataFrame) -> pd.DataFrame:
    """Sold-out share and hidden demand per store tier (step 3: always per tier)."""
    g = sales_daily.groupby("store_tier")
    out = pd.DataFrame({
        "rows": g.size(),
        "sold_out_share": g["is_sold_out"].mean().round(3),
        "units_sold": g["units_sold"].sum(),
        "adjusted_demand": g["adjusted_demand"].sum().round(0),
    })
    out["hidden_demand_share"] = (1 - out["units_sold"] / out["adjusted_demand"]).round(3)
    return out.reset_index()


def chart_raw_vs_adjusted(sales_daily: pd.DataFrame, sku_id: str, location_id: str, start: str, end: str,
                          path: str | None = None, truth: pd.DataFrame | None = None) -> str:
    """Bias made visible: observed sales vs adjusted demand for one best seller at one store."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = sales_daily[(sales_daily.sku_id == sku_id) & (sales_daily.location_id == location_id)
                    & (sales_daily.business_date >= start) & (sales_daily.business_date <= end)].copy()
    d["date"] = pd.to_datetime(d["business_date"])
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=130)
    for _, r in d[d.is_sold_out == 1].iterrows():
        ax.axvspan(r["date"] - pd.Timedelta(hours=12), r["date"] + pd.Timedelta(hours=12), color="#f3d9d4", lw=0)
    ax.plot(d["date"], d["units_sold"], color="#9a9a9a", lw=1.6, label="Observed sales (POS)")
    ax.plot(d["date"], d["adjusted_demand"], color="#1f5f8b", lw=2.2, label="Adjusted demand")
    if truth is not None:
        t = truth[(truth.sku_id == sku_id) & (truth.location_id == location_id)
                  & (truth.business_date >= start) & (truth.business_date <= end)]
        ax.plot(pd.to_datetime(t["business_date"]), t["true_demand"], color="#c27c0e", lw=1.2, ls="--",
                label="True demand (simulation only)")
    ax.set_title(f"{sku_id} at {location_id}: shaded days are sold out (POS shows 0, demand was not 0)",
                 fontsize=10, loc="left")
    ax.set_ylabel("Units per day")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, fontsize=8, loc="upper left")
    fig.autofmt_xdate()
    fig.tight_layout()
    path = path or os.path.join(config.settings.reports_dir, f"bias_{sku_id}_{location_id}.png")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path
