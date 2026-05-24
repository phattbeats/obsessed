"""
Franklin County Probate Court — General Case Search scraper.

Endpoint: https://probatesearch.franklincountyohio.gov/netdata/PBCNameInx.ndm/input
Search type: Case Name (Last, First)

No login, no CAPTCHA, no VIEWSTATE — plain GET with `string` param.
Results are paginated; each page holds up to ~40 rows.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import TypedDict

import httpx

_BASE = "https://probatesearch.franklincountyohio.gov/netdata/PBCNameInx.ndm/input"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://probate.franklincountyohio.gov/Record-Search/General-Case-Search",
}
_MAX_PAGES = 5  # cap at 5 pages (~200 results) per call


class ProbateCaseRow(TypedDict):
    case_no: str
    case_name: str
    case_type: str
    subtype: str
    status: str
    filed_date: str
    closed_date: str
    detail_url: str
    source_url: str


def _parse_rows(html: str, source_url: str) -> list[ProbateCaseRow]:
    """Extract case rows from one page of results HTML."""
    rows: list[ProbateCaseRow] = []
    # Data rows alternate bgcolor=lightblue (unquoted) / bgcolor="white" (quoted, lowercase)
    # The real HTML uses inconsistent quoting, so match both forms case-insensitively.
    pattern = re.compile(
        r'<tr\s+bgcolor=["\']?(?:lightblue|white)["\']?>(.*?)</tr>',
        re.DOTALL | re.IGNORECASE,
    )
    cell_pattern = re.compile(r'<td[^>]*>(.*?)</td>', re.DOTALL)
    href_pattern = re.compile(r'href="([^"]+)"', re.IGNORECASE)

    for match in pattern.finditer(html):
        row_html = match.group(1)
        cells = cell_pattern.findall(row_html)
        clean = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]

        if len(clean) < 5:
            continue

        href_match = href_pattern.search(row_html)
        detail_url = href_match.group(1) if href_match else ""
        # Normalise relative to absolute
        if detail_url.startswith("http://probatesearch"):
            detail_url = "https://" + detail_url[7:]

        rows.append(
            ProbateCaseRow(
                case_no=clean[0],
                case_name=clean[1] if len(clean) > 1 else "",
                case_type=clean[2] if len(clean) > 2 else "",
                subtype=clean[3] if len(clean) > 3 else "",
                status=clean[4] if len(clean) > 4 else "",
                filed_date=clean[5] if len(clean) > 5 else "",
                closed_date=clean[6] if len(clean) > 6 else "",
                detail_url=detail_url,
                source_url=source_url,
            )
        )
    return rows


def _next_page_param(html: str) -> str | None:
    """Return the `stringf=...` value for the next-page link, or None."""
    m = re.search(
        r'href="input\?([^"]*stringf=[^"]+)"',
        html,
        re.IGNORECASE,
    )
    if not m:
        return None
    # Parse the query string from the href
    qs = m.group(1)
    params = dict(urllib.parse.parse_qsl(qs, keep_blank_values=True))
    return params.get("stringf")


async def search_probate_by_name(
    last_name: str,
    first_name: str = "",
    *,
    max_pages: int = _MAX_PAGES,
) -> list[ProbateCaseRow]:
    """
    Search Franklin County Probate by case name (last [, first]).

    Returns a flat list of ProbateCaseRow dicts across all paginated pages
    up to max_pages. Empty list on any error or no results.
    """
    last_name = last_name.strip()
    first_name = first_name.strip()
    if not last_name:
        return []

    search_string = last_name.upper()
    if first_name:
        search_string += ", " + first_name.upper()

    results: list[ProbateCaseRow] = []
    async with httpx.AsyncClient(
        timeout=20.0,
        follow_redirects=True,
        headers=_HEADERS,
    ) as client:
        # The server's alphabetical index breaks when the comma in "Last, First"
        # is percent-encoded (%2C). Pass it as a literal comma via safe=','.
        encoded = urllib.parse.quote(search_string, safe=",")
        request_url = f"{_BASE}?string={encoded}"
        pages_fetched = 0

        while pages_fetched < max_pages:
            try:
                resp = await client.get(request_url)
                resp.raise_for_status()
            except httpx.HTTPError:
                break

            html = resp.text
            page_rows = _parse_rows(html, str(resp.url))
            results.extend(page_rows)
            pages_fetched += 1

            if not page_rows:
                break

            # Follow pagination only if we got a full page
            next_val = _next_page_param(html)
            if not next_val:
                break
            # Next page uses stringf= (forward); comma must stay literal here too
            enc_next = urllib.parse.quote(next_val, safe=",*!=")
            request_url = f"{_BASE}?stringf={enc_next}"

    return results
