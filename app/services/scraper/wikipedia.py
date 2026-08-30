"""
Wikipedia scraper for PLACES entity type.
Primary source for place descriptions, history, and notable facts.
Uses the Wikipedia REST API (no auth, no rate limits on public data).

Falls back to Wikipedia HTML scrape (via crawl4ai) if the REST API fails.
Rate-limit aware: no hard sleeps needed on the public REST API.
Cache: checks entity_cache before scraping. Cache miss scrapes and writes result.
"""
import httpx
import re
from app.services.entity_cache import get_cached, write_cached

WIKIPEDIA_API = "https://en.wikipedia.org/api/rest_v1"
WIKIPEDIA_ACTION_API = "https://en.wikipedia.org/w/api.php"

# Wikimedia enforces a User-Agent policy: generic library UAs (python-httpx/*,
# python-requests/*) get 403 on every endpoint. Without this header the REST
# calls below silently fail and every scrape falls through to the crawl4ai HTML
# path, which is where the page-chrome-as-facts bug comes from (PHA-1558).
# https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy
USER_AGENT = (
    "ObsessedBot/1.0 (https://github.com/phattbeats/obsessed) httpx"
)
_HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}

# Article sections that are citations/navigation rather than facts.
_SKIP_SECTIONS = {
    "references", "external links", "see also", "notes", "bibliography",
    "further reading", "sources", "citations", "footnotes", "gallery",
}


# Wikipedia page-chrome lines that crawl4ai's markdown extraction picks up
# alongside the article body. They are navigation, not facts, and break
# question generation (PHA-1558). Each pattern is intentionally narrow.
_NAV_LINE_PATTERNS = [
    re.compile(r"^\s*\*\s*\[.+?\]\([^)]+\)", re.IGNORECASE),  # markdown link list (sidebar nav)
    re.compile(r"move to sidebar hide", re.IGNORECASE),
    re.compile(
        r"\b(Visit the main page|Guides to browsing Wikipedia|Articles related to current events|"
        r"Visit a randomly selected article|Learn about Wikipedia and how it is edited|"
        r"Contact Wikipedia|How to contact Wikipedia|Support Wikipedia)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^\s*\[(?:Main page|Contents|Current events|Random article|About Wikipedia|Contact Wikipedia|Donate)\]\([^)]+\)", re.IGNORECASE),
    # Any link into a MediaWiki namespace or maintenance URL is chrome, never a
    # fact. Matching on the URL rather than on the visible label is what makes
    # this hold up against real crawl4ai output instead of only the lines that
    # happened to appear in the original bug report.
    re.compile(r"(?:wikipedia|wikimedia|wikidata)\.org/wiki/(?:Special|Help|Portal|Wikipedia|Talk|File|Category|Template):", re.IGNORECASE),
    re.compile(r"wikipedia\.org/w/index\.php", re.IGNORECASE),
    re.compile(r"wikipedia\.org/static/images/", re.IGNORECASE),
    re.compile(r"\b(?:action=edit|action=history|oldid=|printable=yes)\b", re.IGNORECASE),
    re.compile(r"^\s*\[?\s*(?:Search|Edit links|View source|View history|Read|Edit|Log in|Create account|Contributions|Download as PDF|Printable version|Toggle limited content width)\s*\]?\s*$", re.IGNORECASE),
    re.compile(r"^\s*Toggle (?:the table of contents|[\w\s'’-]+ subsection)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:Jump to content|From Wikipedia, the free encyclopedia|This page always uses small font size|Appearance|Tools|Actions|In other projects|Personal tools|Namespaces|Views|Navigation|Contribute|Print/export)\s*$", re.IGNORECASE),
]

# A markdown link whose title attribute contains a newline wraps across lines,
# e.g. `* [View source](https://…&action=edit "This page is protected.` /
# `You can view its source \[e\]")`. Line-at-a-time filtering sees a fragment
# with no closing paren, keeps both halves, and they surface as "facts".
_UNCLOSED_LINK = re.compile(r"\]\([^)]*$")


def _join_wrapped_links(lines: list[str]) -> list[str]:
    """Rejoin markdown links whose title attribute wrapped across lines."""
    out: list[str] = []
    pending: str | None = None
    for line in lines:
        current = line if pending is None else f"{pending} {line.strip()}"
        if _UNCLOSED_LINK.search(current) and len(current) < 4000:
            pending = current
        else:
            out.append(current)
            pending = None
    if pending is not None:
        out.append(pending)
    return out


def _is_wikipedia_nav_line(line: str) -> bool:
    """Return True if the line is Wikipedia page chrome (sidebar/footer nav)."""
    text = line.strip()
    if not text:
        return True
    return any(p.search(text) for p in _NAV_LINE_PATTERNS)


def _clean_wikipedia_output(text: str) -> str:
    """Strip Wikipedia sidebar/nav boilerplate from scraped markdown.

    crawl4ai returns the full page markdown including the left-hand sidebar
    navigation list (`* [Main page](...)`, `* [Contents](...)`, etc.), the
    header/search/tools chrome, and the hidden "move to sidebar hide" element.
    Article body text is preserved; only chrome lines are removed.
    """
    if not text:
        return ""
    lines = _join_wrapped_links(text.split("\n"))
    kept = [line for line in lines if not _is_wikipedia_nav_line(line)]
    return "\n".join(kept)


async def _scrape_via_rest(slug: str, place_name: str) -> tuple[str, dict]:
    """Primary: Wikipedia REST API (summary + mobile-sections)."""
    raw_parts = []
    meta = {}

    async with httpx.AsyncClient(timeout=20.0, headers=_HEADERS) as client:
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

        # 2. Full article body via the Action API's plain-text extract.
        #    The old REST `page/mobile-sections/{slug}` endpoint was retired by
        #    Wikimedia and now returns 403 for every title, even with a
        #    compliant User-Agent — it produced zero article body, which is what
        #    starved question generation and forced the chrome-laden HTML
        #    fallback. `prop=extracts&explaintext=1` returns the article as
        #    plain text with `== Section ==` headers and no page chrome at all.
        r2 = await client.get(
            WIKIPEDIA_ACTION_API,
            params={
                "action": "query",
                "prop": "extracts",
                "explaintext": "1",
                "redirects": "1",
                "format": "json",
                "titles": slug.replace("_", " "),
            },
        )
        if r2.status_code == 200:
            pages = (r2.json().get("query") or {}).get("pages") or {}
            for page in pages.values():
                extract = page.get("extract") or ""
                if extract:
                    raw_parts.extend(_split_extract_sections(extract))

    return raw_parts, meta


def _split_extract_sections(extract: str) -> list[str]:
    """Split an Action API plain-text extract into fact-bearing sections.

    The extract uses `== Heading ==` / `=== Subheading ===` markers. Reference
    and navigation sections carry no facts, so they are dropped.
    """
    sections: list[str] = []
    heading = ""
    buf: list[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if body and len(body) > 30 and heading.strip().lower() not in _SKIP_SECTIONS:
            sections.append(f"[{heading}]\n{body}" if heading else body)

    for line in extract.split("\n"):
        m = re.match(r"^\s*(={2,6})\s*(.+?)\s*\1\s*$", line)
        if m:
            flush()
            heading = m.group(2)
            buf = []
            continue
        buf.append(line)
    flush()
    return sections


async def _scrape_via_html(place_name: str) -> tuple[str, dict]:
    """Fallback: scrape Wikipedia page directly via crawl4ai."""
    from app.services.scraper.crawl4ai import crawl4ai_scrape
    url = f"https://en.wikipedia.org/wiki/{place_name.replace(' ', '_')}"
    text, meta = await crawl4ai_scrape(url)
    text = _clean_wikipedia_output(text or "")
    title = (meta or {}).get("title", place_name) if isinstance(meta, dict) else place_name
    return text, {"title": title}


async def scrape_wikipedia(place_name: str, entity_type: str = "place") -> tuple[str, dict]:
    """
    Fetch Wikipedia summary + content for a place name.
    Cache check first. On miss: REST API → HTML fallback → write to cache.
    Returns (raw_text, metadata_dict).
    metadata: {title, description, latitude, longitude, url}
    """
    # Cache check
    cached = get_cached(place_name, entity_type)
    if cached:
        return cached[0], {"cached": True}

    slug = place_name.replace(" ", "_")

    # REST API — primary
    try:
        raw_parts, meta = await _scrape_via_rest(slug, place_name)
        if raw_parts:
            result = "\n\n".join(raw_parts[:40])
            result = _clean_wikipedia_output(result)
            write_cached(place_name, entity_type, result, meta.get("url", ""))
            return result, meta
    except Exception:
        pass

    # HTML fallback
    try:
        text, meta = await _scrape_via_html(place_name)
        if text and len(text) > 50:
            text = _clean_wikipedia_output(text)
            write_cached(place_name, entity_type, text, meta.get("title", ""))
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
        async with httpx.AsyncClient(timeout=15.0, headers=_HEADERS) as client:
            r = await client.get(
                WIKIPEDIA_ACTION_API,
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
