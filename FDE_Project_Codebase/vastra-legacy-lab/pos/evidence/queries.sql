-- Evidence query pack for S1 (SQLite). Load the logs first: python evidence/load_logs.py
.headers on
.mode column

-- E1 conversion rate, E2 cart abandonment, E3 average order value
SELECT ROUND(100.0*SUM(orders)/SUM(sessions),2) AS conversion_pct,
       ROUND(100.0*(1-1.0*SUM(orders)/SUM(carts_created)),0) AS cart_abandonment_pct,
       ROUND(1.0*SUM(revenue_inr)/SUM(orders),0) AS aov_inr,
       ROUND(SUM(sessions)/(COUNT(DISTINCT date)/30.4)/1e6,1) AS sessions_millions_per_month
FROM web_analytics_daily;

-- E5 checkout p95 (W2.1-W2.5) and E13 the courier step
SELECT w2_1_to_w2_5_total_ms AS checkout_p95_ms FROM checkout_traces_sample
ORDER BY CAST(w2_1_to_w2_5_total_ms AS INT) LIMIT 1 OFFSET (SELECT CAST(0.95*(COUNT(*)-1) AS INT) FROM checkout_traces_sample);
SELECT ROUND(AVG(w2_2_price_ms)) price, ROUND(AVG(w2_3_promo_ms)) promo, ROUND(AVG(w2_4_stock_ms)) stock,
       ROUND(AVG(w2_5_courier_ms)) courier FROM checkout_traces_sample;

-- E6 availability over 12 months, and the incidents in the last 3 months
SELECT ROUND(100*(1-SUM(minutes_down)/(365.0*1440)),2) AS availability_pct, ROUND(SUM(minutes_down)/60.0) AS hours_down
FROM availability_monthly_12m;
SELECT * FROM incidents ORDER BY started_at;

-- E7 payment failures at sale peaks (18:00-22:00 on sale days) vs overall
SELECT ROUND(100.0*SUM(status='failed')/COUNT(*),1) AS peak_failure_pct FROM payment_attempts_sample
WHERE substr(at,1,10) IN ('2026-10-02','2026-10-03','2026-10-04','2026-11-06','2026-11-07','2026-11-08')
  AND CAST(substr(at,12,2) AS INT) BETWEEN 18 AND 22;
SELECT ROUND(100.0*SUM(duplicate_charge_on_retry)/SUM(status='failed'),1) AS dup_charge_pct_of_failures FROM payment_attempts_sample;

-- E8 oversold orders, E14 time for orders to reach the ERP
SELECT ROUND(100.0*SUM(cancel_reason='out_of_stock_after_payment')/COUNT(*),1) AS oversell_pct FROM orders_sample_10pct;
SELECT ROUND(AVG((julianday(received_by_erp_at)-julianday(created_at))*24),1) AS avg_hours_to_erp FROM orders_sample_10pct;

-- E9 top-500 SKUs out of stock online, E10 stock sync runs
SELECT ROUND(100.0*SUM(out_of_stock_online)/COUNT(*),0) AS oos_pct FROM top500_sku_availability_daily;
SELECT status, COUNT(*) runs FROM stock_sync_job_log GROUP BY status;

-- E11 zero-result searches, with the commonest failing queries
SELECT ROUND(100.0*SUM(results='0')/COUNT(*),0) AS zero_result_pct FROM search_queries_sample;
SELECT query, COUNT(*) n FROM search_queries_sample WHERE results='0' GROUP BY query ORDER BY n DESC;

-- E12 duplicate customers: same email (ignoring case and spaces) AND same phone (ignoring +91 and spaces)
SELECT ROUND(100.0*(COUNT(*)-COUNT(DISTINCT lower(trim(email)) || '|' || substr(replace(replace(phone,'+91',''),' ',''),-10)))/COUNT(*),0)
       AS duplicate_pct FROM customer_records_sample;

-- E15 store-stock enquiries, E16 transfer approval time
SELECT ROUND(AVG(staff_minutes_total)) avg_staff_min, MIN(CAST(staff_minutes_total AS INT)) min_min,
       MAX(CAST(staff_minutes_total AS INT)) max_min, ROUND(100.0*SUM(became_order)/COUNT(*)) pct_became_order
FROM care_store_stock_enquiries;
SELECT ROUND(AVG(julianday(approved_at)-julianday(requested_at)),1) AS avg_days_to_approve FROM transfer_requests_email;

-- E17 planner days a week on data handling, E18 reorder review time
SELECT ROUND(SUM(export_hours+spreadsheet_hours+erp_lookup_hours+po_keying_hours)/COUNT(DISTINCT week_start)/8,1) AS planner_days_per_week
FROM planner_timesheets_weekly;
SELECT ROUND(AVG(julianday(approved_at)-julianday(sent_for_review_at)),1) AS avg_review_days FROM reorder_review_emails;

-- E19 order history kept online
SELECT COUNT(*) years, ROUND(100.0*SUM(CASE WHEN CAST(year AS INT)<=2023 THEN orders_rows ELSE 0 END)/SUM(orders_rows)) pct_older_than_3y
FROM db_orders_by_year;

-- E20 consent records, SDKs receiving personal data
SELECT ROUND(100.0*SUM(consent_timestamp<>'' AND consent_purpose<>'')/COUNT(*)) pct_complete FROM consent_records_sample;
SELECT sdk, purpose FROM app_sdk_inventory WHERE receives_email='yes' OR receives_phone='yes';

-- E21 price edits with no named user, POS overrides as a share of store sales
SELECT login_used, COUNT(*) edits FROM price_promo_changes WHERE named_user='' GROUP BY login_used;
SELECT ROUND(100.0*SUM(override_discount_inr)/SUM(store_sales_inr),1) override_pct FROM pos_overrides_daily;

-- E22 failed logins (the worst hour) and loyalty complaints
SELECT date, hour, failed_logins FROM auth_failed_logins_hourly ORDER BY CAST(failed_logins AS INT) DESC LIMIT 3;
SELECT COUNT(*) loyalty_complaints FROM loyalty_points_complaints;

-- E23 return-related calls and refund times
SELECT ROUND(100.0*SUM(CASE WHEN reason='returns' THEN contacts ELSE 0 END)/SUM(contacts)) pct_returns_calls FROM care_contacts_daily;
SELECT MIN(CAST(days AS INT)) min_days, MAX(CAST(days AS INT)) max_days, ROUND(AVG(days),1) avg_days FROM refunds_sample;

-- E24-E29 Vastra Daily picking
SELECT ROUND(AVG(pick_minutes)) avg_pick_min, ROUND(AVG(walk_metres)/1000.0,1) avg_walk_km FROM vastra_daily_pick_slips;
SELECT type, ROUND(100.0*AVG(ready_on_time)) pct_on_time FROM vastra_daily_pick_slips GROUP BY type;
SELECT ROUND(100.0*SUM(CAST(frozen_out_minutes AS REAL)>15)/COUNT(*)) pct_frozen_over_15 FROM vastra_daily_pick_slips WHERE frozen_out_minutes<>'';
SELECT CAST(substr(slot_time,12,2) AS INT) slot_hour, ROUND(AVG(packing_queue_minutes)) avg_pack_queue FROM vastra_daily_pick_slips GROUP BY slot_hour;
SELECT ROUND(AVG(overtime_hours)) overtime_h_per_month, ROUND(1.0*SUM(picking_packing_labour_inr)/SUM(orders)) cost_per_order_inr FROM vastra_daily_rota_monthly;
