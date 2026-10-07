"""Anti-corruption mappers between legacy crawler schemas and domain models."""

from typing import Any

from app.crawler.schemas import (
    CrawlPolicy as LegacyCrawlPolicy,
    SourceDiscoveryProfile as LegacySourceDiscoveryProfile,
)
from app.modules.crawling.domain.crawl_policy import (
    CrawlPolicy as DomainCrawlPolicy,
    SourceDiscoveryProfile as DomainSourceDiscoveryProfile,
)


def to_domain_crawl_policy(legacy: LegacyCrawlPolicy) -> DomainCrawlPolicy:
    return DomainCrawlPolicy.model_validate(legacy.model_dump())


def to_legacy_crawl_policy(domain: DomainCrawlPolicy | Any) -> LegacyCrawlPolicy:
    if isinstance(domain, LegacyCrawlPolicy):
        return domain
    return LegacyCrawlPolicy.model_validate(domain.model_dump())


def to_domain_discovery_profile(
    legacy: LegacySourceDiscoveryProfile,
) -> DomainSourceDiscoveryProfile:
    return DomainSourceDiscoveryProfile.model_validate(legacy.model_dump())


def to_legacy_discovery_profile(
    domain: DomainSourceDiscoveryProfile | Any,
) -> LegacySourceDiscoveryProfile:
    if isinstance(domain, LegacySourceDiscoveryProfile):
        return domain
    return LegacySourceDiscoveryProfile.model_validate(domain.model_dump())
