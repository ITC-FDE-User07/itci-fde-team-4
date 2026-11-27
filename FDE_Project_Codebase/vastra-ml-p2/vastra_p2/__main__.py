"""P2 command line.

  python -m vastra_p2 contest [--depth 4] [--tier-weight tier-3=2]   # rule vs model, fairness, gate, model card
  python -m vastra_p2 sign --by "A. Planner" [--role "Head of Supply Chain"]
  python -m vastra_p2 daily-list [--date 2026-11-25]                 # writes both lists + which one is in force
  python -m vastra_p2 explain 2026-11-25:SKU00003:ST011              # the three reasons for one row
"""
import argparse
import datetime as dt
import json
import os
import sys

from dotenv import load_dotenv


def main(argv=None):
    load_dotenv(os.path.join(os.getcwd(), ".env"))
    from vastra_p2 import config
    config.reload_settings()
    from vastra_data import config as dconfig
    dconfig.reload_settings()
    from vastra_p2 import card, daily, explain, features, model as M

    ap = argparse.ArgumentParser(prog="python -m vastra_p2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("contest")
    c.add_argument("--depth", type=int, default=4)
    c.add_argument("--tier-weight", action="append", default=[], help="tier-3=2.0")
    s = sub.add_parser("sign")
    s.add_argument("--by", required=True)
    s.add_argument("--role", default="Head of Supply Chain")
    s.add_argument("--note", default="")
    d = sub.add_parser("daily-list")
    d.add_argument("--date", default=None, help="list date (default: the day after the last data day)")
    e = sub.add_parser("explain")
    e.add_argument("row_id")
    args = ap.parse_args(argv)

    if args.cmd == "contest":
        weights = {k: float(v) for k, v in (x.split("=") for x in args.tier_weight)}
        inputs = features.load_inputs(actor=config.settings.actor)
        df = features.contest_rows(features.build(inputs))
        train, test, cutoff = M.time_split(df)
        mdl = M.train(train, max_depth=args.depth, weights=weights)
        scored = M.score(mdl, test)
        metrics = M.evaluate(scored)
        sens = []
        for k in (config.settings.top_k // 2, config.settings.top_k * 2):
            o = M.evaluate(scored, k)["results"][0]
            sens.append({"k": k, "rule_recall": o["rule_recall"], "model_recall": o["model_recall"]})
        imp = M.feature_importance(mdl)
        signed = card.signoff() is not None
        gate = M.gate(metrics, signed)
        extra = {"train_rows": len(train), "train_end": train["business_date"].max(), "cutoff": cutoff,
                 "depth": args.depth, "tier_weights": weights, "sensitivity": sens,
                 "importance": imp.to_dict(orient="records"), "importance_warning": M.importance_warning(imp),
                 "gate": gate, "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
        M.save(mdl, metrics, extra)
        png = M.plot_importance(imp, os.path.join(config.settings.model_dir, "feature_importance.png"))
        path = card.write(metrics, gate, extra["importance"], extra)
        print(f"test period {metrics['test_period'][0]} .. {metrics['test_period'][1]}, K={metrics['k']} rows/day\n")
        print(f"{'Group':<26}{'stock-outs':>11}{'rule recall':>13}{'model recall':>14}{'rule prec':>11}{'model prec':>12}")
        for r in metrics["results"]:
            f = lambda v: "–" if v is None else f"{v:.1%}"  # noqa: E731
            print(f"{r['group']:<26}{r['stockouts']:>11}{f(r['rule_recall']):>13}{f(r['model_recall']):>14}"
                  f"{f(r['rule_precision']):>11}{f(r['model_precision']):>12}")
        print("\nAcceptance gate:")
        for ch in gate["checks"]:
            print(f"  [{'x' if ch['passed'] else ' '}] {ch['check']}: {ch['evidence']}")
        print(f"\nDECISION: {gate['decision']}\nmodel card: {path}\nimportance: {png}")
    elif args.cmd == "sign":
        print(json.dumps(card.sign(args.by, args.role, args.note), indent=2))
        print("re-run `contest` to refresh the gate and the card")
    elif args.cmd == "daily-list":
        inputs = features.load_inputs(actor=config.settings.actor)
        day = args.date or (dt.date.fromisoformat(inputs["sales"]["business_date"].max())
                            + dt.timedelta(days=1)).isoformat()
        m = daily.make(day, inputs)
        print(json.dumps(m, indent=2))
        import pandas as pd
        lst = pd.read_csv(m["files"][m["in_force"]])
        print(f"\nTop 5 of the {m['in_force']} list for {day}:")
        print(lst.head(5)[["rank", "sku_id", "location_id", "store_tier", "days_of_cover", "suggested_order_qty",
                           "reason_1", "reason_2", "reason_3"]].to_string(index=False))
    elif args.cmd == "explain":
        import pandas as pd
        day = args.row_id.split(":")[0]
        for method in ("model", "rule"):
            p = os.path.join(config.settings.lists_dir, f"{day}_{method}.csv")
            if os.path.exists(p):
                lst = pd.read_csv(p)
                hit = lst[lst.row_id == args.row_id]
                if len(hit):
                    r = hit.iloc[0]
                    print(f"{method} list, rank {r['rank']}: {r['product']} at {r['location_id']} ({r['store_tier']})")
                    print("  " + " · ".join([r["reason_1"], r["reason_2"], r["reason_3"]]))
                else:
                    print(f"{method} list: not in today's top {config.settings.top_k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
