import httpx

from app.services.entity_cache import get_cached, write_cached

OL_API = "https://openlibrary.org"


async def search_openlibrary(query: str, max_results: int = 3) -> list[dict]:
    """
    Search OpenLibrary for a book/work by title.
    Returns list of {key, title, author, year, cover_i}.
    """
    try:
        # CHECK CACHE FIRST
        key = query
        entity_type = "thing"
        cached = get_cached(key, entity_type)
        if cached:
            raw_content, meta = cached
            return eval(raw_content)  # CACHE HIT

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(
                f"{OL_API}/search.json",
                params={"q": query, "limit": max_results},
            )
            r.raise_for_status()
            data = r.json()
            results = []
            for doc in data.get("docs", [])[:max_results]:
                results.append({
                    "key": doc.get("key", ""),
                    "title": doc.get("title", ""),
                    "author": ", ".join(doc.get("author_name", [])[:2]),
                    "year": doc.get("first_publish_year", ""),
                    "cover_i": doc.get("cover_i", ""),
                    "isbn": doc.get("isbn", [""])[0],
                    "subjects": doc.get("subject", [])[:5],
                    "pages": doc.get("number_of_pages_median", 0),
                    "language": doc.get("language", [""])[0],
                })
            write_cached(key, entity_type, repr(results), "")
            return results
    except Exception:
        return []


async def scrape_openlibrary(key: str, entity_type: str = "thing") -> tuple[str, dict]:
    """
    Fetch a specific OpenLibrary work/edition by key (e.g. '/works/OL123W').
    Returns (raw_text, metadata).
    """
    try:
        # CHECK CACHE FIRST
        cached = get_cached(key, entity_type)
        if cached:
            raw_content, meta = cached
            return raw_content, meta  # CACHE HIT

        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{OL_API}{key}.json")
            if r.status_code != 200:
                return f"[OpenLibrary: not found for key {key}]", {}

            d = r.json()
            meta = {
                "key": key,
                "title": d.get("title", ""),
                "description": "",
            }

            desc = d.get("description", {})
            if isinstance(desc, dict):
                meta["description"] = desc.get("value", "")
            else:
                meta["description"] = str(desc or "")

            raw_parts = [f"[OpenLibrary: {meta['title']}]"]
            if meta["description"]:
                raw_parts.append(meta["description"])

            subjects = d.get("subject", [])[:10]
            if subjects:
                raw_parts.append("Subjects: " + ", ".join(subjects))

            raw_content = "\n".join(raw_parts)
            write_cached(key, entity_type, raw_content, meta.get("key", ""))
            return raw_content, meta
    except Exception as e:
        return f"[OpenLibrary scrape error for {key}: {e}]", {}
