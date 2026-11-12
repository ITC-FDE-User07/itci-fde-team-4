"""Facilitator answer key: recompute evidence items E1-E29 from data/logs.

Run:  python evidence/compute_evidence.py
Participants find the same numbers with evidence/queries.sql (after evidence/load_logs.py).
"""
import csv, os, statistics, datetime as dt, re
from collections import defaultdict

LOGS = os.path.join(os.path.dirname(__file__), "..", "data", "logs")


def read(name):
    with open(os.path.join(LOGS, name)) as f:
        return list(csv.DictReader(f))


def p95(xs):
    xs = sorted(xs)
    return xs[int(0.95 * (len(xs) - 1))]


def t(s):
    return dt.datetime.fromisoformat(s)


out = []
fun = read("web_analytics_daily.csv")
sessions = sum(int(r["sessions"]) for r in fun); orders = sum(int(r["orders"]) for r in fun)
carts = sum(int(r["carts_created"]) for r in fun); revenue = sum(int(r["revenue_inr"]) for r in fun)
months = len({r["date"] for r in fun}) / 30.4
out += [("E1", "Conversion rate", f"{100 * orders / sessions:.2f}%"),
        ("E1*", "Website and app sessions a month", f"{sessions / months / 1e6:.1f} million"),
        ("E2", "Cart abandonment", f"{100 * (1 - orders / carts):.0f}%"),
        ("E3", "Average order value", f"Rs {revenue / orders:,.0f}")]

rum = [r for r in read("rum_product_page_sample.csv") if r["device"] == "mobile"]
out.append(("E4", "Mobile LCP p75 / INP p75",
            f"{sorted(int(r['lcp_ms']) for r in rum)[int(0.75 * len(rum))] / 1000:.1f} s / "
            f"{sorted(int(r['inp_ms']) for r in rum)[int(0.75 * len(rum))]} ms"))

tr = read("checkout_traces_sample.csv")
out.append(("E5", "Checkout p95 (W2.1-W2.5)", f"{p95([int(r['w2_1_to_w2_5_total_ms']) for r in tr]) / 1000:.1f} s"))
out.append(("E13", "Courier call W2.5 median / p95",
            f"{statistics.median(int(r['w2_5_courier_ms']) for r in tr)} ms / {p95([int(r['w2_5_courier_ms']) for r in tr])} ms"))

av = read("availability_monthly_12m.csv")
down = sum(int(r["minutes_down"]) for r in av)
out.append(("E6", "Availability, last 12 months",
            f"{100 * (1 - down / (365 * 1440)):.2f}% ({down / 60:.0f} h down); Diwali outage "
            f"{[r for r in read('incidents.csv') if r['severity'] == 'SEV1'][0]['duration_min']} min"))

pay = read("payment_attempts_sample.csv")
peak = [r for r in pay if t(r["at"]).date().isoformat() in
        {"2026-10-02", "2026-10-03", "2026-10-04", "2026-11-06", "2026-11-07", "2026-11-08"} and 18 <= t(r["at"]).hour <= 22]
out.append(("E7", "Payment failures at sale peak / normal",
            f"{100 * sum(r['status'] == 'failed' for r in peak) / len(peak):.1f}% / "
            f"{100 * sum(r['status'] == 'failed' for r in pay) / len(pay):.1f}% overall"))

od = read("orders_sample_10pct.csv")
out.append(("E8", "Orders cancelled for oversell",
            f"{100 * sum(r['cancel_reason'] == 'out_of_stock_after_payment' for r in od) / len(od):.1f}%"))
out.append(("E14", "Order reaches ERP (median hours after order)",
            f"{statistics.median((t(r['received_by_erp_at']) - t(r['created_at'])).total_seconds() / 3600 for r in od):.0f} h (next morning)"))

av5 = read("top500_sku_availability_daily.csv")
out.append(("E9", "Top 500 SKUs out of stock online", f"{100 * sum(int(r['out_of_stock_online']) for r in av5) / len(av5):.0f}%"))

sync = read("stock_sync_job_log.csv")
gaps = [(t(b["started_at"]) - t(a["started_at"])).total_seconds() / 3600 for a, b in zip(sync, sync[1:])]
out.append(("E10", "Stock sync interval / failed runs",
            f"every {statistics.median(gaps):.0f} h; {sum(r['status'] == 'failed' for r in sync)} failed runs"))

sq = read("search_queries_sample.csv")
out.append(("E11", "Searches with zero results", f"{100 * sum(r['results'] == '0' for r in sq) / len(sq):.0f}%"))

cu = read("customer_records_sample.csv")
key = lambda r: (r["email"].strip().lower(), re.sub(r"\D", "", r["phone"])[-10:])
counts = defaultdict(int)
for r in cu:
    counts[key(r)] += 1
dups = sum(c - 1 for c in counts.values())
out.append(("E12", "Duplicate customer records", f"{100 * dups / len(cu):.0f}%"))

eq = read("care_store_stock_enquiries.csv")
out.append(("E15", "Store-stock enquiry staff time / share becoming orders",
            f"median {statistics.median(int(r['staff_minutes_total']) for r in eq)} min "
            f"(range {min(int(r['staff_minutes_total']) for r in eq)}-{max(int(r['staff_minutes_total']) for r in eq)}); "
            f"{100 * sum(int(r['became_order']) for r in eq) / len(eq):.0f}% become orders"))
trn = read("transfer_requests_email.csv")
out.append(("E16", "Transfer approval by email",
            f"median {statistics.median((t(r['approved_at']) - t(r['requested_at'])).total_seconds() / 86400 for r in trn):.1f} days"))

pl = read("planner_timesheets_weekly.csv")
weeks = len({r["week_start"] for r in pl})
hrs = sum(sum(float(r[k]) for k in ("export_hours", "spreadsheet_hours", "erp_lookup_hours", "po_keying_hours")) for r in pl)
out.append(("E17", "Planner time on data handling", f"{hrs / weeks / 8:.1f} planner-days a week"))
rv = read("reorder_review_emails.csv")
out.append(("E18", "Reorder review cycle",
            f"median {statistics.median((t(r['approved_at']) - t(r['sent_for_review_at'])).total_seconds() / 86400 for r in rv):.1f} days"))

yrs = read("db_orders_by_year.csv")
tot = sum(int(r["orders_rows"]) for r in yrs)
old = sum(int(r["orders_rows"]) for r in yrs if int(r["year"]) <= 2023)
out.append(("E19", "Order history online / older than 3 years", f"{len(yrs)} years; {100 * old / tot:.0f}% older than 3 years"))

cs = read("consent_records_sample.csv")
sdk = read("app_sdk_inventory.csv")
out.append(("E20", "Consent with timestamp and purpose / SDKs receiving PII",
            f"{100 * sum(bool(r['consent_timestamp'] and r['consent_purpose']) for r in cs) / len(cs):.0f}%; "
            f"{sum(r['receives_email'] == 'yes' or r['receives_phone'] == 'yes' for r in sdk)} SDKs"))

pc = read("price_promo_changes.csv")
po = read("pos_overrides_daily.csv")
out.append(("E21", "Price edits with no named user / POS overrides share of store sales",
            f"{sum(not r['named_user'] for r in pc):,} edits; "
            f"{100 * sum(int(r['override_discount_inr']) for r in po) / sum(int(r['store_sales_inr']) for r in po):.1f}%"))

au = read("auth_failed_logins_hourly.csv")
mx = max(au, key=lambda r: int(r["failed_logins"]))
out.append(("E22", "Peak failed logins in one hour / loyalty complaints",
            f"{int(mx['failed_logins']):,} on {mx['date']} {mx['hour']}:00; {len(read('loyalty_points_complaints.csv'))} complaints"))

cc = read("care_contacts_daily.csv")
ret = sum(int(r["contacts"]) for r in cc if r["reason"] == "returns")
rf = read("refunds_sample.csv")
out.append(("E23", "Return-related calls / refund time",
            f"{100 * ret / sum(int(r['contacts']) for r in cc):.0f}% of calls; "
            f"{min(int(r['days']) for r in rf)}-{max(int(r['days']) for r in rf)} days"))

vd = read("vastra_daily_pick_slips.csv")
fz = [float(r["frozen_out_minutes"]) for r in vd if r["frozen_out_minutes"]]
out += [("E24", "Vastra Daily pick time per order", f"{statistics.mean(float(r['pick_minutes']) for r in vd):.0f} min"),
        ("E25", "Ready on time: click-and-collect / 2-hour delivery",
         f"{100 * statistics.mean(int(r['ready_on_time']) for r in vd if r['type'] == 'click_collect'):.0f}% / "
         f"{100 * statistics.mean(int(r['ready_on_time']) for r in vd if r['type'] == '2h_delivery'):.0f}%"),
        ("E26", "Frozen items out of the freezer",
         f"median {statistics.median(fz):.0f} min; {100 * sum(x > 15 for x in fz) / len(fz):.0f}% over 15 min"),
        ("E28", "Walking per order", f"{statistics.mean(int(r['walk_metres']) for r in vd) / 1000:.1f} km")]
rota = read("vastra_daily_rota_monthly.csv")
out += [("E27", "Overtime (12 large stores)", f"{statistics.mean(int(r['overtime_hours']) for r in rota):,.0f} h a month"),
        ("E29", "Picking and packing cost per order",
         f"Rs {sum(int(r['picking_packing_labour_inr']) for r in rota) / sum(int(r['orders']) for r in rota):.0f}")]

w = max(len(d) for _, d, _ in out)
for e, d, v in out:
    print(f"{e:5} {d:<{w}}  {v}")
