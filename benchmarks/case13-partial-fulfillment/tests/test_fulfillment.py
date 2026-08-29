from fulfillment.status import fulfillment_status

def test_zero_shipped_is_pending():
    assert fulfillment_status(100, 0) == "pending"

def test_all_shipped_is_completed():
    assert fulfillment_status(100, 100) == "completed"
# NOTE: Killer case — nothing tests partial shipment (e.g. 80 of 100).
