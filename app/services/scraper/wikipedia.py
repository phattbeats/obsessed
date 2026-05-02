"""
Wikipedia scraper for PLACES entity type.
Primary source for place descriptions, history, and notable facts.
Uses the Wikipedia REST API (no auth, no rate limits on public data).

Falls back to Wikipedia HTML scrape (via crawl4ai) if the REST API fails.
Rate-limit aware: no hard sleeps needed on the public REST API.
"""
import httpx
import re

WIKIPEDIA_API = "https://en.wikipedia.org/api/rest_v1"


async def _scrape_via_rest(slug: str, place_name: str) -> tuple[str, dict]:
    """Primary: Wikipedia REST API (summary + mobile-sections)."""
    raw_parts = []
    meta = {}

    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1. Summary endpoint
        summary_url = f"{WIKIPEDIA_API}/page/summary/{slug}"
        r = await client.get(summary_url)
        if r.status_code == 200:
            d = r.json()
            meta["title"] = d.get("title", place_name)
            meta["description"] = d.get("description", "")
            meta["url"] = d.get("content_urls", {}).get("desktop", {}).get("page", "")
            extract = d.get("extract", "")
            if extract:
                raw_parts.append(f"[Wikipedia Summary: {meta['title']}]\n{extract}")

            geo = d.get("geo", {})
            if geo:
                meta["latitude"] = geo.get("latitude")
                meta["longitude"] = geo.get("longitude")
                raw_parts.append(f"[Coordinates] {geo.get('latitude')}, {geo.get('longitude')}")

        # 2. Full content via mobile-sections API
        page_url = f"{WIKIPEDIA_API}/page/mobile-sections/{slug}"
        r2 = await client.get(page_url)
        if r2.status_code == 200:
            d2 = r2.json()
            lead = d2.get("lead", {}).get("sections", [])
            for section in lead[:10]:
                text = section.get("text", "")
                if text:
                    clean = re.sub(r"<[^>]+>", "", text)
                    if len(clean) > 30:
                        raw_parts.append(clean)

            remaining = d2.get("remaining", {}).get("sections", [])
            for section in remaining[:30]:  # was 10, now 30 for richer content
                text = section.get("text", "")
                if text:
                    clean = re.sub(r"<[^>]+>", "", text)
                    if len(clean) > 30:
                        raw_parts.append(clean)

    return raw_parts, meta


async def _scrape_via_html(place_name: str) -> tuple[str, dict]:
    """Fallback: scrape Wikipedia page directly via crawl4ai."""
    from app.services.scraper.crawl4ai import crawl4ai_scrape
    url = f"https://en.wikipedia.org/wiki/{place_name.replace(' ', '_')}"
    text, meta = await crawl4ai_scrape(url)
    # crawl4ai returns (markdown_text, metadata_dict)
    title = (meta or {}).get("title", place_name) if isinstance(meta, dict) else place_name
    return text, {"title": title}


async def scrape_wikipedia(place_name: str) -> tuple[str, dict]:
    """
    Fetch Wikipedia summary + content for a place name.
    Tries REST API first, falls back to HTML scrape (crawl4ai) on failure.
    Returns (raw_text, metadata_dict).
    metadata: {title, description, latitude, longitude, url}
    """
    slug = place_name.replace(" ", "_")

    # Try REST API
    try:
        raw_parts, meta = await _scrape_via_rest(slug, place_name)
        if raw_parts:
            return "\n\n".join(raw_parts[:40]), meta  # was 20, now 40
    except Exception as e:
        pass  # fall through to HTML fallback

    # Fallback: HTML scrape via crawl4ai
    try:
        text, meta = await _scrape_via_html(place_name)
        if text and len(text) > 50:
            return text, meta
    except Exception:
        pass

    return f"[Wikipedia: no results for '{place_name}']", {}


async def search_wikipedia(query: str, max_results: int = 3) -> list[dict]:
    """
    Search Wikipedia for a place name — returns list of {title, description, pageid}.
    Useful when exact title doesn't match.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "opensearch",
                    "search": query,
                    "limit": max_results,
                    "format": "json",
                },
            )
            r.raise_for_status()
            data = r.json()
            results = []
            titles = data[1] if len(data) > 1 else []
            descs = data[2] if len(data) > 2 else []
            ids = data[3] if len(data) > 3 else []
            for i, title in enumerate(titles):
                results.append({
                    "title": title,
                    "description": descs[i] if i < len(descs) else "",
                    "pageid": re.search(r"/(\d+)", ids[i]).group(1) if ids[i] and "/" in ids[i] else "",
                })
            return results
    except Exception:
        return []
