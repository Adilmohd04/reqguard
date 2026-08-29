def fulfillment_status(ordered: int, shipped: int) -> str:
    if shipped == 0:
        return "pending"
    if shipped >= ordered:
        return "completed"
    return "partial"
