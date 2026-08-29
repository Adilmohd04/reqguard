from datetime import datetime
from discounts.calculator import Coupon, apply_coupon

def test_valid_coupon_applies_discount():
    coupon = Coupon(code="SAVE20", discount_percent=20.0, expires_at=datetime(2030, 1, 1))
    now = datetime(2026, 1, 1)
    result = apply_coupon(100.0, coupon, now)
    assert result == 80.0

def test_zero_discount_coupon():
    coupon = Coupon(code="ZERO", discount_percent=0.0, expires_at=datetime(2030, 1, 1))
    now = datetime(2026, 1, 1)
    assert apply_coupon(50.0, coupon, now) == 50.0
# NOTE: No test passes an expired coupon to verify rejection.
