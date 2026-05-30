"""
County Auditor / property-records scraper.

Dispatch order per request:
  1. STATE_PORTALS registry → dedicated scraper for known state portals
  2. Search-first discovery → Google search for county auditor URL
  3. OH_AUDITOR_URLS fallback → hardcoded Ohio county URLs

Adding a state is config, not code: add an entry to STATE_PORTALS with a
portal_type and any portal-specific kwargs.  The scraper function referenced
by portal_type handles the actual HTTP calls.

Tennessee (portal_type="tpad"):
  Centralised at assessment.cot.tn.gov/TPAD.  86 of 95 counties are served
  via a POST JSON API; the remaining 9 link to county-specific external sites.
  This scraper uses the TPAD Search/GetSearchResults endpoint directly —
  no JS execution required.  Market value is not returned by the search API
  (it loads via a JS/Tyler token on the details page); the field is left empty
  and noted in the docstring.

Ohio (portal_type="oh_fallback"):
  Falls back to the hardcoded URL map plus web-search discovery, same as
  the original behaviour.
"""

from __future__ import annotations

import os
import re
import httpx
from typing import Optional

from app.services.scraper.crawl4ai import crawl4ai_scrape
from app.services.scraper.flaresolverr import fs_get, CloudflareWallError


# ---------------------------------------------------------------------------
# Ohio county fallback URLs (unchanged from original)
# ---------------------------------------------------------------------------

OH_AUDITOR_URLS: dict[str, str] = {
    "franklin": "https://property.franklincountyauditor.org",
    "delaware": "https://delawarecountyauditor.org/property-search/",
    "licking": "https://lickingcountyauditor.org/property-search/",
    "fairfield": "https://fairfieldcountyauditor.com/property-search/",
    "union": "https://unioncountyauditor.org/property-search/",
    "madison": "https://madisoncountyauditor.org/",
    "pickaway": "https://pickawaycountyauditor.com/",
    "hocking": "https://hockingcountyauditor.net/property-search/",
    "athens": "https://athenscountyauditor.com/",
    "vinton": "https://vintoncountyauditor.com/",
}

# Keep the old name as an alias so any existing imports don't break.
AUDITOR_SEARCH_URLS = OH_AUDITOR_URLS


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
    except Exception:
        return []

    records = []
    for item in raw:
        if not item or item.get("empty"):
            continue

        raw_address = item.get("propertyAddress", "").strip()
        owner = item.get("owner", "").strip()
        parcel_id = item.get("parcelId", "").strip()

        # Normalise address: TPAD stores as "STREET NAME  NUMBER" (name first)
        # reformat to conventional "NUMBER STREET NAME" when possible
        address = _normalise_tpad_address(raw_address)

        record = {
            "owner": owner,
            "address": address,
            "parcel_id": parcel_id,
            "acreage": "",
            "market_value": "",   # not available from search endpoint
            "taxable_value": "",
            "search_term": search_term,
            "county": county,
            "source_url": f"{base_url}/Parcel/Details?parcelId={parcel_id}&jur={county_code}",
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
    # Matches e.g. "FORT HENRY DR  1797" or "OAK ST  201"
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
            urls = re.findall(r'https?://[^\s<>\'"]+', text)
            for url in urls:
                url_lower = url.lower()
                if any(k in url_lower for k in ['auditor', 'property', 'search', 'parcel']):
                    if 'google' not in url_lower and 'search?' not in url_lower:
                        return url.split('&')[0].split('?')[0]
            return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Record parser (generic — used for crawl4ai / FlareSolverr paths)
# ---------------------------------------------------------------------------

def parse_property_record(raw: str, search_term: str) -> dict:
    """Parse raw text from an auditor page into a structured record."""
    lines = [l.strip() for l in raw.split("\n") if l.strip()]
    record = {
        "owner": "",
        "address": "",
        "parcel_id": "",
        "acreage": "",
        "market_value": "",
        "taxable_value": "",
        "search_term": search_term,
    }
    for line in lines:
        lower = line.lower()
        if "owner" in lower and not record["owner"]:
            record["owner"] = line.split(":", 1)[-1].strip()
        elif "address" in lower and not record["address"]:
            record["address"] = line.split(":", 1)[-1].strip()
        elif "parcel" in lower and not record["parcel_id"]:
            record["parcel_id"] = line.split(":", 1)[-1].strip()
        elif "acre" in lower and not record["acreage"]:
            record["acreage"] = line.split(":", 1)[-1].strip()
        elif "market" in lower and not record["market_value"]:
            record["market_value"] = line.split(":", 1)[-1].strip()
        elif "taxable" in lower and not record["taxable_value"]:
            record["taxable_value"] = line.split(":", 1)[-1].strip()
    return record


# ---------------------------------------------------------------------------
# Main search entry point
# ---------------------------------------------------------------------------

async def search_property_records(
    county: str,
    search_term: str,
    search_type: str = "owner",
    state: str = "Ohio",
    use_flaresolverr: bool = False,
) -> list[dict]:
    """
    Search property records for any US county.

    Dispatch order:
      1. STATE_PORTALS registry — dedicated scraper for the given state.
      2. Web-search discovery — find county auditor URL via Google.
      3. OH fallback map — hardcoded Ohio county URLs (Ohio only).

    county: county name (e.g. "Sullivan", "Sullivan County", "Franklin")
    search_term: owner name, street address, or parcel ID
    search_type: "owner", "address", or "parcel"
    state: full state name (default "Ohio")
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

    # --- Path 3: Ohio fallback map ---
    if not auditor_url and state_key == "ohio":
        key = county.lower().replace(" county", "").replace(" ", "_")
        auditor_url = OH_AUDITOR_URLS.get(key, "")

    if not auditor_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(auditor_url)
        if text and not text.startswith("["):
            records = []
            blocks = text.split("---")
            for block in blocks[:20]:
                if search_term.lower() in block.lower():
                    record = parse_property_record(block, search_term)
                    record["county"] = county
                    record["source_url"] = auditor_url
                    records.append(record)
            return records

        if use_flaresolverr:
            try:
                html, status = await fs_get(auditor_url)
                if html and status < 400:
                    blocks = html.split("---")
                    records = []
                    for block in blocks[:20]:
                        if search_term.lower() in block.lower():
                            record = parse_property_record(block, search_term)
                            record["county"] = county
                            record["source_url"] = auditor_url
                            records.append(record)
                    return records
            except CloudflareWallError:
                pass

        return []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Convenience wrappers
# ---------------------------------------------------------------------------

async def get_property_details(county: str, parcel_id: str, state: str = "Ohio") -> dict:
    """Look up a specific property by parcel ID."""
    results = await search_property_records(county, parcel_id, "parcel", state)
    for r in results:
        if parcel_id in str(r.get("parcel_id", "")):
            return r
    return {"parcel_id": parcel_id, "county": county, "status": "not found"}


async def get_property_by_address(
    county: str,
    address: str,
    state: str = "Ohio",
) -> dict:
    """
    Look up property details by street address.

    For TN via TPAD, the search is fuzzy and returns multiple hits sorted by
    score.  We pick the first result whose normalised address contains at least
    the street name from the query (number optional, matching is case-insensitive).
    If no match narrows it down, we return the top result if present.
    """
    results = await search_property_records(county, address, "address", state)
    if not results:
        return {"address": address, "county": county, "status": "not found"}

    # Try to find the closest address match
    addr_lower = address.lower()
    # Extract street name tokens (drop numeric tokens for fuzzy match)
    tokens = [t for t in addr_lower.split() if not t.isdigit()]

    for r in results:
        rec_addr = r.get("address", "").lower()
        if all(tok in rec_addr for tok in tokens):
            return r

    # No token match — return top result by score
    return results[0]
