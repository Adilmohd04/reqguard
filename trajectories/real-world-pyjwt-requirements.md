# Requirements — token expiration enforcement

Derived directly from `jwt/api_jwt.py::_validate_exp` and RFC 7519 (the JWT
spec's `exp` claim semantics). Not synthetic — this is PyJWT's real,
documented behavior.

REQ-01: A JSON Web Token whose `exp` (expiration time) claim is at or before
the current time (adjusted for any configured leeway) must be rejected by
`jwt.decode()`. Attempting to decode such a token must raise
`ExpiredSignatureError`, never return a decoded payload.
