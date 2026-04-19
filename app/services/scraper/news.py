"""
Local news archive scraper.
Searches for news articles in a given city/region, extracts title + excerpt only.
Does NOT store full article text.
"""

from __future__ import annotations

import os
from typing import Optional
import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape


NEWS_SEARCH_URL = "https://api.bing.microsoft.com/v7.0/news/search"


async def search_local_news(
    location: str,
    count: int = 10,
) -> list[dict]:
    """
    Search Bing News for local news in a location.
    Returns list of {title, url, description, date} dicts.
    Does NOT fetch full article content.
    """
    api_key = os.getenv("BING_NEWS_API_KEY", "")
    if not api_key:
        return []

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            params = {
                "q": f"local news {location}",
                "count": count,
                "freshness": "Month",
                "mkt": "en-US",
            }
            resp = await client.get(
                NEWS_SEARCH_URL,
                params=params,
                headers={"Ocp-Apim-Subscription-Key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            articles = []
            for item in data.get("value", []):
                articles.append({
                    "title": item.get("name", ""),
                    "url": item.get("url", ""),
                    "description": item.get("description", ""),
                    "date": item.get("datePublished", ""),
                })
            return articles
    except Exception:
        return []


async def get_article_excerpt(url: str) -> str:
    """
    Fetch article and return first paragraph (excerpt).
    Returns empty string on failure.
    Used only when user wants a deeper look at a specific article.
    """
    text, meta = await crawl4ai_scrape(url)
    if not text or text.startswith("["):
        return ""
    # Return first paragraph only
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    first = paragraphs[0] if paragraphs else ""
    # Truncate to 300 chars for excerpt
    if len(first) > 300:
        first = first[:297] + "..."
    return first