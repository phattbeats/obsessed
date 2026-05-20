"""
Generic placeholder SOS adapter for states without a real implementation.

Discovers the state's SOS Business Search URL via Google, then crawl4ai's
its landing page and greps for the search term. This is intentional
scaffolding for states whose APIs haven't been reversed yet (per
[PHA-231](/PHA/issues/PHA-231)-[PHA-234](/PHA/issues/PHA-234)) — when a real
adapter for a state lands, register it in `sos/__init__.py` and it'll
shadow this fallback for that state.
"""

from __future__ import annotations

import re
from typing import Optional

import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape
from app.services.scraper.flaresolverr import CloudflareWallError, fs_get
from app.services.scraper.sos.base import CanonicalRow


SOS_FALLBACK_URLS = {
    "ohio": "https://businesssearch.ohiosos.gov",
    "kentucky": "https://app.sos.ky.gov/ftsearch",
    "indiana": "https://bsd.sos.in.gov/PublicBusinessSearch",
    "west_virginia": "https://apps.wvto.gov/OpenGov/HCDRSearch.php",
}


async def find_sos_url(state: str) -> str | None:
    """Web-search the SOS business entity search URL for a state."""
    query = f"{state} Secretary of State business entity search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": query, "num": 5},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            urls = re.findall(r'https?://[^\s<>\'"]+', resp.text)
            for url in urls:
                url_lower = url.lower()
                if any(k in url_lower for k in ["sos", "business", "entity", "search", "ftsearch"]):
                    if "google" not in url_lower and "search?" not in url_lower:
                        return url.split("&")[0].split("?")[0]
            return None
    except Exception:
        return None


def _parse_entity_line(raw: str) -> dict:
    """Best-effort parse of an HTML/text line containing an entity hit."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    entry = {
        "entity_name": "",
        "entity_id": "",
        "jurisdiction": "",
        "status": "Active",
        "formation_date": "",
        "source_url": "",
    }
    for line in lines:
        if not entry["entity_name"] and len(line) > 2:
            entry["entity_name"] = line
        elif not entry["entity_id"] and any(k in line.lower() for k in ["id:", "number:", "#"]):
            entry["entity_id"] = line
        elif any(k in line.lower() for k in ["active", "inactive", "dissolved", "good standing"]):
            entry["status"] = line
    return entry


async def _text_search(
    state: str,
    needle: str,
    use_flaresolverr: bool,
    extra: Optional[dict] = None,
) -> list[CanonicalRow]:
    sos_url = await find_sos_url(state) or SOS_FALLBACK_URLS.get(state.lower(), "")
    if not sos_url:
        return []

    try:
        text, _ = await crawl4ai_scrape(sos_url)
        haystack: str | None = text if text and not text.startswith("[") else None
        if haystack is None and use_flaresolverr:
            try:
                html, status = await fs_get(sos_url)
                if html and isinstance(status, int) and status < 400:
                    haystack = html
            except CloudflareWallError:
                haystack = None
        if not haystack:
            return []

        out: list[CanonicalRow] = []
        for line in haystack.split("\n"):
            if needle.lower() in line.lower():
                entry = _parse_entity_line(line)
                entry["jurisdiction"] = state
                entry["source_url"] = sos_url
                if extra:
                    entry.update(extra)
                out.append(entry)  # type: ignore[arg-type]
        return out[:20]
    except Exception:
        return []


class FallbackAdapter:
    """Generic placeholder used for any state without a real adapter."""

    state_keys: tuple[str, ...] = ()  # registered as default in dispatcher

    def __init__(self, *, use_flaresolverr: bool = False) -> None:
        self._use_flaresolverr = use_flaresolverr

    async def search_entities(
        self,
        entity_name: str,
        *,
        status: str = "X",  # noqa: ARG002 — placeholder accepts but ignores
        limit: int = 100,  # noqa: ARG002 — fallback caps at 20 internally
        state: str = "",
    ) -> list[CanonicalRow]:
        if not entity_name or not entity_name.strip():
            return []
        return await _text_search(state, entity_name, self._use_flaresolverr)

    async def search_by_owner(
        self,
        owner_name: str,
        *,
        limit: int = 100,  # noqa: ARG002
        state: str = "",
    ) -> list[CanonicalRow]:
        if not owner_name or not owner_name.strip():
            return []
        return await _text_search(
            state, owner_name, self._use_flaresolverr, extra={"owner": owner_name}
        )
