"""
Semaphore-based rate limiter for controlling concurrent requests.
Enforces per-source concurrency limits and respects Retry-After headers.
"""
import asyncio
import httpx
import random
from typing import Any, Callable, Awaitable


class RateLimiter:
    """
    Async context manager for rate-limited operations.
    
    Usage:
        limiter = RateLimiter(max_concurrent=3, min_interval=1.1)
        async with limiter:
            await do_request()
    """

    def __init__(self, max_concurrent: int = 1, min_interval: float = 0.0):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._min_interval = min_interval
        self._last_release = 0.0

    async def __aenter__(self) -> None:
        await self._sem.acquire()
        now = asyncio.get_event_loop().time()
        if self._min_interval > 0 and self._last_release > 0:
            elapsed = now - self._last_release
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
        return None

    async def __aexit__(self, *_: Any) -> None:
        self._last_release = asyncio.get_event_loop().time()
        self._sem.release()


async def retry_with_backoff(
    fn: Callable[..., Awaitable[httpx.Response]],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    respect_retry_after: bool = True,
) -> httpx.Response:
    """
    Call an async HTTP function with exponential backoff + jitter.
    If the response has a Retry-After header, respect it (Capped at max_delay).
    Returns the response on success. Raises after all retries exhausted.
    """
    for attempt in range(max_retries):
        try:
            resp = await fn()
            # Check for rate-limit status codes
            if resp.status_code in (429, 503) and respect_retry_after:
                retry_after = resp.headers.get("Retry-After", "")
                delay = _parse_retry_after(retry_after)
                if delay is None:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                delay = min(delay, max_delay)
                jitter = random.uniform(0, delay * 0.1)
                await asyncio.sleep(delay + jitter)
                continue
            return resp
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (429, 503) and respect_retry_after:
                retry_after = e.response.headers.get("Retry-After", "")
                delay = _parse_retry_after(retry_after)
                if delay is None:
                    delay = min(base_delay * (2 ** attempt), max_delay)
                delay = min(delay, max_delay)
                jitter = random.uniform(0, delay * 0.1)
                await asyncio.sleep(delay + jitter)
                continue
            if attempt == max_retries - 1:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)
    raise RuntimeError(f"retry_with_backoff: all {max_retries} retries exhausted")


def _parse_retry_after(value: str) -> float | None:
    """Parse Retry-After header value (seconds or HTTP date)."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


# Pre-configured limiters for external APIs
NOMINATIM_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.1)  # 1 req/s max — Nominatim ToS
REDDIT_LIMITER = RateLimiter(max_concurrent=1, min_interval=2.1)   # 2.1s between calls
WIKIDATA_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.1)  # 1 req/s
GDELT_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.1)     # 1 req/s
EVENTS_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.0)   # 1 req/s
GOOGLE_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.0)     # 1 req/s (Places API quota)
# Steam (api.steampowered.com — keyed calls)
STEAM_API_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.0)
# Steam Store (store.steampowered.com — appdetails, no auth)
STEAM_STORE_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.5)
# Steam Community (steamcommunity.com — XML profile/summaries)
STEAM_COMMUNITY_LIMITER = RateLimiter(max_concurrent=1, min_interval=1.5)

generic_limiter = RateLimiter(max_concurrent=3, min_interval=0.5)   # shared fallback
