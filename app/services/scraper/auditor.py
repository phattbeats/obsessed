"""
County Auditor / property-records scraper.

Dispatch order per request:
  1. STATE_PORTALS registry → dedicated scraper for known state portals
  2. Search-first discovery → Google search for county auditor URL
  3. COUNTY_FALLBACK_URLS → hardcoded map of major-population counties

Adding a state is config, not code: add an entry to STATE_PORTALS with a
portal_type and any portal-specific kwargs.  The scraper function referenced
by portal_type handles the actual HTTP calls.

Tennessee (portal_type="tpad"):
  Centralised at assessment.cot.tn.gov/TPAD.  86 of 95 counties are served
  via a POST JSON API; the remaining 9 link to county-specific external sites.
  This scraper uses the TPAD Search/GetSearchResults endpoint directly — no JS
  execution required.  Market value is not returned by the search API (it loads
  via a JS/Tyler token on the details page); the field is left empty and noted
  in the docstring.

Ohio (portal_type="oh_fallback"):
  Falls back to the hardcoded URL map plus web-search discovery.

Improvements (PHA-798):
  • Coverage: expanded COUNTY_FALLBACK_URLS to cover ~70% of OH population
    (Cuyahoga, Hamilton, Summit, Montgomery, Lucas, Stark, Butler, Warren,
    Clermont, Lorain, etc., plus the original central-Ohio counties).
  • Field extraction: parse_property_record now captures sale price, deed
    date, last sale date, year built, owner history chain, and school
    district — not just owner/address/parcel/acreage/market/taxable.
  • Robustness: bare excepts replaced with scrape_with_fallback for
    retry+backoff across crawl4ai → FlareSolverr.
  • Anti-bot: FlareSolverr is now the default fallback (crawl4ai first;
    FlareSolverr if crawl4ai returns no results or a Cloudflare challenge).
  • Markdown-table parsing: many auditor sites render records as
    `| Field | Value |` rows; the parser handles both `Field: value` and
    table layouts.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Awaitable, Callable

import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape
from app.services.scraper.flaresolverr import (
    CloudflareWallError,
    fs_get,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# County fallback URLs
# Coverage targets the top OH counties by population so the bulk of OH
# property records are reachable without a web search round-trip.
# ---------------------------------------------------------------------------

# Format: county_key -> (base_url, [path hints]).
# base_url is the auditor site root; path_hints are common property-search
# subpaths the generic parser will try in order.  If all hints 404, the
# base_url is scraped as a last resort.
COUNTY_FALLBACK_URLS: dict[str, tuple[str, list[str]]] = {
    # --- Central Ohio (original set, unchanged) ---
    "franklin": (
        "https://property.franklincountyauditor.org",
        ["/Property/Search/", "/Owner/Search/", "/"],
    ),
    "delaware": (
        "https://delawarecountyauditor.org",
        ["/property-search/", "/search/"],
    ),
    "licking": (
        "https://lickingcountyauditor.org",
        ["/property-search/", "/search/"],
    ),
    "fairfield": (
        "https://fairfieldcountyauditor.com",
        ["/property-search/", "/search/"],
    ),
    "union": (
        "https://unioncountyauditor.org",
        ["/property-search/", "/search/"],
    ),
    "madison": (
        "https://madisoncountyauditor.org",
        ["/property-search/", "/search/"],
    ),
    "pickaway": (
        "https://pickawaycountyauditor.com",
        ["/property-search/", "/"],
    ),
    "hocking": (
        "https://hockingcountyauditor.net",
        ["/property-search/", "/"],
    ),
    "athens": (
        "https://athenscountyauditor.com",
        ["/property-search/", "/"],
    ),
    "vinton": (
        "https://vintoncountyauditor.com",
        ["/property-search/", "/"],
    ),
    # --- NE Ohio / Cuyahoga / Cleveland metro ---
    "cuyahoga": (
        "https://auditor.cuyahogacounty.gov",
        ["/search/", "/property-search/", "/fiscal-officer/property-search/"],
    ),
    "summit": (
        "https://fiscaloffice.summitoh.net",
        ["/property-search/", "/search/"],
    ),
    "portage": (
        "https://www.portagecounty-auditor.org",
        ["/property-search/", "/"],
    ),
    "medina": (
        "https://www.medinacountyauditor.org",
        ["/property-search/", "/"],
    ),
    "geauga": (
        "https://auditor.geauga.oh.gov",
        ["/property-search/", "/"],
    ),
    "lake": (
        "https://www.lakecountyohio.gov/auditor",
        ["/property-search/", "/"],
    ),
    "lorain": (
        "https://www.loraincountyauditor.com",
        ["/property-search/", "/"],
    ),
    # --- SW Ohio / Cincinnati metro ---
    "hamilton": (
        "https://www.hamiltoncountyauditor.org",
        ["/property-search/", "/search/"],
    ),
    "butler": (
        "https://www.butlercountyauditor.org",
        ["/property-search/", "/"],
    ),
    "warren": (
        "https://www.warrencountyauditor.org",
        ["/property-search/", "/"],
    ),
    "clermont": (
        "https://www.clermontcountyohio.gov/auditor",
        ["/property-search/", "/"],
    ),
    # --- NW Ohio / Toledo metro ---
    "lucas": (
        "https://co.lucas.oh.us/Auditor",
        ["/property-search/", "/"],
    ),
    "wood": (
        "https://www.woodcountyauditor.org",
        ["/property-search/", "/"],
    ),
    # --- NE / Stark / Canton metro ---
    "stark": (
        "https://www.starkcountyohio.gov/auditor",
        ["/property-search/", "/"],
    ),
    # --- Central OH / Dayton metro ---
    "montgomery": (
        "https://www.mcohio.org/government/elected_officials/auditor/index.php",
        ["/property-search/", "/"],
    ),
    "greene": (
        "https://www.co.greene.oh.us/auditor",
        ["/property-search/", "/"],
    ),
    "miami": (
        "https://www.miamicountyohio.gov/auditor",
        ["/property-search/", "/"],
    ),
    "clark": (
        "https://www.clarkcountyohio.gov/auditor",
        ["/property-search/", "/"],
    ),
    # --- Other significant counties ---
    "trumbull": (
        "https://auditor.co.trumbull.oh.us",
        ["/property-search/", "/"],
    ),
    "mahoning": (
        "https://www.mahoningcountyoh.gov/auditor",
        ["/property-search/", "/"],
    ),
    "richland": (
        "https://www.richlandcountyauditor.org",
        ["/property-search/", "/"],
    ),
    "allen": (
        "https://www.allencountyauditor.org",
        ["/property-search/", "/"],
    ),
    "tuscarawas": (
        "https://www.co.tuscarawas.oh.us/auditor",
        ["/property-search/", "/"],
    ),
    "muskingum": (
        "https://www.muskingumcountyauditor.org",
        ["/property-search/", "/"],
    ),
}

# Flat alias kept for backwards-compat with existing imports.
OH_AUDITOR_URLS: dict[str, str] = {
    k: v[0] for k, v in COUNTY_FALLBACK_URLS.items()
}
AUDITOR_SEARCH_URLS = OH_AUDITOR_URLS  # legacy alias


# ---------------------------------------------------------------------------
# Tennessee — TPAD county codes
# assessment.cot.tn.gov/TPAD serves 86 of 95 TN counties.
# Counties marked "(external link)" on the TPAD site are NOT in this map.
# ---------------------------------------------------------------------------

TN_COUNTY_CODES: dict[str, str] = {
    "anderson": "001", "bedford": "002", "benton": "003", "bledsoe": "004",
    "blount": "005", "bradley": "006", "campbell": "007", "cannon": "008",
    "carroll": "009", "carter": "010", "cheatham": "011",
    "claiborne": "013", "clay": "014", "cocke": "015", "coffee": "016",
    "crockett": "017", "cumberland": "018",
    "decatur": "020", "dekalb": "021", "dickson": "022", "dyer": "023",
    "fayette": "024", "fentress": "025", "franklin": "026", "gibson": "027",
    "giles": "028", "grainger": "029", "greene": "030", "grundy": "031",
    "hamblen": "032", "hancock": "034", "hardeman": "035", "hardin": "036",
    "hawkins": "037", "haywood": "038", "henderson": "039", "henry": "040",
    "houston": "042", "humphreys": "043", "jackson": "044", "jefferson": "045",
    "johnson": "046",
    "lake": "048", "lauderdale": "049", "lawrence": "050", "lewis": "051",
    "lincoln": "052", "loudon": "053", "macon": "054", "madison": "055",
    "marion": "056", "marshall": "057", "maury": "058", "mcminn": "059",
    "mcnairy": "060", "meigs": "061", "monroe": "062",
    "moore": "064", "morgan": "065", "obion": "066", "overton": "067",
    "perry": "068", "pickett": "069", "polk": "070", "putnam": "071",
    "rhea": "072", "roane": "073", "robertson": "074",
    "scott": "076", "sequatchie": "077", "sevier": "078",
    "smith": "080", "stewart": "081", "sullivan": "082", "sumner": "083",
    "tipton": "084", "trousdale": "085", "unicoi": "086", "union": "087",
    "van buren": "088", "warren": "089", "washington": "090", "wayne": "091",
    "weakley": "092", "white": "093", "wilson": "095",
}

# Counties served by external (non-TPAD) sites — fall through to search.
TN_EXTERNAL_COUNTIES = {
    "chester", "davidson", "hamilton", "hickman", "knox",
    "montgomery", "rutherford", "shelby", "williamson",
}


# ---------------------------------------------------------------------------
# State → portal registry
# Each entry: {"portal_type": str, ...extra kwargs...}
# ---------------------------------------------------------------------------

STATE_PORTALS: dict[str, dict] = {
    "tennessee": {
        "portal_type": "tpad",
        "base_url": "https://assessment.cot.tn.gov/TPAD",
        "county_codes": TN_COUNTY_CODES,
        "external_counties": TN_EXTERNAL_COUNTIES,
    },
    # Ohio uses the fallback URL map + search discovery — no dedicated portal.
}


# ---------------------------------------------------------------------------
# Tennessee TPAD scraper
# ---------------------------------------------------------------------------

async def _search_tpad_tn(
    county: str,
    search_term: str,
    portal_cfg: dict,
) -> list[dict]:
    """
    Query the TN Comptroller TPAD API for property records.

    Source: POST https://assessment.cot.tn.gov/TPAD/Search/GetSearchResults
    Returns JSON array of matches; owner and parcel_id are always populated
    when a record is found.  Market value is NOT available from this endpoint
    (it loads via a Tyler Technologies JS token on the parcel details page)
    and is left as an empty string.

    Counties served by external sites (TN_EXTERNAL_COUNTIES) cannot be
    queried here and will return an empty list — callers should fall through
    to web-search discovery for those.
    """
    county_key = county.lower().replace(" county", "").strip()
    county_code = portal_cfg["county_codes"].get(county_key)

    if not county_code:
        if county_key in portal_cfg.get("external_counties", set()):
            return []  # external-site county — caller should fall back to search
        return []

    base_url = portal_cfg["base_url"]
    search_url = f"{base_url}/Search/GetSearchResults"

    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    search_url,
                    data={
                        "Jur": county_code,
                        "Query": search_term,
                        "SortBy": "PropertyAddress",
                        "ClearDatatable": "true",
                    },
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "X-Requested-With": "XMLHttpRequest",
                        "Referer": f"{base_url}/Search?Jur={county_code}&Query={search_term}",
                    },
                )
                if resp.status_code != 200:
                    return []
                raw = resp.json()
            break  # success — exit retry loop
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("TPAD attempt %d failed for %s/%s: %s", attempt + 1, county, search_term, exc)
            if attempt == 1:
                return []
            await asyncio.sleep(2 ** attempt)  # backoff: 1s, 2s
    else:
        return []

    records = []
    for item in raw:
        if not item or item.get("empty"):
            continue

        raw_address = item.get("propertyAddress", "").strip()
        owner = item.get("owner", "").strip()
        parcel_id = item.get("parcelId", "").strip()

        address = _normalise_tpad_address(raw_address)

        record = {
            "owner": owner,
            "address": address,
            "parcel_id": parcel_id,
            "acreage": "",
            "market_value": "",   # not available from search endpoint
            "taxable_value": "",
            "sale_price": "",     # not available from search endpoint
            "deed_date": "",      # not available from search endpoint
            "last_sale_date": "",
            "year_built": "",
            "owner_history": "",  # not available from search endpoint
            "school_district": "",
            "search_term": search_term,
            "county": county,
            "source_url": f"{base_url}/Parcel/Details?parcelId={parcel_id}&jur={county_code}",
            "source": "tpad",
            "state": "Tennessee",
            "property_class": item.get("class", ""),
            "county_name": item.get("countyName", ""),
            "tax_year": item.get("taxYear", ""),
            "subdivision": item.get("subdivisionName", ""),
            "gis_map": item.get("gisMap", ""),
        }
        records.append(record)

    return records


def _normalise_tpad_address(raw: str) -> str:
    """
    TPAD stores addresses as 'STREET NAME  NUMBER' (name before number).
    Convert to the conventional 'NUMBER STREET NAME' form when a trailing
    number is present.  Returns the raw string unchanged when the pattern
    doesn't match.
    """
    m = re.match(r'^(.+?)\s{2,}(\d+)\s*$', raw.strip())
    if m:
        return f"{m.group(2)} {m.group(1).strip()}"
    return raw.strip()


# ---------------------------------------------------------------------------
# Generic web-search URL discovery (all states)
# ---------------------------------------------------------------------------

async def find_auditor_url(county: str, state: str = "Ohio") -> str | None:
    """
    Web search to discover the actual county auditor property search URL.
    Returns URL or None.
    """
    query = f"{county} county {state} auditor property search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://www.google.com/search",
                params={"q": query, "num": 5},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            text = resp.text
            urls = re.findall(r'https?://[^\s<>\']+', text)
            for url in urls:
                url_lower = url.lower()
                if any(k in url_lower for k in ['auditor', 'property', 'search', 'parcel']):
                    if 'google' not in url_lower and 'search?' not in url_lower:
                        return url.split('&')[0].split('?')[0]
            return None
    except Exception as exc:
        logger.debug("find_auditor_url failed for %s/%s: %s", county, state, exc)
        return None


# ---------------------------------------------------------------------------
# Record parser (generic — used for crawl4ai / FlareSolverr paths)
# ---------------------------------------------------------------------------

# Field-name aliases for the new fields introduced in PHA-798.
_FIELD_ALIASES: dict[str, list[str]] = {
    "owner": ["owner", "owner name", "taxpayer", "taxpayer name"],
    "address": ["address", "property address", "situs address", "situs"],
    "parcel_id": ["parcel", "parcel id", "parcel number", "parcel #", "pin"],
    "acreage": ["acre", "acres", "acreage", "lot size"],
    "market_value": ["market value", "market", "total market value", "assessed market"],
    "taxable_value": ["taxable", "taxable value", "assessed value", "tax value"],
    "sale_price": ["sale price", "sold price", "sold for", "purchase price", "sale amount"],
    "deed_date": ["deed date", "deed recorded", "deed"],
    "last_sale_date": ["last sale", "last sold", "sale date", "sale recorded"],
    "year_built": ["year built", "built", "year constructed", "construction year"],
    "owner_history": ["owner history", "previous owner", "ownership history", "transfer history"],
    "school_district": ["school district", "school", "district"],
}


def _normalise_key(line: str) -> str:
    """Drop trailing colon and whitespace, lowercase — for matching field names."""
    return re.sub(r"[:\s]+", " ", line).strip().lower()


def _match_field(line: str) -> str | None:
    """
    Identify which canonical field (if any) a line represents.
    Returns the canonical key or None.

    Handles:
      • "Owner: John"
      • "| Owner | John |"  (markdown table)
      • "Owner        John"  (whitespace-separated)
      • "Owner History: ..."  (multi-word alias wins over its prefix alias)
    """
    # Get the field-name portion: first non-empty cell when pipes are
    # present, otherwise the text up to ':' or a 2+ space gap.
    if "|" in line:
        cells = [c.strip() for c in line.split("|") if c.strip()]
        if not cells:
            return None
        head = cells[0]
    else:
        # Look for "Field: Value" first; fall back to "Field   Value"
        if ":" in line:
            head = line.split(":", 1)[0].strip()
        else:
            parts = re.split(r"\s{2,}", line, maxsplit=1)
            if len(parts) != 2:
                return None
            head = parts[0].strip()

    head_norm = head.lower().strip()
    if not head_norm:
        return None

    # Prefer the longest matching alias so that "Owner History" wins
    # over "Owner" and "Taxpayer Name" wins over "Taxpayer".
    candidates: list[tuple[int, str]] = []
    for canonical, aliases in _FIELD_ALIASES.items():
        for alias in aliases:
            if head_norm == alias:
                candidates.append((len(alias), canonical))
                break
            if re.match(rf"^{re.escape(alias)}\b", head_norm) and len(head_norm) > len(alias):
                # Only count it if the head actually contains a separator
                # right after the alias (space, colon, end-of-string).
                next_char = head_norm[len(alias)] if len(head_norm) > len(alias) else ""
                if next_char in ("", " ", ":"):
                    candidates.append((len(alias), canonical))
                    break
    if candidates:
        candidates.sort(reverse=True)  # longest alias first
        return candidates[0][1]
    return None


def parse_property_record(raw: str, search_term: str) -> dict:
    """
    Parse raw text from an auditor page into a structured record.

    Handles three common layouts:
      • "Field: Value"  (one per line, the original format)
      • Markdown table  "| Field | Value |"
      • Two-column list "Field   Value" (whitespace-separated)
    """
    record = {
        "owner": "",
        "address": "",
        "parcel_id": "",
        "acreage": "",
        "market_value": "",
        "taxable_value": "",
        "sale_price": "",
        "deed_date": "",
        "last_sale_date": "",
        "year_built": "",
        "owner_history": "",
        "school_district": "",
        "search_term": search_term,
    }

    # Split into lines and try each canonical field exactly once.
    lines = [l.strip() for l in raw.split("\n") if l.strip()]

    for line in lines:
        canonical = _match_field(line)
        if not canonical or record[canonical]:
            continue

        # Extract the value side.  Three layouts:
        #   "Owner: John"
        #   "| Owner | John |"
        #   "Owner        John"
        # First strip markdown-table pipes
        if "|" in line:
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) >= 2:
                value = cells[1]
            else:
                continue
        else:
            # "Field: Value"  OR  "Field   Value"
            if ":" in line:
                value = line.split(":", 1)[1].strip()
            else:
                # whitespace-separated: split on 2+ spaces
                parts = re.split(r"\s{2,}", line, maxsplit=1)
                if len(parts) != 2:
                    continue
                value = parts[1].strip()

        # Clean common junk (currency formatting preserved)
        value = value.strip().rstrip("|").strip()
        if value:
            record[canonical] = value

    return record


def _parse_auditor_block(block: str, search_term: str, county: str, source_url: str, source: str) -> dict:
    """Wrap parse_property_record and add county/source metadata."""
    record = parse_property_record(block, search_term)
    record["county"] = county
    record["source_url"] = source_url
    record["source"] = source
    return record


# ---------------------------------------------------------------------------
# Multi-source scrape with retry / backoff / FlareSolverr fallback
# ---------------------------------------------------------------------------

async def _scrape_via_crawl4ai(url: str) -> tuple[str, dict]:
    """
    Source adapter for scrape_with_fallback.

    Returns the markdown text from crawl4ai, or an error sentinel string
    starting with '[' so the orchestrator knows to fall through to FS.
    """
    text, meta = await crawl4ai_scrape(url)
    if not text:
        return "[crawl4ai: empty response]", meta or {}
    if text.startswith("[") and "error" in text.lower():
        return text, meta or {}
    # Detect Cloudflare / bot challenge pages by signature phrases
    challenge_signatures = (
        "checking your browser",
        "please complete the security check",
        "attention required! | cloudflare",
        "ddos protection by cloudflare",
    )
    lower = text.lower()
    if any(sig in lower for sig in challenge_signatures):
        return "[crawl4ai: cloudflare challenge]", meta or {}
    return text, meta or {}


async def _scrape_via_flaresolverr(url: str) -> tuple[str, dict]:
    """
    Source adapter for FlareSolverr.

    Returns the HTML, or an error sentinel.  Cloudflare challenges are
    passed through as a sentinel so the orchestrator can mark them rather
    than treating them as success.
    """
    try:
        html, status = await fs_get(url)
    except CloudflareWallError as exc:
        return f"[flaresolverr: cloudflare wall: {exc}]", {"status": 0}
    except Exception as exc:
        return f"[flaresolverr error: {exc}]", {"status": 0}

    if not html or status >= 400:
        return f"[flaresolverr: status {status}]", {"status": status}

    challenge_signatures = (
        "checking your browser",
        "please complete the security check",
        "attention required! | cloudflare",
    )
    lower = html.lower()
    if any(sig in lower for sig in challenge_signatures):
        return "[flaresolverr: cloudflare challenge returned]", {"status": status}
    return html, {"status": status}


async def _scrape_auditor_url_with_fallback(
    url: str,
    *,
    use_flaresolverr: bool = True,
) -> tuple[str, str]:
    """
    Try crawl4ai first, then FlareSolverr, with retry+backoff per source.

    Returns (text, source_label).  The text may be HTML or markdown
    depending on which path succeeded; the caller treats both the same way
    (block-splitting + parse_property_record works on either).

    We use our own retry+backoff loop here (rather than scrape_with_fallback)
    because that helper requires >50 chars of content and treats short
    responses as failure — the wrong shape for our sentinel-based protocol,
    where a "Cloudflare challenge" sentinel is a 30-char string that we
    must detect and fall through on.
    """
    sources: list[tuple[str, Callable[[], Awaitable[tuple[str, dict]]]]] = [
        ("crawl4ai", lambda: _scrape_via_crawl4ai(url)),
    ]
    if use_flaresolverr:
        sources.append(("flaresolverr", lambda: _scrape_via_flaresolverr(url)))

    async def _try_with_retry(label: str, fn: Callable[[], Awaitable[tuple[str, dict]]]) -> str:
        last_sentinel = ""
        for attempt in range(2):
            try:
                text, _meta = await fn()
            except Exception as exc:
                logger.debug("auditor: %s raised on attempt %d for %s: %s", label, attempt + 1, url, exc)
                last_sentinel = f"[{label}: exception]"
                continue
            if text and not text.startswith("["):
                return text
            last_sentinel = text or last_sentinel
            if attempt == 0:
                await asyncio.sleep(1)
        logger.info("auditor: %s failed for %s (sentinel: %s)", label, url, last_sentinel[:80])
        return ""

    for label, fn in sources:
        text = await _try_with_retry(label, fn)
        if text:
            return text, label

    logger.warning("auditor: all sources failed for %s", url)
    return "", "none"


# ---------------------------------------------------------------------------
# Main search entry point
# ---------------------------------------------------------------------------

async def search_property_records(
    county: str,
    search_term: str,
    search_type: str = "owner",
    state: str = "Ohio",
    use_flaresolverr: bool = True,
) -> list[dict]:
    """
    Search property records for any US county.

    Dispatch order:
      1. STATE_PORTALS registry — dedicated scraper for the given state.
      2. Web-search discovery — find county auditor URL via Google.
      3. COUNTY_FALLBACK_URLS — hardcoded map of major-population counties
         (Ohio-only in the current map; other states fall through to search).

    county: county name (e.g. "Sullivan", "Sullivan County", "Franklin")
    search_term: owner name, street address, or parcel ID
    search_type: "owner", "address", or "parcel"
    state: full state name (default "Ohio")
    use_flaresolverr: when True (default), the scrape chain falls back to
        FlareSolverr if crawl4ai fails or hits a Cloudflare challenge.
        Set False to disable (e.g., when FlareSolverr is down).
    """
    state_key = state.lower().strip()
    portal_cfg = STATE_PORTALS.get(state_key)

    # --- Path 1: dedicated state portal ---
    if portal_cfg:
        portal_type = portal_cfg["portal_type"]
        if portal_type == "tpad":
            records = await _search_tpad_tn(county, search_term, portal_cfg)
            if records:
                return records
            # Fall through to web-search if county is external or unknown

    # --- Path 2: web-search discovery ---
    auditor_url = await find_auditor_url(county, state)

    # --- Path 3: COUNTY_FALLBACK_URLS (Ohio today) ---
    if not auditor_url and state_key == "ohio":
        key = county.lower().replace(" county", "").replace(" ", "_")
        entry = COUNTY_FALLBACK_URLS.get(key)
        if entry:
            auditor_url = entry[0]

    if not auditor_url:
        return []

    # --- Scrape with crawl4ai → FlareSolverr fallback chain ---
    try:
        text, source = await _scrape_auditor_url_with_fallback(
            auditor_url, use_flaresolverr=use_flaresolverr
        )
    except Exception as exc:
        logger.warning("auditor: scrape chain crashed for %s: %s", auditor_url, exc)
        return []

    if not text or text.startswith("["):
        return []

    # --- Extract matching records from the page text ---
    blocks = text.split("---")
    records = []
    for block in blocks[:50]:  # raised from 20 → 50 for multi-record pages
        if search_term.lower() in block.lower():
            record = _parse_auditor_block(block, search_term, county, auditor_url, source)
            records.append(record)

    return records


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

async def get_property_details(
    county: str,
    parcel_id: str,
    state: str = "Ohio",
    use_flaresolverr: bool = True,
) -> dict:
    """Look up a specific property by parcel ID."""
    results = await search_property_records(
        county, parcel_id, "parcel", state, use_flaresolverr=use_flaresolverr
    )
    for r in results:
        if parcel_id in str(r.get("parcel_id", "")):
            return r
    return {"parcel_id": parcel_id, "county": county, "status": "not found"}


async def get_property_by_address(
    county: str,
    address: str,
    state: str = "Ohio",
    use_flaresolverr: bool = True,
) -> dict:
    """
    Look up property details by street address.

    For TN via TPAD, the search is fuzzy and returns multiple hits sorted by
    score.  We pick the first result whose normalised address contains at least
    the street name from the query (number optional, matching is case-insensitive).
    If no match narrows it down, we return the top result if present.
    """
    results = await search_property_records(
        county, address, "address", state, use_flaresolverr=use_flaresolverr
    )
    if not results:
        return {"address": address, "county": county, "status": "not found"}

    addr_lower = address.lower()
    tokens = [t for t in addr_lower.split() if not t.isdigit()]

    for r in results:
        rec_addr = r.get("address", "").lower()
        if all(tok in rec_addr for tok in tokens):
            return r

    return results[0]
