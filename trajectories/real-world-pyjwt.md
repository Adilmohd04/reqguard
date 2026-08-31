# Trajectory: reqguard-report

## REQ-001
- [execute] `run_tests` → baseline: 365 passed, 0 failed, exit 0
- ⚠ [investigate] Error (cache_miss): cached prefix was invalidated — check for interpolation into the system prompt
- **[investigate] Decision:** Selected the expiration comparison, its validation gate, and the decode orchestration because together they establish when `exp` is checked and prove that an expired payload cannot be returned on the normal decode path.
- **[plan] Decision:** Changing the inclusive expiration comparison to a strict comparison is a plausible off-by-one boundary mistake.
- [plan] `apply_mutation` → boundary_shift on jwt/api_jwt.py::PyJWT._validate_exp
- [execute] `run_tests` → mutated: 365 passed, 0 failed, exit 0
- **[execute] Verdict:** survived=True
- **[repair] Decision:** The `with pytest.raises(ExpiredSignatureError)` assertion detects whether equality at the expiration boundary is rejected.
- **[repair] Verdict:** verified=True, passes_on_original=True, kills_mutant=True

## (orchestration — no requirement attributed)

## Cost
- API calls: 15
- Input tokens: 0
- Output tokens: 0
- Cache read tokens: 0
- Cache creation tokens: 0

