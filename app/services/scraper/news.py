"""
Local news archive scraper via Google News RSS.
Searches for news articles by any query (person, company, topic, location).
Extracts title + description only. No API key required.
"""

from __future__ import annotations

import httpx
from typing import Optional
import xml.etree.ElementTree as ET
from datetime import datetime

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"


def _parse_rss_entry(item: ET.Element) -> dict:
    """Parse a single <item> from an RSS feed."""
    def get(tag: str) -> str:
        el = item.find(tag)
        return el.text.strip() if el is not None and el.text else ""
    return {
        "title": get("title"),
        "url": get("link"),
        "description": get("description"),
        "date": get("pubDate"),
    }


async def search_news(
    query: str,
    count: int = 10,
    *,
    location: Optional[str] = None,
) -> list[dict]:
    """
    Search Google News via RSS for any query.
    Returns list of {title, url, description, date} dicts.
    No API key required.

    Args:
        query: Free-form search term (person, company, topic, etc.)
        count: Max articles to return (default 10)
        location: Optional location hint appended to query (e.g. "Columbus OH")
    """
    q = f"{query} {location}" if location else query
    url = f"{GOOGLE_NEWS_RSS}?q={httpx.utils.encode_url_component(q)}&hl=en-US&gl=US&ceid=US:en"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
            channel = root.find("channel")
            if channel is None:
                return []
            items = channel.findall("item")
            articles = []
            for item in items[:count]:
                article = _parse_rss_entry(item)
                if article["title"] and article["url"]:
                    articles.append(article)
            return articles
    except Exception:
        return []


async def search_local_news(
    location: str,
    count: int = 10,
) -> list[dict]:
    """
    Search for local news in a location.
    Convenience wrapper: searches "local news {location}".
    """
    return await search_news(f"local news {location}", count=count)


async def get_article_excerpt(url: str) -> str:
    """
    Fetch article and return first paragraph (excerpt).
    Returns empty string on failure.
    Used only when user wants a deeper look at a specific article.
    """
    from app.services.scraper.crawl4ai import crawl4ai_scrape
    text, meta = await crawl4ai_scrape(url)
    if not text or text.startswith("["):
        return ""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    first = paragraphs[0] if paragraphs else ""
    if len(first) > 300:
        first = first[:297] + "..."
    return first
