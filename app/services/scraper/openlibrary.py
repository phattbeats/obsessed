"""
OpenLibrary scraper for THINGS entity type — books, authors, publishers.
Free API, no auth, good structured data on published works.
"""
import httpx
import re

OL_API = "https://openlibrary.org"


async def search_openlibrary(query: str, max_results: int = 3) -> list[dict]:
    """
    Search OpenLibrary for a book/work by title.
    Returns list of {key, title, author, year, cover_i}.
    """
    try:
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
                    "key": doc.get("key", ""),  # e.g. "/works/OL123W"
                    "title": doc.get("title", ""),
                    "author": ", ".join(doc.get("author_name", [])[:2]),
                    "year": doc.get("first_publish_year", ""),
                    "cover_i": doc.get("cover_i", ""),
                    "isbn": doc.get("isbn", [""])[0],
                    "subjects": doc.get("subject", [])[:5],
                    "pages": doc.get("number_of_pages_median", 0),
                    "language": doc.get("language", [""])[0],
                })
            return results
    except Exception:
        return []


async def scrape_openlibrary(key: str) -> tuple[str, dict]:
    """
    Fetch a specific OpenLibrary work/edition by key (e.g. '/works/OL123W').
    Returns (raw_text, metadata).
    """
    try:
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

            # Extract description (can be string or dict with 'value')
            desc = d.get("description", {})
            if isinstance(desc, dict):
                meta["description"] = desc.get("value", "")
            else:
                meta["description"] = str(desc or "")

            raw_parts = [f"[OpenLibrary: {meta['title']}]"]
            if meta["description"]:
                raw_parts.append(meta["description"])

            # Subjects / topics
            subjects = d.get("subjects", []) or d.get("subject_places", []) or d.get("subject_times", []) or []
            if subjects:
                raw_parts.append(f"Topics: {', '.join(subjects[:8])}")

            # Author links
            authors = d.get("authors", [])
            if authors:
                author_names = []
                for a in authors[:3]:
                    author_key = a.get("key", "")
                    if author_key:
                        # Try to fetch author name
                        try:
                            ar = await client.get(f"{OL_API}{author_key}.json")
                            if ar.status_code == 200:
                                ad = ar.json()
                                author_names.append(ad.get("name", author_key))
                        except Exception:
                            author_names.append(author_key.split("/")[-1])
                if author_names:
                    raw_parts.append(f"Authors: {', '.join(author_names)}")

            # Cover image
            cover_i = d.get("covers", [None])[0]
            if cover_i:
                meta["cover_url"] = f"https://covers.openlibrary.org/b/id/{cover_i}-L.jpg"

            # First published
            created = d.get("created", {})
            if created:
                date = created.get("value", "")[:10] if isinstance(created, dict) else str(created)[:10]
                if date:
                    raw_parts.append(f"Created: {date}")

            return "\n".join(raw_parts), meta

    except Exception as e:
        return f"[OpenLibrary scrape error for {key}: {e}]", {}


async def scrape_openlibrary_by_query(query: str) -> tuple[str, list[dict]]:
    """
    Search OpenLibrary for a query, then scrape the top result.
    Returns (raw_text, entries_found).
    """
    if not query:
        return "[OpenLibrary: empty query]", []

    search_results = await search_openlibrary(query, max_results=3)
    if not search_results:
        return f"[OpenLibrary: no results for '{query}']", []

    raw_parts = []
    entries = []
    for result in search_results[:2]:
        key = result.get("key", "")
        if key:
            text, meta = await scrape_openlibrary(key)
            if text and not text.startswith("[OpenLibrary: not found"):
                raw_parts.append(text)
                entries.append({**result, "meta": meta})

    if not raw_parts:
        return f"[OpenLibrary: scrape failed for '{query}']", []

    return "\n\n".join(raw_parts), entries