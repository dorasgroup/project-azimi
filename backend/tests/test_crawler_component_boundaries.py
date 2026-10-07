import ast
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
INFRASTRUCTURE_ROOT = BACKEND_ROOT / "app" / "modules" / "crawling" / "infrastructure"


def _class_methods(path: Path, class_name: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                child.name
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
                and not child.name.startswith("_")
            }
    raise AssertionError(f"{class_name} is missing from {path}")


def test_crawler_runtime_components_have_focused_public_interfaces() -> None:
    expected = {
        "http_transport.py": ("SafeHttpTransport", {"get", "probe"}),
        "access_policy.py": ("RobotsAccessPolicy", {"cache", "is_allowed"}),
        "source_probe.py": ("SourceProbeService", {"probe"}),
        "link_discovery.py": (
            "LinkDiscoveryService",
            {"search_candidates", "prioritize_sitemaps", "discover"},
        ),
        "page_fetcher.py": (
            "Crawl4AiPageFetcher",
            {"create_crawler", "fetch_page", "fetch_all"},
        ),
        "http_page_fetcher.py": ("HttpPageFetcher", {"requires_browser", "fetch"}),
        "fetch_resilience.py": ("ResilientHttpFetcher", {"fetch"}),
        "browser_renderer.py": ("BrowserPageRenderer", {"create_crawler", "render"}),
        "gdelt_discovery.py": ("GdeltDiscoveryProvider", {"discover"}),
        "common_crawl_discovery.py": ("CommonCrawlDiscoveryProvider", {"discover"}),
        "historical_discovery.py": ("HistoricalDiscoveryService", {"discover"}),
        "crawl_diagnostics.py": ("CrawlDiagnosticsBuilder", {"build"}),
        "source_profile.py": (
            "SourceProfileSeedBuilder",
            {"search_candidates", "archive_candidates", "feed_urls", "sitemap_urls"},
        ),
        "url_admission.py": ("SourceUrlAdmissionPolicy", {"filter"}),
        "candidate_ranker.py": ("CandidateRanker", {"rank"}),
        "crawl_orchestrator.py": ("CrawlOrchestrator", {"select_links", "crawl"}),
        "discovery_seeds.py": (
            "DiscoverySeedBuilder",
            {"search_candidates", "archive_candidates", "html_seeds", "feed_urls", "default_sitemap_urls"},
        ),
        "html_discovery.py": ("HtmlDiscoveryProvider", {"discover"}),
        "feed_discovery.py": ("FeedDiscoveryProvider", {"discover"}),
        "sitemap_discovery.py": (
            "SitemapDiscoveryProvider",
            {"robots_sitemaps", "prioritize_references", "discover"},
        ),
    }

    for filename, (class_name, methods) in expected.items():
        assert _class_methods(INFRASTRUCTURE_ROOT / filename, class_name) == methods


def test_legacy_crawl_engine_is_only_a_compatibility_facade() -> None:
    path = BACKEND_ROOT / "app" / "crawler" / "engine.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    crawl_engine = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "CrawlEngine"
    )
    methods = [
        node
        for node in crawl_engine.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    assert max(len(method.body) for method in methods) <= 12
    imports = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert "app.modules.crawling.infrastructure.crawl_orchestrator" in imports
    assert "app.modules.crawling.infrastructure.link_discovery" in imports
    assert "app.modules.crawling.infrastructure.page_fetcher" in imports
