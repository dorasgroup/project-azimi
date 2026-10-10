"""Bounded delay calculation for unlimited transient control retries."""

MAX_BACKOFF_SECONDS = 60
BASE_BACKOFF_SECONDS = 1


def calculate_retry_backoff(retries_done: int, max_backoff: int = MAX_BACKOFF_SECONDS) -> int:
    return min(max_backoff, (2 ** min(max(0, retries_done), 10)) + BASE_BACKOFF_SECONDS)
