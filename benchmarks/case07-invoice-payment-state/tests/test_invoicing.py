from invoicing.service import Invoice, pay_invoice

def test_partial_payment_keeps_open():
    inv = Invoice(id="i1", status="open", balance_due=100.0)
    pay_invoice(inv, 40.0)
    assert inv.status == "open"
    assert inv.balance_due == 60.0

def test_full_payment_marks_paid():
    inv = Invoice(id="i2", status="open", balance_due=50.0)
    pay_invoice(inv, 50.0)
    assert inv.status == "paid"
    assert inv.balance_due == 0.0
# NOTE: No test attempts payment against an already paid or void invoice.
