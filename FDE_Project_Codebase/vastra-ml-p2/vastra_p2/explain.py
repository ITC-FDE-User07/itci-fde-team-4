"""Explaining each score (7.2): three reasons in plain words for every row.

For a decision tree we follow the row's path from the root to its leaf. Each split changes the predicted risk;
that change is credited to the feature used at the split. The three features that raised the risk most become
the reasons, written with the row's own values, e.g. "Days of cover 2.1 · Diwali in 9 days · Supplier lead time
12 days".

Remember: reasons describe how the model behaves, not proof of cause. They help the planner judge; they do not
replace judgment.
"""
import numpy as np
import pandas as pd

from vastra_p2.features import FEATURES

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
TIERS = {0: "Metro", 1: "Tier-2", 2: "Tier-3"}
FALLBACK = ["days_of_cover", "lead_time_days", "festival_next_14d", "adj_sales_7d", "on_order"]


def phrase(feature: str, row) -> str:
    v = row[feature]
    if feature == "days_of_cover":
        return f"Days of cover {v:.1f}"
    if feature == "available":
        unit = "unit" if int(v) == 1 else "units"
        return f"Only {int(v)} {unit} in stock" if v < 10 else f"{int(v)} {unit} in stock"
    if feature == "on_order":
        return "Nothing on order" if v == 0 else f"{int(v)} units on order"
    if feature == "adj_sales_7d":
        return f"Selling {v:.1f}/day last 7 days"
    if feature == "adj_sales_28d":
        return f"Selling {v:.1f}/day last 28 days"
    if feature == "day_of_week":
        return f"{DAYS[int(v)]} (weekend peak)" if int(v) >= 4 else DAYS[int(v)]
    if feature == "festival_next_14d":
        if v and isinstance(row.get("festival"), str):
            return f"{row['festival']} in {int(row['days_to_festival'])} days"
        return "Festival in next 14 days" if v else "No festival in next 14 days"
    if feature == "promo_active":
        return "Promotion running" if v else "No promotion"
    if feature == "lead_time_days":
        return f"Supplier lead time {v:.0f} days"
    if feature == "store_tier_code":
        return f"{TIERS.get(int(v), 'Store')} store"
    return f"{feature} {v}"


def contributions(model, X: pd.DataFrame) -> np.ndarray:
    """Per-row, per-feature change in predicted risk along the decision path (Saabas-style for one tree)."""
    t = model.tree_
    val = t.value[:, 0, :]
    p1 = val[:, 1] / val.sum(axis=1)
    paths = model.decision_path(X[FEATURES]).tocsr()
    out = np.zeros((len(X), len(FEATURES)))
    for i in range(len(X)):
        nodes = paths.indices[paths.indptr[i]:paths.indptr[i + 1]]
        nodes = np.sort(nodes)           # parent ids are always smaller than child ids in sklearn trees
        for parent, child in zip(nodes[:-1], nodes[1:]):
            out[i, t.feature[parent]] += p1[child] - p1[parent]
    return out


def reasons(model, df: pd.DataFrame, n: int = 3) -> list[list[str]]:
    c = contributions(model, df)
    out = []
    for i, (_, row) in enumerate(df.iterrows()):
        order = np.argsort(-c[i])
        picked = [FEATURES[j] for j in order if c[i, j] > 0][:n]
        if len(picked) < n:                      # top up with the planner's usual context, in this order
            picked += [f for f in FALLBACK if f not in picked][:n - len(picked)]
        out.append([phrase(f, row) for f in picked])
    return out


def rule_reasons(df: pd.DataFrame) -> list[list[str]]:
    out = []
    for _, r in df.iterrows():
        out.append([f"Days of cover {r['days_of_cover']:.1f} vs lead time {r['lead_time_days']:.0f} + 2 safety days",
                    phrase("on_order", r), phrase("adj_sales_28d", r)])
    return out
