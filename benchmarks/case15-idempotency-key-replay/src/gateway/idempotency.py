from typing import Callable, Any

class IdempotencyStore:
    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}

    def execute_or_replay(self, key: str, handler: Callable[[], Any]) -> Any:
        if key in self._cache:
            return self._cache[key]
        result = handler()
        self._cache[key] = result
        return result
