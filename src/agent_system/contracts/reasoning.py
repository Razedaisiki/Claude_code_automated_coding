from typing import Protocol


class ReasoningProvider(Protocol):
    def complete(self, *, system: str, user: str, max_tokens: int, timeout: int) -> str: ...
