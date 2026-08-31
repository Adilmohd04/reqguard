"""The example's test suite.

It passes. It also has holes, deliberately: it never exercises a partially
shipped order, never sends a coupon large enough to hit the 50% cap, and never
tests the free-shipping threshold at its boundary. `tia scan` is supposed to
find exactly those three gaps.
"""

import pytest
from shop.orders import (
    LineItem,
    Order,
    OrderError,
    discount_amount,
    is_completed,
    order_total,
    refund,
    shipping_fee,
    status,
    subtotal,
)


def make_order(quantity=2, unit_price=25.0, shipped=0, paid=0.0):
    return Order(
        order_id="A-1",
        items=[LineItem(sku="WIDGET", quantity=quantity, unit_price=unit_price, shipped=shipped)],
        amount_paid=paid,
    )


# --- REQ-004: an empty order is rejected. Covered properly. ----------------


def test_empty_order_is_rejected():
    order = Order(order_id="A-2", items=[])
    with pytest.raises(OrderError):
        subtotal(order)


def test_empty_order_is_not_completed():
    assert is_completed(Order(order_id="A-3", items=[])) is False


# --- REQ-001: completion. Only the two easy ends are tested. ---------------


def test_order_with_everything_shipped_is_completed():
    order = make_order(quantity=2, shipped=2)
    assert is_completed(order) is True
    assert status(order) == "completed"


def test_order_with_nothing_shipped_is_not_completed():
    order = make_order(quantity=2, shipped=0)
    assert is_completed(order) is False
    assert status(order) == "pending"


# --- REQ-002: the discount cap is never actually exercised. ----------------


def test_small_coupon_is_applied():
    order = make_order(quantity=4, unit_price=25.0)
    assert discount_amount(order, 0.10) == 10.0


def test_negative_coupon_is_rejected():
    order = make_order()
    with pytest.raises(OrderError):
        discount_amount(order, -0.1)


# --- REQ-005: shipping. Both cases are far from the boundary. --------------


def test_large_order_ships_free():
    order = make_order(quantity=6, unit_price=25.0)  # subtotal 150
    assert shipping_fee(order) == 0.0


def test_small_order_pays_shipping():
    order = make_order(quantity=2, unit_price=25.0)  # subtotal 50
    assert shipping_fee(order) == 7.50


def test_order_total_includes_shipping_and_discount():
    order = make_order(quantity=2, unit_price=25.0)  # subtotal 50
    assert order_total(order, coupon_rate=0.10) == 52.50


# --- REQ-003: refunds. Covered properly, including the boundary. -----------


def test_refund_within_paid_amount():
    order = make_order(paid=100.0)
    assert refund(order, 40.0) == 40.0


def test_refund_exactly_the_paid_amount_is_allowed():
    order = make_order(paid=100.0)
    assert refund(order, 100.0) == 100.0


def test_refund_above_paid_amount_is_rejected():
    order = make_order(paid=100.0)
    with pytest.raises(OrderError):
        refund(order, 100.01)


def test_second_refund_cannot_exceed_the_remaining_balance():
    order = make_order(paid=100.0)
    refund(order, 60.0)
    with pytest.raises(OrderError):
        refund(order, 60.0)


def test_zero_refund_is_rejected():
    order = make_order(paid=100.0)
    with pytest.raises(OrderError):
        refund(order, 0.0)
