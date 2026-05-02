"""
Rate-limit-aware async HTTP utility with fallback-on-failure support.
Each scraper uses this instead of raw httpx calls to get automatic retries,
exponential backoff, and graceful source degradation.
"""
import asyncio
import httpx
from typing import Callable, Awaitable


async def scrape_with_fallback(
    *,
    sources: list[tuple[str, Callable]],  # [(label, awaitable_fn(url)), ...]
    timeout: float = 20.0,
    max_retries: int = 2,
) -> tuple[str, dict]:
    """
    Try each source in order until one returns non-empty content.
    Returns (content, metadata) from the first successful source.

    Each source fn must: await httpx_async_call() → (text_content, metadata_dict)
    If all fail, returns ("[All sources failed]", {}).

    Graceful degradation: a single source failure logs the error and moves on.
    """
    import logging
    logger = logging.getLogger(__name__)

    for label, fn in sources:
        for attempt in range(max_retries):
            try:
                text, meta = await fn()
                if text and len(text) > 50:
                    return text, meta
                # Empty response — try next source
                logger.debug(f"[fallback] {label} returned empty on attempt {attempt+1}")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    # Not found — expected for some sources, skip immediately
                    logger.debug(f"[fallback] {label} → 404, skipping")
                    break
                logger.debug(f"[fallback] {label} HTTP error {e.response.status_code} on attempt {attempt+1}")
                await asyncio.sleep(2 ** attempt)  # simple backoff
            except Exception as e:
                logger.debug(f"[fallback] {label} error on attempt {attempt+1}: {e}")
                await asyncio.sleep(2 ** attempt)

    return "[All sources failed]", {}


def make_wikipedia_html_fallback(url: str) -> tuple[str, Callable]:
    """Returns (label, fn) for Wikipedia HTML scrape via crawl4ai."""
    async def fn():
        from app.services.scraper.crawl4ai import crawl4ai_scrape
        text, meta = await crawl4ai_scrape(url)
        return text, meta
    return "wikipedia_html", fn


async def retry_with_backoff(
    fn: Callable[[], Awaitable],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> tuple[str, dict]:
    """
    Call an async fn with exponential backoff and jitter.
    Returns (result_text, meta_dict). Raises on all retries exhausted.
    """
    import random
    for attempt in range(max_retries):
        try:
            return await fn()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = min(base_delay * (2 ** attempt), max_delay)
            jitter = random.uniform(0, delay * 0.1)
            await asyncio.sleep(delay + jitter)