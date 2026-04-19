"""
County Auditor property records scraper.
Search-first discovery: accepts any US county name, discovers the actual auditor
website via web search, then scrapes with crawl4ai. Nationwide — no hardcoded county list.
"""

from __future__ import annotations

import os
import re
import httpx
from typing import Optional

from app.services.scraper.crawl4ai import crawl4ai_scrape


# Fallback known auditor URLs (Ohio counties — others discovered via search)
AUDITOR_SEARCH_URLS = {
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


def parse_property_record(raw: str, search_term: str) -> dict:
    """Parse raw text from auditor page into structured record."""
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


async def search_property_records(
    county: str,
    search_term: str,
    search_type: str = "owner",
    state: str = "Ohio",
) -> list[dict]:
    """
    Discover the auditor URL for a county via web search, then scrape.
    county: county name (e.g. "Franklin County", "Delaware County")
    search_term: owner name or property address
    search_type: "owner" or "address"
    state: state name (default Ohio, expandable)
    Returns list of property records.
    """
    # Try web search first
    auditor_url = await find_auditor_url(county, state)
    if not auditor_url:
        # Fallback to known Ohio county URLs
        key = county.lower().replace(" county", "").replace(" ", "_")
        auditor_url = AUDITOR_SEARCH_URLS.get(key, "")

    if not auditor_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(auditor_url)
        if not text or text.startswith("["):
            return []
        records = []
        blocks = text.split("---")
        for block in blocks[:20]:
            if search_term.lower() in block.lower():
                record = parse_property_record(block, search_term)
                record["county"] = county
                record["source_url"] = auditor_url
                records.append(record)
        return records
    except Exception:
        return []


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
    """Look up property details by street address."""
    results = await search_property_records(county, address, "address", state)
    if results:
        return results[0]
    return {"address": address, "county": county, "status": "not found"}