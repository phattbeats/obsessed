"""
Travel / tourism source scraper for PLACES entity type.
Uses crawl4ai to scrape any travel URL (TripAdvisor, travel blogs, etc.)
Also provides TripAdvisor-specific extraction helpers.
"""
import httpx
import re

# TripAdvisor API endpoints (limited, no auth)
TRIPADVISOR_SEARCH = "https://www.tripadvisor.com/data/管控/graphql"
TA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ObsessedBot/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.tripadvisor.com/",
}


async def search_tripadvisor(query: str, max_results: int = 5) -> list[dict]:
    """
    Search TripAdvisor for a place.
    Note: TripAdvisor's GraphQL API is not public. We scrape the public
    search results page via crawl4ai instead for structured data.
    Returns empty list — caller should use scrape_tripadvisor_url with
    the actual TripAdvisor search page URL.
    This stub exists as a placeholder since TripAdvisor blocks simple HTTP.
    """
    return []


async def scrape_tripadvisor_url(url: str) -> tuple[str, dict]:
    """
    Scrape a TripAdvisor place URL using crawl4ai.
    Extracts: name, rating, review count, address, description, categories.
    url example: https://www.tripadvisor.com/Attraction_Review-g...
    """
    from app.services.scraper.crawl4ai import crawl4ai_scrape

    text, meta = await crawl4ai_scrape(url)
    if not text or len(text) < 50:
        return f"[TripAdvisor: scrape failed for {url}]", {}

    # Clean up Wikipedia-style citation markers [1]
    text = re.sub(r"\[\d+\]", "", text)
    # Strip review noise (very long review blocks don't help trivia)
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        if len(line) > 15 and not line.startswith("TripAdvisor Review:"):
            cleaned.append(line)

    return "\n".join(cleaned[:50]), meta


async def scrape_travel_blog(url: str) -> tuple[str, dict]:
    """
    Generic travel blog scraper using crawl4ai.
    Works for: lonelyplanet.com, timeout.com, AtlasObscura, travel blogs.
    """
    from app.services.scraper.crawl4ai import crawl4ai_scrape

    text, meta = await crawl4ai_scrape(url)
    if not text or len(text) < 50:
        return f"[Travel blog: scrape failed for {url}]", {}

    # Strip HTML artifacts and normalize whitespace
    text = re.sub(r"\[\d+]\.", "\n", text)  # numbered lists → newlines
    text = re.sub(r"\s+", " ", text).strip()
    lines = [l.strip() for l in text.split("\n") if len(l.strip()) > 30]

    return "\n".join(lines[:50]), meta


async def scrape_generic_place(url: str) -> tuple[str, dict]:
    """
    Generic place scraper — tries crawl4ai on any URL.
    Returns (markdown_text, metadata).
    """
    from app.services.scraper.crawl4ai import crawl4ai_scrape

    text, meta = await crawl4ai_scrape(url)
    if not text:
        return f"[Places scrape: no content from {url}]", {}
    # Truncate very long content
    lines = text.split("\n")
    return "\n".join(lines[:100]), meta
