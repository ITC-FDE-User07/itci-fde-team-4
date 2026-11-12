"""Load every CSV in data/logs into evidence/evidence.db (SQLite), one table per file.

Run: python evidence/load_logs.py   then   sqlite3 evidence/evidence.db < evidence/queries.sql
"""
import csv, os, sqlite3

HERE = os.path.dirname(__file__)
LOGS = os.path.join(HERE, "..", "data", "logs")
DB = os.path.join(HERE, "evidence.db")
if os.path.exists(DB):
    os.remove(DB)
con = sqlite3.connect(DB)
for name in sorted(os.listdir(LOGS)):
    if not name.endswith(".csv"):
        continue
    table = name[:-4]
    with open(os.path.join(LOGS, name)) as f:
        r = csv.reader(f); cols = next(r)
        con.execute(f'CREATE TABLE "{table}" ({", ".join(f"[{c}]" for c in cols)})')
        con.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" * len(cols))})', r)
    print(f"{table}: loaded")
con.commit(); con.close()
print("database:", os.path.abspath(DB))
