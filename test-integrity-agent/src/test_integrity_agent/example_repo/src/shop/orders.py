"""A small order service, implementing the rules in REQUIREMENTS.md.

The implementation is correct. The test suite next to it is not thorough -- and
that is the point of this example: `pytest -q` is green, and `tia scan` still
finds requirements that nothing defends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

FLAT_SHIPPING_FEE = 7.50
FREE_SHIPPING_THRESHOLD = 100.0
MAX_DISCOUNT_RATE = 0.50


class OrderError(ValueError):
    """Raised when an order violates a rule that makes it unpriceable."""


@dataclass
class LineItem:
    """One product line on an order."""

    sku: str
    quantity: int
    unit_price: float
    shipped: int = 0

    @property
    def subtotal(self) -> float:
        return self.quantity * self.unit_price

    @property
    def fully_shipped(self) -> bool:
        return self.shipped >= self.quantity


@dataclass
class Order:
    """An order and everything that has been charged against it."""

    order_id: str
    items: list[LineItem] = field(default_factory=list)
    amount_paid: float = 0.0
    amount_refunded: float = 0.0


def subtotal(order: Order) -> float:
    """Total price of the line items, before discount and shipping.

    REQ-004: An order with no line items must be rejected rather than priced.
    """
    if not order.items:
        raise OrderError(f"order {order.order_id} has no line items")
    return sum(item.subtotal for item in order.items)


def discount_amount(order: Order, coupon_rate: float) -> float:
    """The money taken off this order by a coupon.

    REQ-002: A discount must never reduce an order by more than 50% of its
    subtotal, whatever the coupon says.
    """
    if coupon_rate < 0:
        raise OrderError("coupon rate cannot be negative")
    base = subtotal(order)
    capped_rate = min(coupon_rate, MAX_DISCOUNT_RATE)
    return round(base * capped_rate, 2)


def shipping_fee(order: Order) -> float:
    """What the customer pays for shipping.

    REQ-005: Shipping is free only when the subtotal is at least 100; below
    that, a flat fee applies.
    """
    if subtotal(order) >= FREE_SHIPPING_THRESHOLD:
        return 0.0
    return FLAT_SHIPPING_FEE


def order_total(order: Order, coupon_rate: float = 0.0) -> float:
    """The amount owed: subtotal, less discount, plus shipping."""
    base = subtotal(order)
    total = base - discount_amount(order, coupon_rate) + shipping_fee(order)
    return round(max(total, 0.0), 2)


def is_completed(order: Order) -> bool:
    """Whether the order can be reported to the customer as completed.

    REQ-001: An order must never be reported as completed unless every line
    item is fully shipped.
    """
    if not order.items:
        return False
    return all(item.fully_shipped for item in order.items)


def status(order: Order) -> str:
    """A human-facing order status."""
    if is_completed(order):
        return "completed"
    if any(item.shipped > 0 for item in order.items):
        return "partially_shipped"
    return "pending"


def refund(order: Order, amount: float) -> float:
    """Refund ``amount`` against ``order`` and return the new refunded total.

    REQ-003: A refund must never exceed the amount the customer actually paid.
    """
    if amount <= 0:
        raise OrderError("refund amount must be positive")
    refundable = order.amount_paid - order.amount_refunded
    if amount > refundable:
        raise OrderError(
            f"refund of {amount} exceeds the refundable balance of {refundable}"
        )
    order.amount_refunded = round(order.amount_refunded + amount, 2)
    return order.amount_refunded
