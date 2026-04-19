"""News scraper routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.services.scraper.news import search_local_news, get_article_excerpt

router = APIRouter(prefix="/api/news", tags=["news"])


@router.get("/local/{location}")
async def get_local_news(location: str, count: int = 10):
    """Get local news articles for a location (titles + descriptions only)."""
    results = await search_local_news(location, count)
    return {"location": location, "articles": results, "count": len(results)}


@router.get("/excerpt")
async def get_excerpt(url: str):
    """Get a short excerpt from a specific article URL."""
    excerpt = await get_article_excerpt(url)
    if not excerpt:
        raise HTTPException(status_code=404, detail="Could not fetch article excerpt")
    return {"excerpt": excerpt}