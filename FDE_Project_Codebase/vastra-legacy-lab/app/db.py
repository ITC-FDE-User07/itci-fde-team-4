"""SQLite stand-in for the monolith's MySQL database (same tables, simpler to run in the lab)."""
import configparser, os, random, sqlite3, datetime as dt

ROOT = os.path.dirname(os.path.dirname(__file__))
CFG = configparser.ConfigParser()
CFG.read(os.path.join(ROOT, "config.ini"))
DB_PATH = os.path.join(ROOT, CFG["database"]["path"])


def connect():
    # EVIDENCE (R15a): every component opens the database with the same shared login.
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_and_seed(n_products=500):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = connect()
    con.executescript("""
    DROP TABLE IF EXISTS products; DROP TABLE IF EXISTS stock_copy; DROP TABLE IF EXISTS orders;
    DROP TABLE IF EXISTS payments;
    CREATE TABLE products (sku TEXT PRIMARY KEY, name TEXT, category TEXT, base_price INT,
                           sale_price INT, sale_ends_utc TEXT, member_discount_pct INT);
    -- Copy of store and warehouse stock, refreshed every 4 hours by app/jobs/stock_sync.py (R5)
    CREATE TABLE stock_copy (sku TEXT PRIMARY KEY, qty INT, copied_at TEXT);
    CREATE TABLE orders (order_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, channel TEXT,
                         sku TEXT, qty INT, total INT, status TEXT, sent_to_erp INT DEFAULT 0);
    CREATE TABLE payments (payment_id INTEGER PRIMARY KEY AUTOINCREMENT, order_ref TEXT, amount INT,
                           status TEXT, at TEXT);
    """)
    random.seed(7)
    cats = ["kurtas", "shirts", "jeans", "dresses", "footwear", "bags", "home-decor", "bedding"]
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(1, n_products + 1):
        base = random.randrange(799, 4999, 100)
        sale = int(base * 0.8) if i % 3 == 0 else None
        ends = (now + dt.timedelta(hours=random.choice([2, 30, 72]))).isoformat() if sale else None
        con.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?)",
                    (f"SKU{i:05d}", f"Product {i}", random.choice(cats), base, sale, ends, 10))
        con.execute("INSERT INTO stock_copy VALUES (?,?,?)",
                    (f"SKU{i:05d}", random.randint(0, 40), (now - dt.timedelta(hours=random.uniform(0, 4))).isoformat()))
    con.commit()
    con.close()


if __name__ == "__main__":
    init_and_seed()
    print("seeded", DB_PATH)
