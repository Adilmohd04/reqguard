class AuthenticationRequiredError(Exception): ...

def extract_bearer_token(headers: dict[str, str]) -> str:
    auth = headers.get("Authorization")
    if not auth or not auth.strip().startswith("Bearer "):
        raise AuthenticationRequiredError("Missing or malformed Authorization header")
    token = auth.split("Bearer ", 1)[1].strip()
    if not token:
        raise AuthenticationRequiredError("Empty Bearer token")
    return token
