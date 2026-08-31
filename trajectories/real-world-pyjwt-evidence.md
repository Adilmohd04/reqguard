# PyJWT external-validation evidence

Reproducibility artifacts for the real-world PyJWT run. Captured directly by
re-running against the pinned checkout — every line below is command output,
not narration.

## Target under test (Rule 10 — reproducible)

| Field | Value |
|---|---|
| Repository | PyJWT (`https://github.com/jpadilla/pyjwt`), unmodified |
| Commit SHA | `7144e4534c34810f4525dc4578a32addd8212cff` |
| Tag / version | `2.13.0` |
| Commit date | 2026-05-21 |
| Mutated symbol | `jwt/api_jwt.py :: PyJWT._validate_exp` |

Line numbers in the report are valid **only** at this commit. A later clone
will move them; re-derive from the symbol name, not the line number.

## The mutation (boundary_shift)

```diff
--- a/jwt/api_jwt.py
+++ b/jwt/api_jwt.py
@@ _validate_exp
-        if exp <= (now - leeway):
+        if exp < (now - leeway):
             raise ExpiredSignatureError("Signature has expired")
```

The inclusive `<=` is what rejects a token expiring *exactly* at the
leeway-adjusted current time. The strict `<` lets that exact-boundary token
through. This is **not** a claim that PyJWT has a bug — the inclusive
comparison may be deliberate. The demonstrated fact is narrower and executable:
**PyJWT's own test suite does not pin this boundary**, so a suite-passing change
can silently move it.

## Evidence — the suite does not catch the mutation

Baseline (clean PyJWT source):

```
$ .venv/Scripts/python -m pytest ../pyjwt-test/tests -q
365 passed, 4 skipped in 5.99s        # exit 0
```

Mutated (`exp <= …` → `exp < …`, nothing else changed):

```
$ .venv/Scripts/python -m pytest ../pyjwt-test/tests -q
365 passed, 4 skipped in 5.33s        # exit 0 — mutation SURVIVES
```

Same 365 pass on both. The suite is green against code with the expiry
boundary shifted — an executable coverage gap, not an opinion. (The 4 skips
are `cryptography`-backend tests in `test_api_jwk.py` / `test_api_jws.py`,
unrelated to the `exp`/`nbf`/`alg` validation logic under test.)

The mutation was applied to the working tree, the suite run, then reverted;
`git -C ../pyjwt-test diff` is empty afterward — nothing written back into
PyJWT.

## Runtime / cost

| Item | Value |
|---|---|
| PyJWT suite (baseline) | ~6.0 s |
| PyJWT suite (mutated) | ~5.3 s |
| Single-requirement live agent run (earlier, REQ-001) | 15 API calls, ~2 min wall (see `real-world-pyjwt.md`) |

Token cost for the live agent run is not captured in the trajectory: the usage
duck-typing in `tracing/trajectory.py` records `api_call` count but zeroed the
per-provider token fields for this provider (a known accounting gap, not a
spend of zero). API-call count is the reliable cost figure.

## The repair test is version-pinned

The generated repair (`real-world-pyjwt-repair-test.py`) uses a mock clock and
asserts equality-at-the-boundary is rejected. It targets PyJWT at the commit
above; on a different PyJWT version the import paths or `_validate_exp`
signature could differ and the test would need re-derivation.

## Status of the multi-requirement discriminating run

`requirements.md` was expanded to four requirements — one expected-unprotected
(REQ-01, exp boundary, the gap above) and three expected-protected (REQ-02
signature / `InvalidSignatureError`, REQ-03 `nbf` / `ImmatureSignatureError`,
REQ-04 algorithm allow-list / `InvalidAlgorithmError`), each naming the real
enforcing function. Running that full set live to produce a
"3 protected + 1 gap" discriminating report is **pending on model-provider
budget**: at capture time all three configured providers were exhausted
(aerolink 402 "5-hour $5 allowance reached", conduit 429 rate-limited,
openrouter 402 no credits). The pipeline failed over across all three and
surfaced a clean typed error — no false verdict was produced. The run is a
pure spend step with no code work remaining.
