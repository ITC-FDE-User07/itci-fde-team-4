"""Daily risk list (06:00 IST). Writes BOTH lists every day, so the kill switch works instantly:

  out/risk_lists/<day>_rule.csv
  out/risk_lists/<day>_model.csv     (only if a trained model exists)
  out/risk_lists/<day>.json          which list is in force and why

The list in force is the model's only if the acceptance gate passed AND P2_MODE is not 'rule'.
The Week 1 API's planner page (/v1/planner/...) reads these files; the planner approves, edits or rejects each row.
"""
import json
import math
import os

import pandas as pd

from vastra_p2 import config, explain, features, model as M


def suggested_qty(r) -> int:
    """Cover lead time + 7 days of adjusted demand, minus what is on hand and on order."""
    need = math.ceil(r["adj_sales_28d"] * (r["lead_time_days"] + 7)) - r["available"] - r["on_order"]
    return int(max(need, 0))


def _frame(rows: pd.DataFrame, method: str, reasons, names: dict, k: int) -> pd.DataFrame:
    rows = rows.copy()
    rows["reasons"] = reasons
    out = pd.DataFrame({
        "row_id": rows["business_date"] + ":" + rows["sku_id"] + ":" + rows["location_id"],
        "list_date": rows["business_date"], "rank": range(1, len(rows) + 1), "method": method,
        "sku_id": rows["sku_id"], "product": rows["sku_id"].map(names), "location_id": rows["location_id"],
        "store_tier": rows["store_tier"],
        "risk": (rows["risk"] if method == "model" else rows["rule_score"]).round(3),
        "available": rows["available"], "on_order": rows["on_order"], "days_of_cover": rows["days_of_cover"].round(1),
        "lead_time_days": rows["lead_time_days"].round(0).astype(int),
        "suggested_order_qty": rows.apply(suggested_qty, axis=1),
        "reason_1": [r[0] for r in reasons], "reason_2": [r[1] for r in reasons], "reason_3": [r[2] for r in reasons],
    })
    return out.head(k)


def make(day: str, inputs: dict | None = None) -> dict:
    k = config.settings.top_k
    inputs = inputs or features.load_inputs(actor=config.settings.actor)
    rows = features.prediction_rows(inputs, day)
    already_out = rows[rows["available"] <= 0]
    rows = M.rule(rows[rows["available"] > 0])
    names = dict(zip(inputs["products"]["sku_id"], inputs["products"]["name"]))
    os.makedirs(config.settings.lists_dir, exist_ok=True)
    rule_rows = rows.sort_values("rule_score", ascending=False).head(k)
    rule_df = _frame(rule_rows, "rule", explain.rule_reasons(rule_rows), names, k)
    rule_path = os.path.join(config.settings.lists_dir, f"{day}_rule.csv")
    rule_df.to_csv(rule_path, index=False)
    files = {"rule": rule_path}
    gate_winner, why = "rule", "no trained model"
    try:
        mdl = M.load_model()
        metrics = M.load_metrics()
        gate_winner = metrics["gate"]["winner"]
        why = metrics["gate"]["decision"]
        scored = M.score(mdl, rows).sort_values("model_score", ascending=False).head(k)
        model_df = _frame(scored, "model", explain.reasons(mdl, scored), names, k)
        model_path = os.path.join(config.settings.lists_dir, f"{day}_model.csv")
        model_df.to_csv(model_path, index=False)
        files["model"] = model_path
    except FileNotFoundError:
        pass
    in_force = "model" if (gate_winner == "model" and config.settings.mode != "rule" and "model" in files) else "rule"
    reason = (f"acceptance gate: {why}" if config.settings.mode != "rule" else "kill switch: P2_MODE=rule")
    manifest = {"list_date": day, "k": k, "in_force": in_force, "why": reason, "files": files,
                "already_sold_out": int(len(already_out)), "rows_scored": int(len(rows))}
    with open(os.path.join(config.settings.lists_dir, f"{day}.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest
