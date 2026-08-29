import pytest
from refunds.processor import Payment, process_refund, ExcessiveRefundError

def test_partial_refund_success():
    p = Payment(id="p1", charged_amount=100.0)
    process_refund(p, 30.0)
    assert p.refunded_amount == 30.0

def test_full_refund_success():
    p = Payment(id="p2", charged_amount=100.0)
    process_refund(p, 100.0)
    assert p.refunded_amount == 100.0

def test_refund_exceeding_charge_raises_error():
    p = Payment(id="p3", charged_amount=50.0)
    with pytest.raises(ExcessiveRefundError):
        process_refund(p, 50.01)

def test_cumulative_refund_exceeding_charge_raises():
    p = Payment(id="p4", charged_amount=100.0)
    process_refund(p, 60.0)
    with pytest.raises(ExcessiveRefundError):
        process_refund(p, 45.0)
