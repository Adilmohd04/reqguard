from ratelimit.limiter import SlidingRateLimiter

def test_first_request_allowed():
    limiter = SlidingRateLimiter(limit=60)
    assert limiter.record_request("10.0.0.1") == 1

def test_requests_under_limit_allowed():
    limiter = SlidingRateLimiter(limit=60)
    for _ in range(5):
        limiter.record_request("10.0.0.2")
    assert limiter.record_request("10.0.0.2") == 6
# NOTE: No test checks exceeding limit (61+ requests).
