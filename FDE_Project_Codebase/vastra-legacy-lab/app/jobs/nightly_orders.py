"""Batch job: send the day's orders to the ERP and warehouse at 02:00 (orders reach the warehouse next morning, E14)."""
import csv, os
from app import db

OUT = os.path.join(db.ROOT, "data", "drop", "orders_to_erp.csv")


def run():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    con = db.connect()
    rows = con.execute("SELECT * FROM orders WHERE sent_to_erp=0 AND status='paid'").fetchall()
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        for r in rows:
            w.writerow([r["order_id"], r["created_at"], r["sku"], r["qty"], r["total"]])
    con.execute("UPDATE orders SET sent_to_erp=1 WHERE sent_to_erp=0 AND status='paid'")
    con.commit(); con.close()
    return len(rows)


if __name__ == "__main__":
    print("orders sent:", run())
