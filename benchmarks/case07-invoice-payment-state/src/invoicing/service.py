from dataclasses import dataclass

class InvoiceClosedError(Exception): ...

@dataclass
class Invoice:
    id: str
    status: str  # "open" | "paid" | "void"
    balance_due: float

def pay_invoice(invoice: Invoice, amount: float) -> Invoice:
    if invoice.status in ("paid", "void"):
        raise InvoiceClosedError(f"Invoice {invoice.id} is {invoice.status}")
    invoice.balance_due -= amount
    if invoice.balance_due <= 0:
        invoice.status = "paid"
    return invoice
