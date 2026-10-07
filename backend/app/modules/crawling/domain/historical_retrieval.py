"""Bounded query partitioning for external historical indexes."""

from dataclasses import dataclass
from datetime import date, timedelta
from math import ceil


@dataclass(frozen=True)
class HistoricalRetrievalBudget:
    """Hard limits that improve date coverage without unbounded API traffic."""

    gdelt_target_window_days: int = 14
    gdelt_max_windows: int = 8
    common_crawl_max_pages_per_index: int = 3
    common_crawl_page_size: int = 1


DEFAULT_HISTORICAL_RETRIEVAL_BUDGET = HistoricalRetrievalBudget()


def balanced_date_windows(
    date_from: date,
    date_to: date,
    *,
    budget: HistoricalRetrievalBudget = DEFAULT_HISTORICAL_RETRIEVAL_BUDGET,
) -> list[tuple[date, date]]:
    """Cover the full interval with bounded, newest-first, balanced windows."""
    if date_to < date_from:
        return []
    total_days = (date_to - date_from).days + 1
    window_count = min(
        budget.gdelt_max_windows,
        max(1, ceil(total_days / budget.gdelt_target_window_days)),
    )
    base_size, remainder = divmod(total_days, window_count)
    windows: list[tuple[date, date]] = []
    cursor = date_from
    for index in range(window_count):
        size = base_size + (1 if index < remainder else 0)
        end = cursor + timedelta(days=size - 1)
        windows.append((cursor, end))
        cursor = end + timedelta(days=1)
    return list(reversed(windows))


def evenly_spaced_pages(
    total_pages: int,
    *,
    budget: HistoricalRetrievalBudget = DEFAULT_HISTORICAL_RETRIEVAL_BUDGET,
) -> list[int]:
    """Sample a bounded set across the complete CDX page range."""
    if total_pages <= 0:
        return [0]
    limit = min(total_pages, budget.common_crawl_max_pages_per_index)
    if limit == 1:
        return [0]
    return sorted(
        {
            round(index * (total_pages - 1) / (limit - 1))
            for index in range(limit)
        }
    )

