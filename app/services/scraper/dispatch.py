"""
Columbus Dispatch obituary scraper — DataDome solve path (PHA-820).

dispatch.com/obituaries is a Gannett cobrand shell that embeds Legacy.com: the
real, data-bearing obituary feed for The Columbus Dispatch lives on Legacy's
affiliate path

    https://www.legacy.com/obituaries/dispatch/

which renders the same JSON-LD ItemList that `legacy_com.parse_search_results`
already understands (verified live 2026-06-13 — current Columbus obituaries:
Neil Gant, Ronald Kish, Mark Monsarrat, ...). Detail pages are normal Legacy
/person|/obituaries pages, so `legacy_com.parse_obituary_detail` parses them too.

This module is therefore a thin Dispatch-flavored facade over `legacy_com`:
  - browse_recent()  -> the affiliate recent-obituaries feed (the closest thing
                        to a "dispatch obit API" — local, current notices).
  - search()         -> Legacy's name index scoped to Ohio (dispatch's market);
                        the affiliate has no standalone name-search route.
  - get_obituary()   -> a single obituary detail page.

DataDome guards these routes the same way it guards the rest of Legacy. All
fetches go through the shared `datadome.fetch_with_solve`, which stays inert
(returns a typed wall) unless DATADOME_SOLVE_PROXY is configured. dispatch.com /
the affiliate path were observed returning 403 DataDome challenges during recon,
so the solve path is not theoretical here.
"""

from __future__ import annotations

from typing import Optional

from app.services.scraper import legacy_com


SOURCE = "dispatch.com"
# Legacy affiliate feed that backs dispatch.com/obituaries.
DISPATCH_AFFILIATE_URL = "https://www.legacy.com/obituaries/dispatch/"
# Dispatch is a Columbus, OH paper — scope name search to its market.
DISPATCH_STATE = "Ohio"


async def browse_recent() -> dict:
    """Fetch the recent Columbus Dispatch obituary feed.

    Returns::

        {
          "url": str,
          "results": list[dict],   # normalized listing rows (source="dispatch.com")
          "wall": None | dict,
          "source": "dispatch.com",
        }
    """
    url = DISPATCH_AFFILIATE_URL
    html, wall = await legacy_com._resolve_html(url)

    results = legacy_com.parse_search_results(html) if html and wall is None else []
    for r in results:
        r["source"] = SOURCE
    return {"url": url, "results": results, "wall": wall, "source": SOURCE}


async def search(first: Optional[str] = None, last: Optional[str] = None) -> dict:
    """Search obituaries for the Dispatch market (Ohio) by name.

    Delegates to Legacy's name index scoped to Ohio and re-tags the source; the
    affiliate path itself exposes no standalone name-search route.
    """
    result = await legacy_com.search_obituaries(
        first, last, DISPATCH_STATE, source=SOURCE
    )
    return result


async def get_obituary(detail_url: str) -> dict:
    """Fetch and parse a single Dispatch/Legacy obituary detail page."""
    return await legacy_com.get_obituary(detail_url, source=SOURCE)
