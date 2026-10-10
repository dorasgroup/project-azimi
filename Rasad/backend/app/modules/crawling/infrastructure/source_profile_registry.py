"""Resolve source URLs to typed crawl profiles without performing network I/O."""

from dataclasses import replace
from urllib.parse import urlparse

from app.crawler.schemas import CrawlPolicy, SourceDiscoveryProfile
from app.modules.crawling.domain.source_profiles import (
    DiscoveryMethod,
    SourceCrawlProfile,
    SourceDiscoveryPlan,
)

_FEEDS = ("/feed", "/rss", "/rss.xml", "/feed.xml")
_SITEMAPS = ("/sitemap_index.xml", "/sitemap.xml")
_SEARCH = ("/search?q={query}", "/search?query={query}", "/?s={query}")
_ARCHIVES = ("/{year}/{month}/", "/archive/{year}/{month}/")
_LOCAL = (
    DiscoveryMethod.FEED,
    DiscoveryMethod.SITEMAP,
    DiscoveryMethod.HTML,
    DiscoveryMethod.SEARCH,
    DiscoveryMethod.ARCHIVE,
    DiscoveryMethod.GDELT,
    DiscoveryMethod.COMMON_CRAWL,
)
_GLOBAL = (
    DiscoveryMethod.SITEMAP,
    DiscoveryMethod.HTML,
    DiscoveryMethod.SEARCH,
    DiscoveryMethod.GDELT,
    DiscoveryMethod.COMMON_CRAWL,
)
_LOCAL_NO_SEARCH = tuple(method for method in _LOCAL if method != DiscoveryMethod.SEARCH)
_GLOBAL_NO_SEARCH = tuple(method for method in _GLOBAL if method != DiscoveryMethod.SEARCH)


def _source(
    key: str,
    host: str,
    *,
    aliases: tuple[str, ...] = (),
    languages: tuple[str, ...] = ("fa",),
    methods: tuple[DiscoveryMethod, ...] = _LOCAL,
    feed_paths: tuple[str, ...] = _FEEDS,
    sitemap_paths: tuple[str, ...] = _SITEMAPS,
    search_templates: tuple[str, ...] = _SEARCH,
    archive_templates: tuple[str, ...] = _ARCHIVES,
    requires_browser: bool = False,
    content_restricted: bool = False,
    remote_egress_required: bool = False,
) -> SourceCrawlProfile:
    return SourceCrawlProfile(
        key=key,
        canonical_host=host,
        aliases=aliases,
        languages=languages,
        methods=methods,
        feed_paths=feed_paths if DiscoveryMethod.FEED in methods else (),
        sitemap_paths=sitemap_paths if DiscoveryMethod.SITEMAP in methods else (),
        search_templates=search_templates if DiscoveryMethod.SEARCH in methods else (),
        archive_templates=archive_templates if DiscoveryMethod.ARCHIVE in methods else (),
        requires_browser=requires_browser,
        content_restricted=content_restricted,
        remote_egress_required=remote_egress_required,
    )


SOURCE_PROFILES = (
    _source("irna", "irna.ir", aliases=("www.irna.ir",)),
    _source("khabaronline", "khabaronline.ir", aliases=("www.khabaronline.ir",)),
    _source("isna", "isna.ir", aliases=("www.isna.ir",)),
    _source("tasnim", "tasnimnews.com", aliases=("www.tasnimnews.com", "tasnimnews.ir", "www.tasnimnews.ir")),
    _source("fars", "farsnews.ir", aliases=("www.farsnews.ir",)),
    _source("yjc", "yjc.ir", aliases=("www.yjc.ir",)),
    _source("snn", "snn.ir", aliases=("www.snn.ir",)),
    _source("alef", "alef.ir", aliases=("www.alef.ir",)),
    _source("shargh", "sharghdaily.com", aliases=("www.sharghdaily.com", "sharghdaily.ir", "www.sharghdaily.ir"), methods=_LOCAL_NO_SEARCH),
    _source("bbc", "bbc.com", aliases=("www.bbc.com",), languages=("fa", "en"), methods=(DiscoveryMethod.FEED, *_GLOBAL), feed_paths=("/persian/index.xml", "/news/10628494"), remote_egress_required=True),
    _source("dw", "dw.com", aliases=("www.dw.com",), languages=("fa", "en"), methods=(DiscoveryMethod.FEED, *_GLOBAL), feed_paths=("/fa-ir/rss", "/en/rss"), remote_egress_required=True),
    _source("iranintl", "iranintl.com", aliases=("www.iranintl.com",), methods=(DiscoveryMethod.FEED, *_GLOBAL), requires_browser=True, remote_egress_required=True),
    _source("radiofarda", "radiofarda.com", aliases=("www.radiofarda.com",), methods=(DiscoveryMethod.FEED, *_GLOBAL), remote_egress_required=True),
    _source("voa", "voanews.com", aliases=("www.voanews.com", "ir.voanews.com"), languages=("fa", "en"), methods=(DiscoveryMethod.FEED, *_GLOBAL), remote_egress_required=True),
    _source("aljazeera", "aljazeera.net", aliases=("www.aljazeera.net",), languages=("ar", "en"), methods=(DiscoveryMethod.FEED, *_GLOBAL), feed_paths=("/aljazeerarss/89a3efde-57af-4f8a-b3f4-e5d70c9e0c4c/4e9f594a-03af-4696-a57b-9fe6f611f454",), remote_egress_required=True),
    _source("guardian", "theguardian.com", aliases=("www.theguardian.com",), languages=("en",), methods=(DiscoveryMethod.FEED, *_GLOBAL_NO_SEARCH), feed_paths=("/world/rss",), remote_egress_required=True),
    _source("euronews", "euronews.com", aliases=("www.euronews.com", "parsi.euronews.com"), languages=("fa", "en"), methods=(DiscoveryMethod.FEED, *_GLOBAL), remote_egress_required=True),
    _source("lemonde", "lemonde.fr", aliases=("www.lemonde.fr",), languages=("fr", "en"), methods=(DiscoveryMethod.FEED, *_GLOBAL), remote_egress_required=True),
    _source("politico", "politico.eu", aliases=("www.politico.eu",), languages=("en",), methods=(DiscoveryMethod.FEED, *_GLOBAL), remote_egress_required=True),
    _source("time", "time.com", aliases=("www.time.com",), languages=("en",), methods=(DiscoveryMethod.FEED, *_GLOBAL), remote_egress_required=True),
    _source("atlantic", "theatlantic.com", aliases=("www.theatlantic.com",), languages=("en",), methods=_GLOBAL, content_restricted=True, remote_egress_required=True),
    _source("economist", "economist.com", aliases=("www.economist.com",), languages=("en",), methods=(DiscoveryMethod.FEED, *_GLOBAL_NO_SEARCH), feed_paths=("/the-world-this-week/rss.xml",), content_restricted=True, remote_egress_required=True),
    _source("foreign_affairs", "foreignaffairs.com", aliases=("www.foreignaffairs.com",), languages=("en",), methods=_GLOBAL, content_restricted=True, remote_egress_required=True),
    _source("ft", "ft.com", aliases=("www.ft.com",), languages=("en",), methods=_GLOBAL, content_restricted=True, remote_egress_required=True),
    _source("nyt", "nytimes.com", aliases=("www.nytimes.com",), languages=("en",), methods=_GLOBAL, content_restricted=True, remote_egress_required=True),
    _source("wapo", "washingtonpost.com", aliases=("www.washingtonpost.com",), languages=("en",), methods=_GLOBAL, content_restricted=True, remote_egress_required=True),
    _source("wsj", "wsj.com", aliases=("www.wsj.com",), languages=("en",), methods=_GLOBAL, content_restricted=True, remote_egress_required=True),
    _source("haaretz", "haaretz.com", aliases=("www.haaretz.com",), languages=("en", "he"), methods=_GLOBAL, content_restricted=True, remote_egress_required=True),
)

GENERIC_PROFILE = _source("generic", "unknown.invalid")


class SourceProfileRegistry:
    """Return an exact host profile or a safe generic fallback."""

    def __init__(self) -> None:
        self._by_host = {
            host.casefold().rstrip("."): profile
            for profile in SOURCE_PROFILES
            for host in profile.hosts
        }

    def resolve(self, source_url: str) -> SourceCrawlProfile:
        host = (urlparse(source_url).hostname or "").casefold().rstrip(".")
        return self._by_host.get(host, replace(GENERIC_PROFILE, canonical_host=host or "unknown.invalid"))


class SourceStrategyRouter:
    """Translate one source profile into an effective legacy crawl policy."""

    def __init__(self, registry: SourceProfileRegistry | None = None) -> None:
        self._registry = registry or SourceProfileRegistry()

    def plan(self, source_url: str, policy: CrawlPolicy) -> SourceDiscoveryPlan:
        profile = self._registry.resolve(source_url)
        configured = policy.discovery_profile
        discovery_profile = SourceDiscoveryProfile(
            replace_default_seeds=configured.replace_default_seeds,
            feed_paths=list(dict.fromkeys((*profile.feed_paths, *configured.feed_paths))),
            sitemap_paths=list(dict.fromkeys((*profile.sitemap_paths, *configured.sitemap_paths))),
            search_templates=list(dict.fromkeys((*profile.search_templates, *configured.search_templates))),
            archive_templates=list(dict.fromkeys((*profile.archive_templates, *configured.archive_templates))),
            include_url_globs=configured.include_url_globs,
            exclude_url_globs=configured.exclude_url_globs,
        )
        enabled = profile.methods
        effective = policy.model_copy(
            update={
                "discovery_profile": discovery_profile,
                "render_javascript": policy.render_javascript or profile.requires_browser,
                "gdelt_discovery_enabled": policy.gdelt_discovery_enabled and DiscoveryMethod.GDELT in enabled,
                "common_crawl_discovery_enabled": policy.common_crawl_discovery_enabled and DiscoveryMethod.COMMON_CRAWL in enabled,
                "historical_discovery_enabled": policy.historical_discovery_enabled and bool({DiscoveryMethod.GDELT, DiscoveryMethod.COMMON_CRAWL}.intersection(enabled)),
            }
        )
        skipped = {
            method.value: "not declared by source profile"
            for method in DiscoveryMethod
            if method not in enabled
        }
        return SourceDiscoveryPlan(profile, effective, enabled, skipped)
