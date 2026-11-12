"""Batch job: copy store and warehouse stock into the monolith's stock_copy table.

Scheduled by cron every 4 hours (0 */4 * * *). Between runs, online stock can be up to 4 hours old (R5, E10).
Input: data/drop/stock_from_pos_and_wms.csv (sku, qty) written by the POS and warehouse exports.
"""
import csv, os, datetime as dt
from app import db

DROP = os.path.join(db.ROOT, "data", "drop", "stock_from_pos_and_wms.csv")


def run():
    if not os.path.exists(DROP):
        print("no stock file; stock_copy left as it was")      # a failed export silently leaves stale stock
        return 0
    con = db.connect(); n = 0; now = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(DROP) as f:
        for r in csv.DictReader(f):
            con.execute("INSERT OR REPLACE INTO stock_copy VALUES (?,?,?)", (r["sku"], int(r["qty"]), now)); n += 1
    con.commit(); con.close()
    return n


if __name__ == "__main__":
    print("rows copied:", run())
