from dataclasses import dataclass

class InvalidStateTransitionError(Exception): ...

@dataclass
class Subscription:
    id: str
    status: str  # "active" | "cancelled" | "expired"
    term_count: int = 1

def renew_subscription(sub: Subscription) -> Subscription:
    if sub.status in ("cancelled", "expired"):
        raise InvalidStateTransitionError(f"Cannot renew subscription in status {sub.status}")
    sub.term_count += 1
    return sub
