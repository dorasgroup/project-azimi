"""Crawl4AI and network adapters."""

__all__ = ["Crawl4AiSourceCrawler", "SqlAlchemySourceRepository"]


def __getattr__(name: str):
    if name == "Crawl4AiSourceCrawler":
        from app.modules.crawling.infrastructure.crawl4ai_adapter import Crawl4AiSourceCrawler

        return Crawl4AiSourceCrawler
    if name == "SqlAlchemySourceRepository":
        from app.modules.crawling.infrastructure.source_repository import SqlAlchemySourceRepository

        return SqlAlchemySourceRepository
    raise AttributeError(name)
