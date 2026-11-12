# Evidence map: where to find E1-E29

Logs cover **12 August - 10 November 2026** (91 days, including the Navratri and Diwali sale weekends). All data is synthetic.
Load them with `python evidence/load_logs.py`, then run the queries in `evidence/queries.sql`.

| ID | Evidence | Where to look | Expected finding |
|---|---|---|---|
| E1 | Conversion rate | `web_analytics_daily.csv` | 1.6%; ~18 million website and app sessions a month |
| E2 | Cart abandonment | `web_analytics_daily.csv` | 78% |
| E3 | Average order value | `web_analytics_daily.csv` | ₹1,850 |
| E4 | Mobile Core Web Vitals (p75) | `rum_product_page_sample.csv` | LCP 4.8 s; INP 420 ms |
| E5 | Checkout p95 (W2.1-W2.5) | `checkout_traces_sample.csv`; code: `app/monolith.py` | 2.9 s, steps run one after another |
| E6 | Availability, 12 months; Diwali outage | `availability_monthly_12m.csv`, `incidents.csv` | 99.71% (≈25 h down); 47-minute SEV1 on 7 Nov |
| E7 | Payment failures at peak | `payment_attempts_sample.csv`; code: `app/partners/stubs.py` | 6.8% at sale peaks; retries can charge twice |
| E8 | Orders cancelled for oversell | `orders_sample_10pct.csv` | 2.6% |
| E9 | Top 500 SKUs out of stock online | `top500_sku_availability_daily.csv` | 11% (worse late in the week) |
| E10 | Stock sync | `stock_sync_job_log.csv`; code: `app/jobs/stock_sync.py` | every 4 hours, some failed runs |
| E11 | Zero-result searches | `search_queries_sample.csv` | 14% (misspellings, sizes) |
| E12 | Duplicate customers | `customer_records_sample.csv` | 19% |
| E13 | Courier step W2.5 | `checkout_traces_sample.csv` | 300 ms - 2 s, the slowest step |
| E14 | Orders reach ERP | `orders_sample_10pct.csv`; code: `app/jobs/nightly_orders.py` | next morning |
| E15 | Store-stock enquiries | `care_store_stock_enquiries.csv` | 20-60 min staff time; 1 in 4 becomes an order |
| E16 | Transfer approvals | `transfer_requests_email.csv` | 1-2 days by email |
| E17 | Planner data handling | `planner_timesheets_weekly.csv` | ≈2 planner-days a week |
| E18 | Reorder review | `reorder_review_emails.csv` | 1-2 days |
| E19 | Order history online | `db_orders_by_year.csv` | 9 years; 41% older than 3 years |
| E20 | Consent and SDKs | `consent_records_sample.csv`, `app_sdk_inventory.csv` | 0% complete consent records; 3 SDKs receive PII |
| E21 | Unnamed price edits; POS overrides | `price_promo_changes.csv`, `pos_overrides_daily.csv`; code: `config.ini`, `pos/pricing_pos.py` | 1,240 edits by shared logins; overrides 2.3% of store sales |
| E22 | Failed logins; loyalty complaints | `auth_failed_logins_hourly.csv`, `loyalty_points_complaints.csv` | 38,000 in one hour on 7 Nov; 210 complaints |
| E23 | Returns calls; refund time | `care_contacts_daily.csv`, `refunds_sample.csv` | 14% of calls; 3-15 days |
| E24 | Pick time per order | `vastra_daily_pick_slips.csv` | 22 min |
| E25 | Ready on time | `vastra_daily_pick_slips.csv` | 91% click-and-collect; 84% 2-hour delivery |
| E26 | Frozen time out of freezer | `vastra_daily_pick_slips.csv` | median ≈34 min; 62% over 15 min |
| E27 | Overtime | `vastra_daily_rota_monthly.csv` | ≈1,150 h a month (12 large stores) |
| E28 | Walking per order | `vastra_daily_pick_slips.csv` | ≈1.4 km |
| E29 | Cost per order | `vastra_daily_rota_monthly.csv` | ₹62 |

**Code evidence** (read the source): three pricing copies that disagree (`app/pricing_web.py`, `app/app_api.py`, `pos/pricing_pos.py`; R4);
sequential checkout with no timeouts or idempotency (`app/monolith.py`; R2); stale stock copy (`app/jobs/stock_sync.py`; R5);
shared login in plain text (`config.ini`; R15a); nightly order batch (`app/jobs/nightly_orders.py`; E14).
