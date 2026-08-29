from dataclasses import dataclass

class InsufficientScopeError(Exception): ...

@dataclass
class Token:
    token_id: str
    scopes: set[str]

def require_write_scope(token: Token) -> None:
    if "write" not in token.scopes:
        raise InsufficientScopeError("Missing required write scope")
