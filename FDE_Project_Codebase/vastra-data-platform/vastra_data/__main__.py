"""Command line for the Week 2 data platform.

  python -m vastra_data init
  python -m vastra_data simulate                    # 24 months of POS/ERP/WMS/legacy history into inbox/
  python -m vastra_data load --source all           # or api-v1 | pos | erp | wms | legacy
  python -m vastra_data clean
  python -m vastra_data ready
  python -m vastra_data pipeline                    # load all + clean + ready (what cron runs every 15 min)
  python -m vastra_data status                      # one query: runs, failed records, alerts
  python -m vastra_data failed list | fix ID --rename a=b --set a=1 | rerun | ignore ID --reason "..."
  python -m vastra_data consent LOY-TEST-0001
  python -m vastra_data withdraw LOY-TEST-0001
  python -m vastra_data erase LOY-TEST-0001 --request DPR-001
  python -m vastra_data access-log
  python -m vastra_data scorecard
  python -m vastra_data bias-chart --sku SKU00003 --store ST011
  python -m vastra_data read ready.sales_daily --purpose replenishment
  python -m vastra_data contract-diff old.yaml new.yaml
"""
import argparse
import json
import logging
import os
import sys

from dotenv import load_dotenv


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def main(argv=None):
    load_dotenv(os.path.join(os.getcwd(), ".env"))
    from vastra_data import config
    config.reload_settings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from vastra_data import alerts, clean, contracts, db, demand, failed, fitness, governance, load, ready, simulate

    ap = argparse.ArgumentParser(prog="python -m vastra_data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    s = sub.add_parser("simulate")
    s.add_argument("--end-date", default="2026-11-24")
    s.add_argument("--days", type=int, default=730)
    s.add_argument("--stores", type=int, default=16)
    s.add_argument("--skus", type=int, default=25)
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--no-defects", action="store_true")
    s = sub.add_parser("load")
    s.add_argument("--source", default="all", choices=["all", "api-v1", "pos", "erp", "wms", "legacy"])
    sub.add_parser("clean")
    sub.add_parser("ready")
    sub.add_parser("pipeline")
    sub.add_parser("status")
    f = sub.add_parser("failed")
    fs = f.add_subparsers(dest="action", required=True)
    fl = fs.add_parser("list")
    fl.add_argument("--status", default="open,fixed")
    fl.add_argument("--limit", type=int, default=20)
    ff = fs.add_parser("fix")
    ff.add_argument("id", type=int)
    ff.add_argument("--rename", action="append", default=[], help="payload.avail=payload.available")
    ff.add_argument("--set", action="append", default=[], help="payload.available=12")
    fr = fs.add_parser("rerun")
    fr.add_argument("ids", nargs="*", type=int)
    fi = fs.add_parser("ignore")
    fi.add_argument("id", type=int)
    fi.add_argument("--reason", required=True)
    for name in ("consent", "withdraw"):
        c = sub.add_parser(name)
        c.add_argument("customer_ref")
    e = sub.add_parser("erase")
    e.add_argument("customer_ref")
    e.add_argument("--request", default="")
    a = sub.add_parser("access-log")
    a.add_argument("--limit", type=int, default=10)
    sub.add_parser("scorecard")
    b = sub.add_parser("bias-chart")
    b.add_argument("--sku", default="SKU00003")
    b.add_argument("--store", default="ST011")
    b.add_argument("--start", default="2026-09-01")
    b.add_argument("--end", default="2026-11-24")
    r = sub.add_parser("read")
    r.add_argument("table")
    r.add_argument("--purpose", required=True)
    r.add_argument("--limit", type=int, default=5)
    d = sub.add_parser("contract-diff")
    d.add_argument("old")
    d.add_argument("new")
    args = ap.parse_args(argv)

    if args.cmd == "init":
        db.init()
        n = governance.sync_catalogue()
        _print({"data_dir": config.settings.data_dir, "catalogue_entries": n})
    elif args.cmd == "simulate":
        _print(simulate.simulate(args.end_date, args.days, args.stores, args.skus, args.seed, not args.no_defects))
    elif args.cmd == "load":
        db.init()
        governance.sync_catalogue()
        _print(load.run_all() if args.source == "all" else load.run(args.source))
    elif args.cmd == "clean":
        _print(clean.build())
    elif args.cmd == "ready":
        try:
            _print(ready.build())
        except ready.ContractViolation as err:
            print(f"READY BUILD ROLLED BACK: {err}", file=sys.stderr)
            return 2
    elif args.cmd == "pipeline":
        db.init()
        governance.sync_catalogue()
        out = {"load": load.run_all(), "clean": {k: v["rows_out"] for k, v in clean.build().items()}}
        try:
            out["ready"] = ready.build()
        except ready.ContractViolation as err:
            out["ready"] = f"ROLLED BACK: {err}"
        _print(out)
    elif args.cmd == "status":
        db.init()
        con = db.connect()
        try:
            raw = [dict(x) for x in con.execute("SELECT source, COUNT(*) records FROM raw.events GROUP BY source")]
            runs = [dict(x) for x in con.execute("SELECT run_id, source, finished_at, read, loaded, duplicates, failed, "
                                                 "status FROM ops.load_runs ORDER BY run_id DESC LIMIT 6")]
        finally:
            con.close()
        _print({"raw_records_by_source": raw, "failed_records": failed.counts(), "last_runs": runs,
                "recent_alerts": [f"{a['at']} {a['message']}" for a in alerts.recent(5)]})
    elif args.cmd == "failed":
        if args.action == "list":
            rows = failed.list_failed(args.status, args.limit)
            for x in rows:
                print(f"#{x['failed_id']} [{x['stage']}/{x['kind']}/{x['status']}] {x['source']} {x['event_id']}\n"
                      f"    reason: {x['reason']}")
            if not rows:
                print("no failed records with status", args.status)
        elif args.action == "fix":
            renames = dict(x.split("=", 1) for x in args.rename)
            sets = dict(x.split("=", 1) for x in args.set)
            _print(failed.fix(args.id, renames, sets))
        elif args.action == "rerun":
            _print(failed.rerun(args.ids or None))
        elif args.action == "ignore":
            failed.ignore(args.id, args.reason)
            print("ignored", args.id)
    elif args.cmd == "consent":
        con = db.connect(restricted=True)
        try:
            token = governance.token_for(con, args.customer_ref)
        finally:
            con.close()
        _print({"customer_ref": args.customer_ref, "customer_token": token,
                "consent": governance.consent_status(token) if token else "no such person (or erased)"})
    elif args.cmd == "withdraw":
        token = governance.withdraw(args.customer_ref)
        _print({"customer_token": token, "consent": governance.consent_status(token)})
    elif args.cmd == "erase":
        _print(governance.erase(args.customer_ref, request_ref=args.request))
    elif args.cmd == "access-log":
        for x in governance.access_log(args.limit):
            print(f"{x['at_utc']} {x['actor']:<14} {x['action']:<8} {x['table_name']:<26} purpose={x['purpose']} "
                  f"allowed={bool(x['allowed'])} {x['detail'] or ''}")
    elif args.cmd == "scorecard":
        card = fitness.scorecard()
        md, js = fitness.write(card)
        for x in card["datasets"]:
            print(f"{x['light']:<6} {x['data']:<36} {x['what_we_do']}")
        print(f"\nwritten: {md}\n         {js}")
    elif args.cmd == "bias-chart":
        import pandas as pd
        sd = governance.read_table("ready.sales_daily", "analytics",
                                   where="WHERE sku_id=? AND location_id=?", params=(args.sku, args.store))
        truth_path = os.path.join(config.settings.reports_dir, "sim_truth.csv.gz")
        truth = pd.read_csv(truth_path) if os.path.exists(truth_path) else None
        path = demand.chart_raw_vs_adjusted(sd, args.sku, args.store, args.start, args.end, truth=truth)
        d = sd[(sd.business_date >= args.start) & (sd.business_date <= args.end)]
        print(f"chart: {path}\nsold-out days: {int(d.is_sold_out.sum())} of {len(d)}; observed units "
              f"{int(d.units_sold.sum())}, adjusted demand {d.adjusted_demand.sum():.0f}")
    elif args.cmd == "read":
        try:
            df = governance.read_table(args.table, args.purpose)
            print(df.head(args.limit).to_string(index=False))
            print(f"... {len(df):,} rows (access logged)")
        except governance.PurposeNotAllowed as err:
            print(f"REFUSED: {err} (refusal logged)", file=sys.stderr)
            return 3
    elif args.cmd == "contract-diff":
        import yaml
        with open(args.old) as fo, open(args.new) as fn:
            problems = contracts.compatible(yaml.safe_load(fo), yaml.safe_load(fn))
        print("compatible: add-only change" if not problems else "BREAKING CHANGE:\n  " + "\n  ".join(problems))
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
