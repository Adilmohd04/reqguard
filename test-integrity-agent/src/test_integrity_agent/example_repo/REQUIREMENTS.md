# Order service requirements

These are the rules the order service must obey. Each one has a stable id, so
its integrity score can be tracked across runs.

- REQ-001: An order must never be reported as completed unless every line item
  is fully shipped.
- REQ-002: A discount must never reduce an order by more than 50% of its
  subtotal, whatever the coupon says.
- REQ-003: A refund must never exceed the amount the customer actually paid.
- REQ-004: An order with no line items must be rejected rather than priced.
- REQ-005: Shipping is free only when the subtotal is at least 100; below that,
  a flat fee applies.
