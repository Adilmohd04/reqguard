import pytest
from http_auth.guard import extract_bearer_token, AuthenticationRequiredError

def test_valid_bearer_token_extracted():
    headers = {"Authorization": "Bearer my-secret-token"}
    assert extract_bearer_token(headers) == "my-secret-token"

def test_missing_authorization_header_raises():
    with pytest.raises(AuthenticationRequiredError):
        extract_bearer_token({})

def test_malformed_auth_header_raises():
    with pytest.raises(AuthenticationRequiredError):
        extract_bearer_token({"Authorization": "Basic dXNlcjpwYXNz"})

def test_empty_bearer_token_raises():
    with pytest.raises(AuthenticationRequiredError):
        extract_bearer_token({"Authorization": "Bearer   "})
