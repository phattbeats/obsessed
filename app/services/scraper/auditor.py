"""
County Auditor property records scraper.
Searches county auditor websites for property records by owner name or address.
Returns structured property records (owner, address, acreage, value) without storing full deeds.
"""

from __future__ import annotations

import os
from typing import Optional
import httpx

from app.services.scraper.crawl4ai import crawl4ai_scrape


# County auditor property search URLs
# These are public-facing search pages
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
    search_type: str = "owner",  # "owner" or "address"
) -> list[dict]:
    """
    Search a county auditor for property records.
    county: one of the keys in AUDITOR_SEARCH_URLS
    search_term: owner name or property address
    search_type: "owner" or "address"
    Returns list of property records.
    """
    base_url = AUDITOR_SEARCH_URLS.get(county.lower(), "")
    if not base_url:
        return []

    try:
        text, meta = await crawl4ai_scrape(base_url)
        if not text or text.startswith("["):
            return []
        records = []
        # Look for blocks containing the search term
        blocks = text.split("---")
        for block in blocks[:20]:
            if search_term.lower() in block.lower():
                record = parse_property_record(block, search_term)
                record["county"] = county
                record["source_url"] = base_url
                records.append(record)
        return records
    except Exception:
        return []


async def get_property_details(county: str, parcel_id: str) -> dict:
    """Look up a specific property by parcel ID."""
    results = await search_property_records(county, parcel_id, "parcel")
    for r in results:
        if parcel_id in str(r.get("parcel_id", "")):
            return r
    return {"parcel_id": parcel_id, "county": county, "status": "not found"}


async def get_property_by_address(county: str, address: str) -> dict:
    """Look up property details by street address."""
    results = await search_property_records(county, address, "address")
    if results:
        return results[0]
    return {"address": address, "county": county, "status": "not found"}