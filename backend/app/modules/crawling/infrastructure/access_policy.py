"""robots.txt access policy for crawler infrastructure."""

from collections.abc import Awaitable, Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.crawler.schemas import CrawlPolicy


HttpGet = Callable[..., Awaitable[httpx.Response]]


class RobotsAccessPolicy:
    """Resolve and cache robots.txt decisions for source URLs."""

    def __init__(self, policy: CrawlPolicy, user_agent: str) -> None:
        self._policy = policy
        self._user_agent = user_agent
        self._cache: dict[str, RobotFileParser | None] = {}

    @property
    def cache(self) -> dict[str, RobotFileParser | None]:
        """Expose the legacy cache object for compatibility and diagnostics."""
        return self._cache

    async def is_allowed(self, url: str, fetch: HttpGet) -> bool:
        if not self._policy.respect_robots_txt:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._cache:
            parser = self._cache[origin]
            return True if parser is None else parser.can_fetch(self._user_agent, url)
        robots_url = f"{origin}/robots.txt"
        try:
            response = await fetch(robots_url)
            if response.status_code >= 400:
                self._cache[origin] = None
                return True
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            self._cache[origin] = parser
            return parser.can_fetch(self._user_agent, url)
        except Exception:
            self._cache[origin] = None
            return True
