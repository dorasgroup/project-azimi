"""Apply source-specific URL include and exclude globs."""

from fnmatch import fnmatchcase

from app.crawler.schemas import DiscoveredLink, SourceDiscoveryProfile


class SourceUrlAdmissionPolicy:
    """Keep only discovered URLs accepted by one source profile."""

    def __init__(self, profile: SourceDiscoveryProfile) -> None:
        self._include = tuple(profile.include_url_globs)
        self._exclude = tuple(profile.exclude_url_globs)

    def filter(self, links: list[DiscoveredLink]) -> list[DiscoveredLink]:
        accepted: list[DiscoveredLink] = []
        for link in links:
            url = str(link.url)
            if self._exclude and any(fnmatchcase(url, pattern) for pattern in self._exclude):
                continue
            if self._include and not any(fnmatchcase(url, pattern) for pattern in self._include):
                continue
            accepted.append(link)
        return accepted
