"""Optional durable crawl progress, independent of broker/database implementation."""
from typing import Protocol


class CrawlCheckpointPort(Protocol):
    async def load(self) -> dict[str, object]: ...

    async def save(self, key: str, value: dict[str, object]) -> None: ...

    async def discard(self) -> None:
        """Drop temporary page bodies only after durable terminal persistence."""
        ...
