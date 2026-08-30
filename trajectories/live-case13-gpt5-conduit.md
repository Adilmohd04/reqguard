# Trajectory: reqguard-report

## REQ-001
- [execute] `run_tests` → baseline: 2 passed, 0 failed, exit 0
- ⚠ [investigate] Error (cache_miss): cached prefix was invalidated — check for interpolation into the system prompt
- **[investigate] Decision:** I selected the single function fulfillment_status in src/fulfillment/status.py because it is the sole code path that computes order status — its branch structure is exactly where 'completed' is guarded and 'partial' is produced, and the test file itself notes (line 8) that the partial case (80 of 100) is untested, the precise scenario REQ-13 targets.
- **[plan] Decision:** Changing the boundary from `ordered` to `ordered - 1` is the smallest change that lets a one-item-short order satisfy the completion branch, directly producing the prohibited 'completed' status for a partial shipment.
- [plan] `apply_mutation` → boundary_shift on src/fulfillment/status.py::fulfillment_status
- [execute] `run_tests` → mutated: 2 passed, 0 failed, exit 0
- **[execute] Verdict:** survived=True
- **[repair] Decision:** The assertion `assert fulfillment_status(100, 99) == "partial"` fails when the mutation makes `99 >= 99` evaluate to True and therefore returns `"completed"`.
- **[repair] Verdict:** verified=True, passes_on_original=True, kills_mutant=True

## (orchestration — no requirement attributed)

## Cost
- API calls: 8
- Input tokens: 0
- Output tokens: 0
- Cache read tokens: 7,168
- Cache creation tokens: 0

