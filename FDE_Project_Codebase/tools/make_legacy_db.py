"""Stand-in for vastra-legacy-lab (Option B, as in the Week 1 run guide Appendix B).
Usage: python tools/make_legacy_db.py vastra-legacy-lab/data/vastra_legacy.db"""
import datetime as dt
import os
import sqlite3
import sys

path = sys.argv[1]
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
con = sqlite3.connect(path)
con.executescript("""
DROP TABLE IF EXISTS products; DROP TABLE IF EXISTS stock_copy;
CREATE TABLE products (sku TEXT PRIMARY KEY, name TEXT, category TEXT, base_price INT,
                       sale_price INT, sale_ends_utc TEXT, member_discount_pct INT);
CREATE TABLE stock_copy (sku TEXT PRIMARY KEY, qty INT, copied_at TEXT);
""")
ends = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).replace(microsecond=0).isoformat()
con.execute("INSERT INTO products VALUES ('SKU00003','Linen kurta','kurtas',2000,1600,?,10)", (ends,))
con.execute("INSERT INTO products VALUES ('SKU00007','Denim jacket','jeans',3000,NULL,NULL,10)")
con.execute("INSERT INTO stock_copy VALUES ('SKU00003',5,'2026-11-19T00:00:00+00:00')")
con.execute("INSERT INTO stock_copy VALUES ('SKU00007',1,'2026-11-19T00:00:00+00:00')")
con.commit()
con.close()
print("legacy db written", path)
