"""
Wikidata scraper for THINGS entity type.
Provides structured entity data (types, properties, relationships) via SPARQL.
No auth required — public endpoint. Rate limited to 1 req/s on SPARQL.
"""
import httpx
import re
import json
from app.services.scraper.rate_limiter import WIKIDATA_LIMITER, retry_with_backoff
from app.services.entity_cache import get_cached, write_cached

WD_API = "https://www.wikidata.org/wiki/Special:EntityData"
WD_SPARQL = "https://query.wikidata.org/sparql"


async def get_wikidata_entity(entity_id: str) -> dict:
    """
    Fetch full entity data for a Wikidata entity (e.g. 'Q5' for human).
    Returns dict with claims, sitelinks, labels.
    """
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{WD_API}/{entity_id}.json")
            if r.status_code != 200:
                return {}
            return r.json()
    except Exception:
        return {}


async def search_wikidata(query: str, max_results: int = 5) -> list[dict]:
    """
    Search Wikidata by label (like "iPhone" or "The Beatles").
    Returns list of {entity_id, label, description}.
    """
    query_sparql = f'''
    SELECT ?item ?itemLabel ?itemDescription WHERE {{
      ?item ?label "{query}"@en.
      ?item rdfs:label ?itemLabel.
      FILTER(LANG(?itemLabel) = "en").
      OPTIONAL {{ ?item schema:description ?itemDescription. FILTER(LANG(?itemDescription) = "en") }}
    }}
    LIMIT {max_results}
    '''
    async with WIKIDATA_LIMITER:
        try:
            resp = await retry_with_backoff(
                lambda: httpx.AsyncClient(timeout=25.0).get(
                    WD_SPARQL,
                    params={"query": query_sparql, "format": "json"},
                    headers={
                        "User-Agent": "ObsessedTriviaBot/1.0 (phatt.tech)",
                        "Accept": "application/sparql-results+json",
                    },
                ),
                max_retries=3,
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for binding in data.get("results", {}).get("bindings", []):
                uri = binding.get("item", {}).get("value", "")
                entity_id = uri.split("/")[-1] if uri else ""
                results.append({
                    "entity_id": entity_id,
                    "label": binding.get("itemLabel", {}).get("value", ""),
                    "description": binding.get("itemDescription", {}).get("value", ""),
                })
            return results
        except Exception:
            return []


def _extract_property_value(claims: dict, prop_id: str) -> str:
    """Extract a single string value from a property's mainsnak."""
    for claim in claims.get(prop_id, []):
        mainsnak = claim.get("mainsnak", {})
        if mainsnak.get("snaktype") == "value":
            datavalue = mainsnak.get("datavalue", {})
            if datavalue.get("type") == "monolingualtext":
                return datavalue["value"]["text"]
            if datavalue.get("type") == "time":
                return datavalue["value"].get("time", "")
            if datavalue.get("type") == "quantity":
                return str(datavalue["value"].get("amount", ""))
            if datavalue.get("type") == "wikibase-entityid":
                return datavalue["value"]["id"]
            return str(datavalue.get("value", ""))
    return ""


def _extract_property_values(claims: dict, prop_id: str) -> list[str]:
    """Extract all values from a property (for multi-valued claims)."""
    values = []
    for claim in claims.get(prop_id, []):
        mainsnak = claim.get("mainsnak", {})
        if mainsnak.get("snaktype") == "value":
            datavalue = mainsnak.get("datavalue", {})
            if datavalue.get("type") == "monolingualtext":
                values.append(datavalue["value"]["text"])
            elif datavalue.get("type") == "time":
                values.append(datavalue["value"].get("time", ""))
            elif datavalue.get("type") == "quantity":
                values.append(str(datavalue["value"].get("amount", "")))
            elif datavalue.get("type") == "wikibase-entityid":
                values.append(datavalue["value"]["id"])
            elif datavalue.get("type") == "string":
                values.append(str(datavalue.get("value", "")))
    return values


async def scrape_wikidata(entity_id: str, entity_type: str = "thing") -> tuple[str, dict]:
    """
    Fetch and format Wikidata entity as readable text.
    Returns (raw_text, metadata).
    metadata: {label, description, entity_id, wikipedia_title}
    """
    if not entity_id:
        return "[Wikidata: no entity_id provided]", {}

    entity_data = await get_wikidata_entity(entity_id)
    if not entity_data:
        return f"[Wikidata: entity not found: {entity_id}]", {}

    try:
        labels = entity_data.get("entities", {}).get(entity_id, {}).get("labels", {})
        descriptions = entity_data.get("entities", {}).get(entity_id, {}).get("descriptions", {})
        claims = entity_data.get("entities", {}).get(entity_id, {}).get("claims", {})
        sitelinks = entity_data.get("entities", {}).get(entity_id, {}).get("sitelinks", {})

        label = labels.get("en", {}).get("value", entity_id)
        description = descriptions.get("en", {}).get("value", "")
        meta = {"label": label, "description": description, "entity_id": entity_id}

        # Wikipedia link if available
        if "enwiki" in sitelinks:
            meta["wikipedia_title"] = sitelinks["enwiki"]["title"]
            meta["wikipedia_url"] = f"https://en.wikipedia.org/wiki/{sitelinks['enwiki']['title'].replace(' ', '_')}"

        raw_parts = [f"[Wikidata: {label}]"]
        if description:
            raw_parts.append(f"Description: {description}")

        # Notable properties for "things"
        # P31 = instance of, P279 = subclass of (what kind of thing)
        instance_of = _extract_property_values(claims, "P31")
        if instance_of:
            raw_parts.append(f"Type: {', '.join(instance_of[:5])}")

        # P361 = part of, P527 = has part (for composite things)
        part_of = _extract_property_values(claims, "P361")
        if part_of:
            raw_parts.append(f"Part of: {', '.join(part_of[:3])}")

        # P577 = publication date (for works/creative items)
        pub_date = _extract_property_value(claims, "P577")
        if pub_date:
            # Strip leading + and format year
            year = re.sub(r"^\+?[^-+]+/", "", str(pub_date))[:4]
            raw_parts.append(f"Published: {year}")

        # P571 = inception date (for inventions, organizations)
        inception = _extract_property_value(claims, "P571")
        if inception and not pub_date:
            year = re.sub(r"^\+?[^-+]+/", "", str(inception))[:4]
            raw_parts.append(f"Inception: {year}")

        # P170 = creator (for artworks, inventions)
        creator = _extract_property_value(claims, "P170")
        if creator:
            raw_parts.append(f"Creator: {creator}")

        # P195 = collection (for artworks)
        collection = _extract_property_value(claims, "P195")
        if collection:
            raw_parts.append(f"Collection: {collection}")

        # P287 = designer (for products, works)
        designer = _extract_property_value(claims, "P287")
        if designer:
            raw_parts.append(f"Designer: {designer}")

        # P154 = logo image
        logo = _extract_property_value(claims, "P154")
        if logo:
            raw_parts.append(f"Logo: {logo}")

        # P373 = Wikimedia Commons category
        commons = _extract_property_value(claims, "P373")
        if commons:
            raw_parts.append(f"Commons: https://commons.wikimedia.org/wiki/{commons}")

        # P269 = SNILL (library identifier)
        # P6366 = page on this site (for things with Wikipedia articles)
        wikipedia_title = _extract_property_value(claims, "P6366")
        if wikipedia_title and "wikipedia_title" not in meta:
            meta["wikipedia_title"] = wikipedia_title

        # P180 = described by source
        write_cached(entity_id, entity_type, "\n".join(raw_parts), meta.get("label", ""))
        return "\n".join(raw_parts), meta

    except Exception as e:
        return f"[Wikidata parse error for {entity_id}: {e}]", {}


async def scrape_wikidata_by_query(query: str) -> tuple[str, list[dict]]:
    """
    Search Wikidata for a query, then scrape the top result.
    Returns (raw_text, wikidata_entries).
    """
    if not query:
        return "[Wikidata: empty query]", []

    results = await search_wikidata(query, max_results=3)
    if not results:
        return f"[Wikidata: no results for '{query}']", []

    raw_parts = []
    scraped_entries = []
    for result in results[:1]:  # scrape top result
        entity_id = result["entity_id"]
        text, meta = await scrape_wikidata(entity_id)
        if text and not text.startswith("[Wikidata: entity not found"):
            raw_parts.append(text)
            scraped_entries.append({"entity_id": entity_id, "meta": meta})

    if not raw_parts:
        return f"[Wikidata: scrape failed for '{query}']", []

    return "\n\n".join(raw_parts), scraped_entries