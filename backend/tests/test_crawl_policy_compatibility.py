"""Compatibility tests for CrawlPolicy across Bounded Contexts."""

import unittest
from datetime import date

from app.crawler.schemas import CrawlPolicy as LegacyCrawlPolicy
from app.modules.crawling.domain.crawl_policy import CrawlPolicy as CrawlingCrawlPolicy
from app.modules.monitoring.domain.crawl_policy import (
    MonitoringCrawlPolicy,
    effective_crawl_policy,
)


class CrawlPolicyCompatibilityTests(unittest.TestCase):
    def test_field_definitions_and_defaults_match_exactly(self) -> None:
        monitoring_fields = MonitoringCrawlPolicy.model_fields
        crawling_fields = CrawlingCrawlPolicy.model_fields
        legacy_fields = LegacyCrawlPolicy.model_fields

        # 1. Field names must be identical
        self.assertEqual(set(monitoring_fields.keys()), set(crawling_fields.keys()))
        self.assertEqual(set(monitoring_fields.keys()), set(legacy_fields.keys()))

        # 2. Defaults must match across all non-model fields
        m_instance = MonitoringCrawlPolicy()
        c_instance = CrawlingCrawlPolicy()
        l_instance = LegacyCrawlPolicy()

        m_dict = m_instance.model_dump(exclude={"discovery_profile"})
        c_dict = c_instance.model_dump(exclude={"discovery_profile"})
        l_dict = l_instance.model_dump(exclude={"discovery_profile"})

        self.assertEqual(m_dict, c_dict)
        self.assertEqual(m_dict, l_dict)

    def test_round_trip_conversion(self) -> None:
        policy = effective_crawl_policy(
            {"crawl_policy": {"max_pages": 40, "max_concurrency": 2}},
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 20),
            source_count=10,
        )
        self.assertIsInstance(policy, MonitoringCrawlPolicy)

        # Convert to legacy and back
        legacy = LegacyCrawlPolicy.model_validate(policy.model_dump())
        self.assertEqual(legacy.max_pages, policy.max_pages)
        self.assertEqual(legacy.max_concurrency, policy.max_concurrency)
        self.assertEqual(legacy.max_discovered_urls, policy.max_discovered_urls)
