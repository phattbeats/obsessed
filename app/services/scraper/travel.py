"""
Travel / tourism source scraper for PLACES entity type.
Uses crawl4ai to scrape any travel URL (TripAdvisor, travel blogs, etc.)
Also provides TripAdvisor-specific extraction helpers.
Graceful degradation: if the travel URL fails, the caller should fall back
to Wikipedia summary (the caller handles this via _travel_fallback in places.py).
"""
import httpx
import re

TRIPADVISOR_SEARCH = "https://www.tripadvisor.com/data/管控/graphql"
TA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ObsessedBot/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.tripadvisor.com/",
}


async def search_tripadvisor(query: str, max_results: int = 5) -> list[dict]:
    """
    Search TripAdvisor for a place.
    Note: TripAdvisor GraphQL API is not public — scrape the public search
    results page via crawl4ai for structured data. This stub returns empty
    list since actual scraping happens via scrape_tripadvisor_url with
    a direct TripAdvisor URL.
    """
    return []


async def scrape_tripadvisor_url(url: str) -> tuple[str, dict]:
    """
    Scrape a TripAdvisor place URL using crawl4ai.
    Extracts: name, rating, review count, address, description, categories.
    """
    from app.services.scraper.crawl4ai import crawl4ai_scrape

    text, meta = await crawl4ai_scrape(url)
    if not text or len(text) < 50:
        return f"[TripAdvisor: scrape failed for {url}]", {}

    text = re.sub(r"\[\d+\]", "", text)
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 15 and not l.startswith("TripAdvisor Review:")]
    return "\n".join(lines[:50]), meta


async def scrape_travel_blog(url: str) -> tuple[str, dict]:
    """
    Generic travel blog scraper using crawl4ai.
    Works for: lonelyplanet.com, timeout.com, AtlasObscura, travel blogs.
    Returns (raw_text, metadata_dict).
    On failure, returns an error message string (caller handles fallback).
    """
    from app.services.scraper.crawl4ai import crawl4ai_scrape

    try:
        text, meta = await crawl4ai_scrape(url)
        if not text or len(text) < 50:
            return f"[Travel blog: scrape failed for {url}]", {}
        text = re.sub(r"\[\d+]\.", "\n", text)
        text = re.sub(r"\s+", " ", text).strip()
        lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 30]
        return "\n".join(lines[:50]), meta
    except Exception as e:
        return f"[Travel blog: error for {url} — {e}]", {}


async def scrape_generic_place(url: str) -> tuple[str, dict]:
    """
    Generic place scraper — tries crawl4ai on any URL.
    Returns (markdown_text, metadata_dict).
    """
    from app.services.scraper.crawl4ai import crawl4ai_scrape

    try:
        text, meta = await crawl4ai_scrape(url)
        if not text:
            return f"[Places scrape: no content from {url}]", {}
        lines = text.split("\n")
        return "\n".join(lines[:100]), meta
    except Exception as e:
        return f"[Places scrape: error for {url} — {e}]", {}