"""Rule vs model: a fair contest (section 6.4).

Both candidates rank the same rows; planners review about K rows a day; both are judged on the same K rows.

  Recall in the top K      of the real stock-outs, how many were in the list the planner sees? (main metric)
  Precision in the top K   of the rows in the list, how many really ran out? (planner trust)
  Recall per store tier    does the list work as well for tier-3 stores as for metro stores? (fairness)

Acceptance gate (agreed before training). The model is used only if, on the last 8 weeks:
  (1) its top-K recall beats the rule by at least 10 points;
  (2) no store tier does worse than with the rule;
  (3) the model card is complete and signed.
If any check fails, the rule ships as the daily list. That is a good result too.
"""
import json
import os
import pickle

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier

from vastra_p2 import config
from vastra_p2.features import FEATURES, LABEL

SAFETY_DAYS = 2


def rule(df: pd.DataFrame) -> pd.DataFrame:
    """The baseline: flag if stock will not last until the next delivery (+2 safety days).
    For ranking, the rule's score is how many days short the cover is."""
    out = df.copy()
    out["rule_flag"] = (out["days_of_cover"] < out["lead_time_days"] + SAFETY_DAYS).astype(int)
    out["rule_score"] = (out["lead_time_days"] + SAFETY_DAYS) - out["days_of_cover"]
    return out


def time_split(df: pd.DataFrame, test_weeks: int | None = None):
    """Rule 2 against leakage: train on older months, test on the last 8 weeks (includes Dussehra and Diwali)."""
    test_weeks = test_weeks or config.settings.test_weeks
    last = pd.to_datetime(df["business_date"]).max()
    cutoff = (last - pd.Timedelta(weeks=test_weeks)).strftime("%Y-%m-%d")
    # a 7-day label gap so no training answer key looks into the test period
    gap_end = (pd.to_datetime(cutoff) - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
    return df[df["business_date"] < gap_end].copy(), df[df["business_date"] >= cutoff].copy(), cutoff


def tier_weights(df: pd.DataFrame, weights: dict | None) -> np.ndarray | None:
    """Remedy if a tier does worse: give its rows more weight in training (7.1)."""
    if not weights:
        return None
    return df["store_tier"].map(weights).fillna(1.0).to_numpy()


def train(train_df: pd.DataFrame, max_depth: int = 4, weights: dict | None = None, seed: int = 0):
    model = DecisionTreeClassifier(max_depth=max_depth, class_weight="balanced", min_samples_leaf=100,
                                   random_state=seed)
    model.fit(train_df[FEATURES], train_df[LABEL].astype(int), sample_weight=tier_weights(train_df, weights))
    return model


def score(model, df: pd.DataFrame) -> pd.DataFrame:
    out = rule(df)
    out["risk"] = model.predict_proba(out[FEATURES])[:, 1]
    # ties: the tree gives the same risk to every row in a leaf; break them with the rule's shortfall
    out["model_score"] = out["risk"] + 1e-6 * out["rule_score"].rank(pct=True)
    return out


def top_k(df: pd.DataFrame, score_col: str, k: int | None = None) -> pd.Series:
    """True for rows that are in that day's top K by score."""
    k = k or config.settings.top_k
    rank = df.groupby("business_date")[score_col].rank(method="first", ascending=False)
    return rank <= k


GROUPS = [("Overall", None), ("Metro", ("store_tier", "metro")), ("Tier-2", ("store_tier", "tier-2")),
          ("Tier-3", ("store_tier", "tier-3")), ("New stores (< 12 months)", ("new_store", 1))]


def evaluate(scored: pd.DataFrame, k: int | None = None) -> dict:
    k = k or config.settings.top_k
    scored = scored.copy()
    scored["in_rule"] = top_k(scored, "rule_score", k)
    scored["in_model"] = top_k(scored, "model_score", k)
    y = scored[LABEL].astype(int)
    rows = []
    for name, cond in GROUPS:
        m = pd.Series(True, index=scored.index) if cond is None else scored[cond[0]] == cond[1]
        pos = int(y[m].sum())
        r = {"group": name, "rows": int(m.sum()), "stockouts": pos}
        for who in ("rule", "model"):
            sel = scored[f"in_{who}"] & m
            r[f"{who}_recall"] = round(float((y[sel]).sum() / pos), 4) if pos else None
            r[f"{who}_precision"] = round(float(y[sel].mean()), 4) if sel.any() else None
            r[f"{who}_listed"] = int(sel.sum())
        rows.append(r)
    days = scored["business_date"].nunique()
    return {"k": k, "test_days": days, "test_period": [scored["business_date"].min(), scored["business_date"].max()],
            "results": rows, "rule_flag_rate": round(float(scored["rule_flag"].mean()), 4)}


def gate(metrics: dict, signed: bool) -> dict:
    res = {r["group"]: r for r in metrics["results"]}
    overall = res["Overall"]
    lift = (overall["model_recall"] - overall["rule_recall"]) * 100
    worse = [g for g, r in res.items() if g != "Overall" and r["rule_recall"] is not None
             and r["model_recall"] is not None and r["model_recall"] < r["rule_recall"]]
    checks = [
        {"check": f"(1) top-K recall beats the rule by at least {config.settings.gate_recall_points:.0f} points",
         "passed": lift >= config.settings.gate_recall_points,
         "evidence": f"model {overall['model_recall']:.1%} vs rule {overall['rule_recall']:.1%} ({lift:+.1f} points)"},
        {"check": "(2) no store tier does worse than with the rule", "passed": not worse,
         "evidence": "every tier at least as good" if not worse else f"worse for: {', '.join(worse)}"},
        {"check": "(3) the model card is complete and signed", "passed": signed,
         "evidence": "signed" if signed else "not signed yet: python -m vastra_p2 sign --by \"<name>\""},
    ]
    ok = all(c["passed"] for c in checks)
    return {"checks": checks, "decision": "model ships" if ok else "rule ships", "winner": "model" if ok else "rule"}


def feature_importance(model) -> pd.DataFrame:
    imp = pd.DataFrame({"feature": FEATURES, "importance": model.feature_importances_})
    return imp.sort_values("importance", ascending=False).reset_index(drop=True)


def importance_warning(imp: pd.DataFrame) -> str | None:
    """If a store or SKU id is near the top, suspect leakage or memorising (7.2)."""
    top = imp.head(3)["feature"].tolist()
    ids = [f for f in top if f in ("sku_id", "location_id", "store_id")]
    return f"id-like feature near the top: {ids}" if ids else None


def plot_importance(imp: pd.DataFrame, path: str) -> str:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = imp.sort_values("importance")
    fig, ax = plt.subplots(figsize=(7, 3.8), dpi=130)
    ax.barh(d["feature"], d["importance"], color="#1f5f8b")
    ax.set_xlabel("Share of the tree's splitting power")
    ax.set_title("P2 decision tree: which inputs it relied on", loc="left", fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def save(model, metrics: dict, extra: dict):
    os.makedirs(config.settings.model_dir, exist_ok=True)
    with open(os.path.join(config.settings.model_dir, "p2_tree.pkl"), "wb") as f:
        pickle.dump({"model": model, "features": FEATURES, "version": "0.1"}, f)
    with open(os.path.join(config.settings.model_dir, "metrics.json"), "w") as f:
        json.dump({**metrics, **extra}, f, indent=2, default=str)


def load_model():
    with open(os.path.join(config.settings.model_dir, "p2_tree.pkl"), "rb") as f:
        return pickle.load(f)["model"]


def load_metrics() -> dict:
    with open(os.path.join(config.settings.model_dir, "metrics.json")) as f:
        return json.load(f)
