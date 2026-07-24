import httpx
from typing import Optional

from app.config import settings

CRAWL4AI_URL = "http://crawl4ai:11235"


def _crawl4ai_headers() -> dict:
    """Return auth headers only when a token is configured."""
    if settings.crawl4ai_token:
        return {"Authorization": f"Bearer {settings.crawl4ai_token}"}
    return {}


async def crawl4ai_fetch_html(url: str) -> tuple[str, int]:
    """Fetch a URL's rendered raw HTML via crawl4ai's headless browser.

    crawl4ai egresses the shared browser service (residential-ish IP, real JS
    render), which clears Cloudflare/DataDome interstitials that a bare httpx GET
    from this container hits. Returns (html, status); html is "" on failure so
    callers can fall through to another strategy.
    """
    try:
        async with httpx.AsyncClient(timeout=70.0) as client:
            resp = await client.post(
                CRAWL4AI_URL + "/crawl",
                json={"urls": [url]},
                headers=_crawl4ai_headers(),
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
            if not results:
                return "", 0
            r = results[0]
            html = r.get("html") or r.get("cleaned_html") or r.get("raw_html") or ""
            return html, int(r.get("status_code") or 0)
    except Exception:
        return "", 0


async def crawl4ai_scrape(url: str) -> tuple[str, Optional[dict]]:
    """
    Scrape any URL using crawl4ai.
    Returns (markdown_text, metadata_dict).
    metadata: {title, description, word_count}
    """
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(
                CRAWL4AI_URL + "/crawl",
                json={"urls": [url], "markdown": True},
                headers=_crawl4ai_headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                return "[crawl4ai: no results]", {}
            r = results[0]
            raw_md = r.get("markdown", {})
            if isinstance(raw_md, dict):
                text = raw_md.get("raw_markdown", "") or raw_md.get("markdown_with_citations", "")
            else:
                text = str(raw_md) if raw_md else ""
            meta = {
                "title": r.get("title", ""),
                "description": r.get("description", ""),
                "word_count": r.get("word_count", 0),
                "url": r.get("url", url),
            }
            return text, meta
    except Exception as e:
        return f"[crawl4ai error: {e}]", {}
