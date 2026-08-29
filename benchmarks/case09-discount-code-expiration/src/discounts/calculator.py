from dataclasses import dataclass
from datetime import datetime

class ExpiredCouponError(Exception): ...

@dataclass
class Coupon:
    code: str
    discount_percent: float
    expires_at: datetime

def apply_coupon(total: float, coupon: Coupon, current_time: datetime) -> float:
    if current_time > coupon.expires_at:
        raise ExpiredCouponError(f"Coupon {coupon.code} expired at {coupon.expires_at}")
    return total * (1.0 - coupon.discount_percent / 100.0)
