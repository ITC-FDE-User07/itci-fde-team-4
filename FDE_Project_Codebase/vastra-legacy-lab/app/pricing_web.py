"""Pricing and promotions, copy 1 of 3: website checkout (inside the monolith).

EVIDENCE (R4): the same rules are written again in app/app_api.py (copy 2) and pos/pricing_pos.py (copy 3),
and the three copies disagree on coupon stacking, member price order and when a sale ends.
"""
import datetime as dt


def price(product, coupon_pct=0, member=False, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    unit = product["base_price"]
    if product["sale_price"] and product["sale_ends_utc"] and now < dt.datetime.fromisoformat(product["sale_ends_utc"]):
        unit = product["sale_price"]
    if coupon_pct:                      # web: coupons stack with the sale price
        unit = unit * (100 - coupon_pct) / 100
    if member:                          # web: member discount applied last
        unit = unit * (100 - product["member_discount_pct"]) / 100
    return round(unit)
