# S1 discovery logs · 8 Aug to 6 Nov 2026 (synthetic)

The last three months of records the FDE team can search in S1 Part B. Each file is evidence for one or more of E1–E10.

| File | Rows | Evidence | What to look for |
|---|---|---|---|
| change_tickets.csv | 5 | E1, E9 | Weeks from request to deployment; changes made on production, untested |
| incidents.csv | 6 | E6, E7, E9 | Month-end slowness, hotfix outage, re-keyed and duplicate requests |
| refund_decisions_q3_2026.csv | 1,200 | E2, E3 | Compare paid_pct with policy_pct_v4; split by source and rate plan |
| guest_requests_aug-oct_2026.csv | 19,381 | E6 | Same room and item within 30 minutes = duplicate; median minutes_to_ticket |
| front_desk_policy_calls_daily.csv | 91 | E5 | Calls a month and minutes per call |
| mysql_slow_query_log_aug-oct_2026.csv | ~1,000 | E7, E10 | p95 duration at month-end; rows examined; one shared db_user |
| mysql_access_review_2026-10-09.csv | 2 | E10 | One login with all privileges, used by everyone; password in config |
| data_profile_2026-10-03.csv | 5 | E8 | Missing channel, duplicate guests, bad dates, float amounts |

Expected findings: 107 of 1,200 refunds (8.9%) differ from policy; about 6,460 requests a month with 7.0% duplicates and a 38-minute median; about 1,860 policy calls a month; month-end booking-search p95 about 4.3 s.
