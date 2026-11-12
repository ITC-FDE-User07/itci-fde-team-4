"""Generate 3 months of synthetic Vastra & Co. logs (12 Aug - 10 Nov 2026) for S1 evidence collection.

Every figure is synthetic. The files are calibrated to reproduce the case's evidence items E1-E29.
Run:  python tools/generate_logs.py   (writes data/logs/*.csv in about a minute)
"""
import csv, os, random, math, datetime as dt

random.seed(2026)
OUT = os.path.join(os.path.dirname(__file__), "..", "data", "logs")
os.makedirs(OUT, exist_ok=True)
START = dt.date(2026, 8, 12)
END = dt.date(2026, 11, 10)
DAYS = [START + dt.timedelta(d) for d in range((END - START).days + 1)]
SALE_DAYS = {dt.date(2026, 10, 2), dt.date(2026, 10, 3), dt.date(2026, 10, 4),   # Navratri sale weekend
             dt.date(2026, 11, 6), dt.date(2026, 11, 7), dt.date(2026, 11, 8)}   # Diwali sale weekend
DIWALI_OUTAGE = (dt.datetime(2026, 11, 7, 20, 13), 47)                             # 47-minute outage (E6)
STORES = [f"ST{n:03d}" for n in range(1, 141)]
CATS = ["kurtas", "shirts", "jeans", "dresses", "footwear", "bags", "home-decor", "bedding"]


def write(name, header, rows):
    with open(os.path.join(OUT, name), "w", newline="") as f:
        c = csv.writer(f)
        c.writerow(header)
        c.writerows(rows)


def ts(d, h=None):
    return dt.datetime(d.year, d.month, d.day, random.randint(9, 23) if h is None else h,
                       random.randint(0, 59), random.randint(0, 59))


# E1, E2, E3: daily funnel by channel (website and app sessions)
rows = []
for d in DAYS:
    sale = d in SALE_DAYS
    for ch, share in (("web", 0.55), ("app", 0.45)):
        sessions = int(545_000 * share * (2.4 if sale else 1) * random.uniform(0.92, 1.08))
        orders = int(sessions * random.uniform(0.0150, 0.0170) * (0.93 if sale else 1))
        carts = int(orders / random.uniform(0.215, 0.225))          # about 78% of carts abandoned
        checkouts = int(orders / random.uniform(0.62, 0.66))
        rows.append([d, ch, sessions, carts, checkouts, orders, round(orders * random.uniform(1790, 1910))])
write("web_analytics_daily.csv",
      ["date", "channel", "sessions", "carts_created", "checkouts_started", "orders", "revenue_inr"], rows)

# E4 and S10: sampled real-user monitoring on product pages
rows = []
for i in range(120_000):
    d = random.choice(DAYS)
    mobile = random.random() < 0.72
    lcp = max(900, random.lognormvariate(math.log(3700 if mobile else 2200), 0.38))
    inp = max(60, random.lognormvariate(math.log(320 if mobile else 160), 0.4))
    p_conv = max(0.002, (0.019 if mobile else 0.021) * math.exp(-0.022 * (lcp - 2000) / 100))
    rows.append([f"S{i:07d}", d, "mobile" if mobile else "desktop", random.choice(["web", "app"]),
                 int(lcp), int(inp), round(random.uniform(0.02, 0.28), 2), int(random.random() < p_conv)])
write("rum_product_page_sample.csv",
      ["session_id", "date", "device", "channel", "lcp_ms", "inp_ms", "cls", "converted"], rows)

# E5, E13: checkout traces (sample of 40,000 checkouts), steps W2.1-W2.5 run one after another
rows = []
for i in range(40_000):
    w1, w2, w3, w4 = (random.gauss(300, 40), random.gauss(250, 35), random.gauss(400, 60), random.gauss(350, 50))
    w5 = random.lognormvariate(math.log(620), 0.51)
    w5 = min(max(w5, 300), 2000) if random.random() > 0.017 else random.uniform(2000, 5000)
    total = w1 + w2 + w3 + w4 + w5
    rows.append([f"CK{i:06d}", ts(random.choice(DAYS)), random.choice(["web", "app"]),
                 int(w1), int(w2), int(w3), int(w4), int(w5), int(total)])
write("checkout_traces_sample.csv",
      ["trace_id", "started_at", "channel", "w2_1_cart_ms", "w2_2_price_ms", "w2_3_promo_ms",
       "w2_4_stock_ms", "w2_5_courier_ms", "w2_1_to_w2_5_total_ms"], rows)

# E7: payment attempts (sample)
rows = []
for i in range(150_000):
    d = random.choice(DAYS)
    sale = d in SALE_DAYS
    hr = random.choice(range(18, 23)) if sale and random.random() < 0.7 else random.randint(8, 23)
    fail = random.random() < (0.068 if sale and 18 <= hr <= 22 else 0.031)
    reason = random.choice(["gateway_timeout", "otp_failed", "bank_declined", "upi_pending_expired"]) if fail else ""
    rows.append([f"PA{i:07d}", ts(d, hr), random.choice(["card", "upi", "netbanking", "wallet"]),
                 "failed" if fail else "success", reason, int(fail and random.random() < 0.07)])
write("payment_attempts_sample.csv",
      ["attempt_id", "at", "method", "status", "failure_reason", "duplicate_charge_on_retry"], rows)

# E8, E14: orders (10% sample)
rows = []
for i in range(86_000):
    d = random.choice(DAYS)
    created = ts(d)
    oversold = random.random() < 0.0265
    status = "cancelled" if oversold else random.choices(
        ["delivered", "in_transit", "returned", "cancelled"], [0.86, 0.04, 0.08, 0.02])[0]
    reason = "out_of_stock_after_payment" if oversold else ("customer_request" if status == "cancelled" else "")
    erp_at = dt.datetime(created.year, created.month, created.day, 2, random.randint(0, 59)) + dt.timedelta(days=1)
    rows.append([f"O{i:07d}", created, random.choice(["web", "app"]), random.choice(CATS), random.randint(1, 4),
                 random.randint(600, 4200), status, reason, erp_at])
write("orders_sample_10pct.csv",
      ["order_id", "created_at", "channel", "category", "items", "value_inr", "status", "cancel_reason",
       "received_by_erp_at"], rows)

# E10: stock sync job log (every 4 hours)
rows = []
for d in DAYS:
    for h in (0, 4, 8, 12, 16, 20):
        start = dt.datetime(d.year, d.month, d.day, h, 0)
        failed = random.random() < 0.03
        rows.append([start, start + dt.timedelta(minutes=random.randint(11, 38)), "failed" if failed else "ok",
                     0 if failed else random.randint(180_000, 240_000), "lock timeout on stock_copy" if failed else ""])
write("stock_sync_job_log.csv", ["started_at", "finished_at", "status", "rows_copied", "error"], rows)

# E9: daily online availability of the top 500 SKUs
rows = []
for d in DAYS:
    for s in range(1, 501):
        rows.append([d, f"SKU{s:05d}", random.choice(CATS),
                     int(random.random() < (0.15 if d.weekday() >= 3 else 0.07))])
write("top500_sku_availability_daily.csv", ["date", "sku", "category", "out_of_stock_online"], rows)

# E11: search queries (sample)
terms = ["kurta set", "linen shirt", "running shoes", "bedsheet king", "palazzo", "denim jacket", "jutti",
         "cushion cover", "anarkali", "sneakers"]
misspelt = ["kurtha", "plazzo", "bed sheet 90x100", "sneakrs", "chikankari kurta 3xl", "jootis", "tote bagg"]
rows = []
for i in range(60_000):
    zero = random.random() < 0.14
    rows.append([f"Q{i:06d}", ts(random.choice(DAYS)), random.choice(["web", "app"]),
                 random.choice(misspelt) if zero else random.choice(terms), 0 if zero else random.randint(3, 240)])
write("search_queries_sample.csv", ["query_id", "at", "channel", "query", "results"], rows)

# E12: customer records across online, store POS and loyalty (sample of 20,000)
first = ["Aarav", "Diya", "Ishaan", "Meera", "Kabir", "Ananya", "Rohan", "Saanvi", "Vihaan", "Tara"]
last = ["Sharma", "Iyer", "Khan", "Patel", "Reddy", "Das", "Gupta", "Nair"]
rows, n = [], 0
while len(rows) < 20_000:
    fn, ln = random.choice(first), random.choice(last)
    ph = f"9{random.randint(100000000, 999999999)}"
    em = f"{fn.lower()}.{ln.lower()}{random.randint(1, 999)}@mail.example"
    rows.append([f"C{n:06d}", "online", fn, ln, em, ph]); n += 1
    if random.random() < 0.235:   # the same person again, formatted differently
        rows.append([f"C{n:06d}", random.choice(["store_pos", "loyalty"]), fn.upper(), ln, em.upper(),
                     f"+91 {ph[:5]} {ph[5:]}"]); n += 1
write("customer_records_sample.csv", ["record_id", "source", "first_name", "last_name", "email", "phone"], rows[:20_000])

# E6: incidents in the last 3 months, and availability for 12 months
write("incidents.csv", ["id", "started_at", "duration_min", "severity", "summary"], [
    ["INC-301", "2026-08-29 21:40", 18, "SEV2", "Checkout slow; courier API timeouts held page threads"],
    ["INC-307", "2026-09-14 11:05", 26, "SEV2", "Stock sync job lock timeout; online stock 8 hours old"],
    ["INC-312", "2026-10-03 20:31", 22, "SEV2", "Navratri sale: payment failures 9% for 20 minutes"],
    ["INC-318", "2026-10-21 02:10", 35, "SEV3", "Nightly order batch failed; orders reached warehouse at noon"],
    ["INC-325", DIWALI_OUTAGE[0].strftime("%Y-%m-%d %H:%M"), DIWALI_OUTAGE[1], "SEV1",
     "Diwali sale: monolith out of database connections; site down"],
    ["INC-326", "2026-11-08 19:02", 14, "SEV2", "App price differed from web during the sale (promotion rule mismatch)"]])
write("availability_monthly_12m.csv", ["month", "minutes_down", "availability_pct"],
      [[m, dm, round(100 * (1 - dm / (30.4 * 1440)), 3)] for m, dm in
       [("2025-12", 95), ("2026-01", 60), ("2026-02", 88), ("2026-03", 140), ("2026-04", 75), ("2026-05", 160),
        ("2026-06", 110), ("2026-07", 130), ("2026-08", 118), ("2026-09", 146), ("2026-10", 192), ("2026-11", 186)]])

# E15, E16: store-stock enquiries handled by care, and transfer approvals by email
rows, trows = [], []
for i in range(12_000):
    t = ts(random.choice(DAYS))
    handle = random.randint(6, 10) + random.randint(2, 3) + random.randint(5, 30) + random.randint(7, 17)
    converted = int(random.random() < 0.25)
    rows.append([f"EQ{i:05d}", t, random.choice(["phone", "chat"]), random.choice(STORES), handle, converted])
    if converted:
        req = t + dt.timedelta(minutes=handle)
        trows.append([f"TR{i:05d}", req, req + dt.timedelta(hours=random.uniform(20, 50)),
                      random.choice(["ship_from_store", "store_transfer"]), "email"])
write("care_store_stock_enquiries.csv", ["enquiry_id", "at", "via", "store_called", "staff_minutes_total", "became_order"], rows)
write("transfer_requests_email.csv", ["transfer_id", "requested_at", "approved_at", "type", "approved_via"], trows)

# E17, E18: planner timesheets and reorder review by email
rows, rrows = [], []
for d in [x for x in DAYS if x.weekday() == 0]:
    for p in ("planner_a", "planner_b", "planner_c", "planner_d"):
        rows.append([d, p, round(random.uniform(1.2, 2.0), 1), round(random.uniform(0.6, 1.0), 1),
                     round(random.uniform(0.4, 0.8), 1), round(random.uniform(0.6, 1.1), 1)])
    for cat in CATS:
        sent = dt.datetime(d.year, d.month, d.day, 17, 0)
        rrows.append([d, cat, sent, sent + dt.timedelta(hours=random.uniform(20, 46)), random.randint(1, 5)])
write("planner_timesheets_weekly.csv",
      ["week_start", "planner", "export_hours", "spreadsheet_hours", "erp_lookup_hours", "po_keying_hours"], rows)
write("reorder_review_emails.csv", ["week_start", "category", "sent_for_review_at", "approved_at", "email_rounds"], rrows)

# E19: order history kept in the live database
write("db_orders_by_year.csv", ["year", "orders_rows"],
      [[2018, 800_000], [2019, 950_000], [2020, 1_050_000], [2021, 1_200_000], [2022, 1_400_000],
       [2023, 1_600_000], [2024, 3_400_000], [2025, 3_600_000], [2026, 3_000_000]])

# E20: consent records and app SDK inventory
write("consent_records_sample.csv", ["customer_id", "marketing_opt_in", "consent_timestamp", "consent_purpose", "withdrawn_at"],
      [[f"C{i:06d}", random.choice(["Y", "N"]), "", "", ""] for i in range(5_000)])
write("app_sdk_inventory.csv", ["sdk", "purpose", "receives_email", "receives_phone"],
      [["AnalyticsSDK-A", "product analytics", "yes", "no"], ["AdsSDK-B", "ad attribution", "yes", "yes"],
       ["CrashSDK-C", "crash reports", "no", "no"], ["PushSDK-D", "notifications", "yes", "no"]])

# E21: price and promotion edits through shared admin logins, and POS overrides
write("price_promo_changes.csv", ["change_id", "at", "login_used", "sku", "field", "old_value", "new_value", "named_user"],
      [[f"PC{i:05d}", ts(random.choice(DAYS)), random.choice(["admin1", "admin2", "admin3", "admin4"]),
        f"SKU{random.randint(1, 3000):05d}", random.choice(["base_price", "sale_price", "coupon_rule", "member_price"]),
        random.randint(500, 4000), random.randint(400, 4000), ""] for i in range(1_240)])
rows = []
for d in DAYS:
    for st in random.sample(STORES, 40):
        sales = random.randint(180_000, 420_000)
        rows.append([d, st, sales, int(sales * random.uniform(0.012, 0.034)), "MGR-" + st[-3:]])
write("pos_overrides_daily.csv", ["date", "store", "store_sales_inr", "override_discount_inr", "override_code_used"], rows)

# E22: failed logins per hour, and loyalty-points complaints
rows = []
for d in DAYS:
    for h in range(24):
        failed = 38_000 if (d == dt.date(2026, 11, 7) and h == 21) else random.randint(300, 900)
        rows.append([d, h, failed, random.randint(2_000, 9_000)])
write("auth_failed_logins_hourly.csv", ["date", "hour", "failed_logins", "successful_logins"], rows)
write("loyalty_points_complaints.csv", ["complaint_id", "at", "points_lost"],
      [[f"LP{i:04d}", ts(random.choice(DAYS)), random.randint(200, 5000)] for i in range(210)])

# E23: care contacts by reason, and refund times
rows = []
shares = {"order_cancelled": 0.16, "refund_pending": 0.12, "price_mismatch": 0.09, "returns": 0.14,
          "delivery_status": 0.25, "store_stock": 0.06, "other": 0.18}
for d in DAYS:
    total = random.randint(5_500, 7_000)
    for r, s in shares.items():
        rows.append([d, r, int(total * s * random.uniform(0.9, 1.1))])
write("care_contacts_daily.csv", ["date", "reason", "contacts"], rows)
rows = []
for i in range(8_000):
    d0 = random.choice(DAYS); k = random.randint(3, 15)
    rows.append([f"RF{i:05d}", d0, d0 + dt.timedelta(days=k), k])
write("refunds_sample.csv", ["refund_id", "return_received", "refund_paid", "days"], rows)

# E24-E29: Vastra Daily pick slips (3 of the 12 large stores) and monthly rota
rows = []
for d in DAYS:
    for st in ("VD01", "VD02", "VD03"):
        for _ in range(random.randint(360, 440)):
            slot_h = random.choice(range(9, 22))
            slot = dt.datetime(d.year, d.month, d.day, slot_h, 0)
            printed = slot - dt.timedelta(minutes=random.randint(60, 150))
            start = printed + dt.timedelta(minutes=random.randint(5, 60))
            pick = max(8.0, random.gauss(22, 4.5))
            has_frozen = int(random.random() < 0.55)
            frozen_out = (round(random.uniform(6, 14.9), 1) if random.random() < 0.38 else round(random.lognormvariate(math.log(45), 0.3), 1)) if has_frozen else ""
            pack_q = random.randint(10, 20) if 17 <= slot_h <= 20 else random.randint(2, 8)
            ready = start + dt.timedelta(minutes=pick + pack_q + random.randint(3, 12))
            delivery = random.random() < 0.6
            on_time = int(random.random() > (0.16 if delivery else 0.09))
            rows.append([f"VDO{len(rows):07d}", st, "2h_delivery" if delivery else "click_collect", slot, printed, start,
                         round(pick, 1), int(pick * random.uniform(58, 70)), has_frozen, frozen_out, pack_q,
                         ready.replace(microsecond=0), on_time, random.randint(0, 2)])
write("vastra_daily_pick_slips.csv",
      ["order_id", "store", "type", "slot_time", "slip_printed_at", "pick_started_at", "pick_minutes", "walk_metres",
       "has_frozen", "frozen_out_minutes", "packing_queue_minutes", "ready_at", "ready_on_time", "substitutions"], rows)
write("vastra_daily_rota_monthly.csv", ["month", "large_stores", "overtime_hours", "picking_packing_labour_inr", "orders"],
      [["2026-08", 12, 1120, 8_970_000, 146_000], ["2026-09", 12, 1150, 9_140_000, 147_500],
       ["2026-10", 12, 1190, 9_300_000, 150_000]])
print("written to", os.path.abspath(OUT))
