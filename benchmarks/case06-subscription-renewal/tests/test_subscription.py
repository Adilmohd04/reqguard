from billing.subscription import Subscription, renew_subscription

def test_renew_active_subscription():
    sub = Subscription(id="s1", status="active", term_count=1)
    renewed = renew_subscription(sub)
    assert renewed.term_count == 2

def test_renew_increments_term():
    sub = Subscription(id="s2", status="active", term_count=3)
    renew_subscription(sub)
    renew_subscription(sub)
    assert sub.term_count == 5
# NOTE: No test attempts renewing cancelled or expired subscription.
