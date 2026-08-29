import pytest
from api_auth.tokens import Token, require_write_scope, InsufficientScopeError

def test_token_with_write_scope_succeeds():
    token = Token(token_id="t1", scopes={"read", "write"})
    require_write_scope(token)  # Should not raise

def test_token_without_write_scope_raises_error():
    token = Token(token_id="t2", scopes={"read"})
    with pytest.raises(InsufficientScopeError):
        require_write_scope(token)

def test_empty_scope_token_raises_error():
    token = Token(token_id="t3", scopes=set())
    with pytest.raises(InsufficientScopeError):
        require_write_scope(token)
