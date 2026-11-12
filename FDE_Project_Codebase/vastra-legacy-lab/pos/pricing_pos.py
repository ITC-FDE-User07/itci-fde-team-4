"""Store POS pricing (copy 3 of 3), as deployed in every store till.

EVIDENCE (R4, R15a):
- member discount is applied to the BASE price before the sale price is considered,
- coupons are ignored in store while a sale is running,
- managers can override any price with a shared override code; nothing records who did it.
"""
import datetime as dt

OVERRIDE_CODES = {"MGR-OVERRIDE"}        # shared by all shift managers


def price(product, coupon_pct=0, member=False, now=None, override_code=None, override_price=None):
    if override_code in OVERRIDE_CODES and override_price is not None:
        return int(override_price)       # no audit record is written
    now = now or dt.datetime.now(dt.timezone.utc)
    unit = product["base_price"]
    if member:
        unit = unit * (100 - product["member_discount_pct"]) / 100
    on_sale = bool(product["sale_price"] and product["sale_ends_utc"]
                   and now < dt.datetime.fromisoformat(product["sale_ends_utc"]))
    if on_sale:
        unit = min(unit, product["sale_price"])
    if coupon_pct and not on_sale:
        unit = unit * (100 - coupon_pct) / 100
    return round(unit)
