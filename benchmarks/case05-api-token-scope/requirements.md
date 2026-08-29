# API Token Scope Requirements

REQ-05: API requests attempting write operations must possess the 'write' token scope. Requests with read-only tokens must be rejected with InsufficientScopeError.
