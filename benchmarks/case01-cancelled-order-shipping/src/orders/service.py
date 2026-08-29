from dataclasses import dataclass

class OrderCancelledError(Exception): ...

@dataclass
class Order:
    id: str
    status: str          # "open" | "cancelled" | "shipped"
    quantity: int

def ship_order(order: Order) -> Order:
    if order.status == "cancelled":            # ← the enforcement point
        raise OrderCancelledError(order.id)
    order.status = "shipped"
    return order
