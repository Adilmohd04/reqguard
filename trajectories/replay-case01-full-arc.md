# Trajectory: reqguard-report

## REQ-01
- [execute] `run_tests` → baseline: 2 passed, 0 failed, exit 0
- **[investigate] Decision:** Selected ship_order because it contains the only cancelled-status guard for the requirement.
- **[plan] Decision:** Deleting the two guard lines is the smallest plausible change that lets a cancelled order ship.
- [plan] `apply_mutation` → remove_guard on src/orders/service.py::ship_order
- [execute] `run_tests` → mutated: 2 passed, 0 failed, exit 0
- **[execute] Verdict:** survived=True
- **[repair] Decision:** pytest.raises(OrderCancelledError) on a cancelled order is the assertion that detects the missing guard.
- **[repair] Verdict:** verified=True, passes_on_original=True, kills_mutant=True

## Cost
- API calls: 0
- Input tokens: 0
- Output tokens: 0
- Cache read tokens: 0
- Cache creation tokens: 0

