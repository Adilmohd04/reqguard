from decimal import Decimal, ROUND_HALF_EVEN

def compute_sales_tax(subtotal: str, rate_percent: str) -> str:
    sub = Decimal(subtotal)
    rate = Decimal(rate_percent) / Decimal("100")
    tax = sub * rate
    rounded = tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
    return str(rounded)
