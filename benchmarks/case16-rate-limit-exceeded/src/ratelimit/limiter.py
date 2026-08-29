class RateLimitExceededError(Exception): ...

class SlidingRateLimiter:
    def __init__(self, limit: int = 60) -> None:
        self.limit = limit
        self._counts: dict[str, int] = {}

    def record_request(self, client_ip: str) -> int:
        current = self._counts.get(client_ip, 0) + 1
        if current > self.limit:
            raise RateLimitExceededError(f"Rate limit of {self.limit} exceeded for {client_ip}")
        self._counts[client_ip] = current
        return current
