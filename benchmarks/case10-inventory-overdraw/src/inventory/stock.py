from dataclasses import dataclass

class InsufficientInventoryError(Exception): ...

@dataclass
class StockItem:
    sku: str
    available: int

def reserve_stock(item: StockItem, requested_qty: int) -> StockItem:
    if requested_qty > item.available:
        raise InsufficientInventoryError(f"Requested {requested_qty} exceeds available {item.available}")
    item.available -= requested_qty
    return item
