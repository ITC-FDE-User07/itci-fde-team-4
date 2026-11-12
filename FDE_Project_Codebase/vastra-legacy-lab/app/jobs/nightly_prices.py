"""Batch job: load the ERP price list file into the monolith at night (prices can be a day old, R4)."""
import csv, os
from app import db

IN = os.path.join(db.ROOT, "data", "drop", "erp_price_list.csv")


def run():
    if not os.path.exists(IN):
        print("no price file tonight; yesterday's prices stay live")
        return 0
    con = db.connect(); n = 0
    with open(IN) as f:
        for r in csv.DictReader(f):
            con.execute("UPDATE products SET base_price=? WHERE sku=?", (int(r["base_price"]), r["sku"])); n += 1
    con.commit(); con.close()
    return n


if __name__ == "__main__":
    print("prices loaded:", run())
