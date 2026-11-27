"""One-page model card (7.3) and its sign-off. The card is generated from the contest results, so its numbers
always match what was measured. Sign-off is a named person writing their name into out/model/signoff.yaml."""
import datetime as dt
import os

import yaml

from vastra_p2 import config


def signoff_path():
    return os.path.join(config.settings.model_dir, "signoff.yaml")


def sign(by: str, role: str = "Head of Supply Chain", note: str = "") -> dict:
    s = {"signed_by": by, "role": role, "signed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
         "note": note}
    os.makedirs(config.settings.model_dir, exist_ok=True)
    with open(signoff_path(), "w") as f:
        yaml.safe_dump(s, f)
    return s


def signoff() -> dict | None:
    if not os.path.exists(signoff_path()):
        return None
    with open(signoff_path()) as f:
        return yaml.safe_load(f)


def _pct(v):
    return "–" if v is None else f"{v:.1%}"


def write(metrics: dict, gate: dict, importance: list[dict], extra: dict) -> str:
    s = signoff()
    res = {r["group"]: r for r in metrics["results"]}
    fair = "\n".join(
        f"| {g} | {_pct(r['rule_recall'])} | {_pct(r['model_recall'])} | "
        f"{'better' if (r['model_recall'] or 0) > (r['rule_recall'] or 0) else 'worse' if (r['model_recall'] or 0) < (r['rule_recall'] or 0) else 'same'} |"
        for g, r in res.items() if g != "Overall")
    top3 = ", ".join(f"{x['feature']} ({x['importance']:.0%})" for x in importance[:3])
    o = res["Overall"]
    sens = extra.get("sensitivity", [])
    sens_txt = "; ".join(f"K={x['k']}: rule {x['rule_recall']:.1%}, model {x['model_recall']:.1%}" for x in sens)
    text = f"""# Model card: P2 Best-seller Stock-out Risk v0.1

| Section | P2 content |
|---|---|
| What it is | P2 Best-seller Stock-out Risk v0.1 · decision tree (depth 4, balanced classes) · owner: Head of Supply Chain |
| Use it for | Ranking top-500 SKU-store pairs by 7-day stock-out risk, to help planners (K = {metrics['k']} rows a day) |
| Do not use it for | Ordering automatically without approval; decisions about customers or staff; other SKUs until tested |
| Data | ready.sales_daily, ready.stock_position, ready.product_master, ready.store_master, ready.po_receipts, ready.promo_calendar (read for purpose 'replenishment', logged). Known gaps: see the fitness scorecard (sales Amber, legacy stock copy Red and not used, POs Amber) |
| Training | {extra['train_rows']:,} rows up to {extra['train_end']}; 7-day gap; test {metrics['test_period'][0]} to {metrics['test_period'][1]} ({metrics['test_days']} days, includes Dussehra and Diwali) |
| Results | Top-{metrics['k']} recall: rule {_pct(o['rule_recall'])}, model {_pct(o['model_recall'])}. Precision: rule {_pct(o['rule_precision'])}, model {_pct(o['model_precision'])}. Sensitivity: {sens_txt or 'n/a'} |
| What it relies on | {top3} |
| Limits | Censored demand (adjusted demand overshoots just after a festival peak); short Week 1 ledger history; unusual events (strikes, floods, supplier failure); simulated lab data |
| Human control | Planner approves, edits or rejects every row; reasons for overrides recorded; switch back to the rule at any time (P2 mode = rule) |
| Acceptance gate | **{gate['decision'].upper()}** |
| Sign-off | {f"{s['signed_by']} ({s['role']}), {s['signed_at']}" if s else 'NOT SIGNED'} |

## Fairness check (7.1): recall in the top K per store tier

| Store tier | Rule: recall in top K | Model: recall in top K | Better or worse? |
|---|---|---|---|
{fair}

## Acceptance gate

| Check | Passed | Evidence |
|---|---|---|
""" + "\n".join(f"| {c['check']} | {'yes' if c['passed'] else 'NO'} | {c['evidence']} |" for c in gate["checks"]) + f"""

Decision: **{gate['decision']}**. The daily list uses the {gate['winner']}.
"""
    os.makedirs(config.settings.model_dir, exist_ok=True)
    path = os.path.join(config.settings.model_dir, "model_card.md")
    with open(path, "w") as f:
        f.write(text)
    return path
