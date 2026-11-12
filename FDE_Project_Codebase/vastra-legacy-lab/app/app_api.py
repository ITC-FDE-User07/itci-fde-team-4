"""Private endpoints used only by the mobile app (copy 2 of the pricing rules).

EVIDENCE (R4): undocumented, unversioned, and different from the web rules:
- coupons do NOT stack with the sale price (the larger discount wins),
- the sale end is compared in local time (IST) without a time zone, so a sale ends 5.5 hours later in the app.
"""
import datetime as dt

IST = dt.timedelta(hours=5, minutes=30)


def price(product, coupon_pct=0, member=False, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    unit = product["base_price"]
    if product["sale_price"] and product["sale_ends_utc"]:
        ends_naive = dt.datetime.fromisoformat(product["sale_ends_utc"]).replace(tzinfo=None)
        if (now.replace(tzinfo=None)) < ends_naive + IST:      # bug: treats UTC end time as local time
            unit = product["sale_price"]
    if member:
        unit = unit * (100 - product["member_discount_pct"]) / 100
    if coupon_pct:
        unit = min(unit, product["base_price"] * (100 - coupon_pct) / 100)
    return round(unit)
