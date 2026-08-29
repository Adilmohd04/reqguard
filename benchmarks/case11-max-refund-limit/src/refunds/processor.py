from dataclasses import dataclass

class ExcessiveRefundError(Exception): ...

@dataclass
class Payment:
    id: str
    charged_amount: float
    refunded_amount: float = 0.0

def process_refund(payment: Payment, amount: float) -> Payment:
    if payment.refunded_amount + amount > payment.charged_amount:
        raise ExcessiveRefundError(f"Refund {amount} exceeds remaining charge")
    payment.refunded_amount += amount
    return payment
