"""PHA-787 — live person test against every Obsessed pipeline.

Subject: Aaron Tom, DOB 1984-02-14, Franklin County / central Ohio.
Consent: confirmed by Brandon (subject is his friend who agreed).

Walks every scraper with seed inputs derived from the subject's identity, captures
a short preview + wall-hit, and writes a structured Markdown report we can paste
into the PHA-787 task comment. Does NOT trigger the LLM question-generation step —
this is a pipeline coverage check, not a trivia run.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# Subject seeds — speculative handles tried across social platforms.
SUBJECT_NAME = "Aaron Tom"
SUBJECT_DOB = "1984-02-14"
SUBJECT_COUNTY = "franklin"  # Ohio
SUBJECT_STATE = "ohio"
SUBJECT_LOCATION_QUERY = "Columbus, Ohio"
SPECULATIVE_HANDLES = ["aarontom", "aaron.tom", "aaron_tom", "atom", "tomaaron"]


@dataclass
class PipelineResult:
    name: str
    seed: str
    ok: bool = False
    chars: int = 0
    preview: str = ""
    error: str = ""
    note: str = ""
    elapsed_ms: int = 0


async def run(label: str, seed: str, coro) -> PipelineResult:
    t0 = time.perf_counter()
    res = PipelineResult(name=label, seed=seed)
    try:
        out = await asyncio.wait_for(coro, timeout=45)
    except asyncio.TimeoutError:
        res.error = "timeout(>45s)"
    except Exception as exc:
        res.error = f"{type(exc).__name__}: {exc}"[:240]
    else:
        # Normalize: scrapers return (text, meta) or list[dict] or str.
        text = ""
        meta = None
        if isinstance(out, tuple) and out:
            text = out[0] if isinstance(out[0], str) else json.dumps(out[0])[:1000]
            meta = out[1] if len(out) > 1 else None
        elif isinstance(out, list):
            text = json.dumps(out)[:2000]
        elif isinstance(out, str):
            text = out
        else:
            text = str(out)[:2000]

        res.chars = len(text or "")
        # Heuristic "ok" = produced content and not an error-sentinel string.
        head = (text or "")[:120].lower()
        looks_error = (
            not text
            or "scrape error" in head
            or "all sources failed" in head
            or text.startswith("[crawl4ai")
        )
        res.ok = not looks_error and res.chars > 40
        res.preview = (text or "")[:400].replace("\n", " ⏎ ")
        if meta:
            try:
                res.note = json.dumps(meta)[:200]
            except Exception:
                res.note = str(meta)[:200]
    res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return res


async def main() -> list[PipelineResult]:
    results: list[PipelineResult] = []

    # ── Social handles (speculative) ─────────────────────────────────────────
    from app.services.scraper.reddit import scrape_reddit
    from app.services.scraper.pinterest import scrape_pinterest
    from app.services.scraper.instagram import scrape_instagram
    from app.services.scraper.facebook import scrape_facebook
    from app.services.scraper.tiktok import scrape_tiktok
    from app.services.scraper.twitter_scraper import scrape_twitter
    from app.services.scraper.steam import scrape_steam

    for h in SPECULATIVE_HANDLES[:2]:  # keep social fanout reasonable
        results.append(await run(f"reddit/{h}", h, scrape_reddit(h)))
        results.append(await run(f"pinterest/{h}", h, scrape_pinterest(h)))
        results.append(await run(f"instagram/{h}", h, scrape_instagram(h)))
        results.append(await run(f"facebook/{h}", h, scrape_facebook(h)))
        results.append(await run(f"tiktok/{h}", h, scrape_tiktok(h)))
        results.append(await run(f"twitter/{h}", h, scrape_twitter(h)))
        results.append(await run(f"steam/{h}", h, scrape_steam(h)))

    # ── Knowledge bases (name-based) ────────────────────────────────────────
    from app.services.scraper.wikipedia import scrape_wikipedia, search_wikipedia
    from app.services.scraper.wikidata import scrape_wikidata_by_query
    from app.services.scraper.openlibrary import scrape_openlibrary_by_query
    from app.services.scraper.things import scrape_things

    results.append(await run("wikipedia/page", SUBJECT_NAME, scrape_wikipedia(SUBJECT_NAME)))
    results.append(await run("wikipedia/search", SUBJECT_NAME, _wrap_search(search_wikipedia, SUBJECT_NAME)))
    results.append(await run("wikidata", SUBJECT_NAME, scrape_wikidata_by_query(SUBJECT_NAME)))
    results.append(await run("openlibrary", SUBJECT_NAME, scrape_openlibrary_by_query(SUBJECT_NAME)))
    results.append(await run("things/aggregate", SUBJECT_NAME, scrape_things(wikipedia_query=SUBJECT_NAME, wikidata_query=SUBJECT_NAME, openlibrary_query=SUBJECT_NAME)))

    # ── Location pipelines (Franklin County / Columbus) ─────────────────────
    from app.services.scraper.osm import scrape_osm
    from app.services.scraper.geonames import scrape_geonames
    from app.services.scraper.places import scrape_places

    results.append(await run("osm/location", SUBJECT_LOCATION_QUERY, scrape_osm(SUBJECT_LOCATION_QUERY)))
    results.append(await run("geonames", SUBJECT_LOCATION_QUERY, scrape_geonames(SUBJECT_LOCATION_QUERY)))
    results.append(await run("places", SUBJECT_LOCATION_QUERY, scrape_places(wikipedia_query=SUBJECT_LOCATION_QUERY, osm_query=SUBJECT_LOCATION_QUERY)))

    # ── News + events ────────────────────────────────────────────────────────
    from app.services.scraper.news import search_news
    from app.services.scraper.gdelt import scrape_gdelt
    from app.services.scraper.events import scrape_events

    results.append(await run("news/google-rss", f"{SUBJECT_NAME} Columbus Ohio", _wrap_search(search_news, f"{SUBJECT_NAME} Columbus Ohio", count=10)))
    results.append(await run("gdelt", SUBJECT_NAME, scrape_gdelt(SUBJECT_NAME)))
    results.append(await run("events/aggregate", SUBJECT_NAME, scrape_events(wikipedia_query=SUBJECT_NAME, gdelt_query=SUBJECT_NAME)))

    # ── Public records: court / auditor / SOS ───────────────────────────────
    from app.services.scraper.court import scrape_court_docket
    from app.services.scraper.auditor import search_property_records, get_property_by_address
    from app.services.scraper.sos import search_sos_entities, search_by_owner

    results.append(await run("court/franklin-docket", SUBJECT_NAME, _wrap_search(scrape_court_docket, SUBJECT_COUNTY, SUBJECT_NAME)))
    results.append(await run("auditor/franklin-owner", SUBJECT_NAME, _wrap_search(search_property_records, SUBJECT_COUNTY, SUBJECT_NAME, "owner")))
    results.append(await run("auditor/franklin-address", SUBJECT_NAME, _wrap_search(get_property_by_address, SUBJECT_COUNTY, SUBJECT_NAME)))
    results.append(await run("sos/ohio-entities", SUBJECT_NAME, _wrap_search(search_sos_entities, SUBJECT_STATE, SUBJECT_NAME)))
    results.append(await run("sos/ohio-by-owner", SUBJECT_NAME, _wrap_search(search_by_owner, SUBJECT_STATE, SUBJECT_NAME)))

    # ── Generic crawl ────────────────────────────────────────────────────────
    from app.services.scraper.crawl4ai import crawl4ai_scrape

    # A live Google search URL — exercises the generic crawl path against a real, JS-rendered page.
    google_url = (
        "https://www.google.com/search?q="
        + "%22Aaron+Tom%22+%22Franklin+County%22+Ohio"
    )
    results.append(await run("crawl4ai/google", google_url, crawl4ai_scrape(google_url)))

    return results


def _wrap_search(fn, *args, **kwargs):
    """Wrap sync-or-async search helpers so run() always gets an awaitable."""
    out = fn(*args, **kwargs)
    if asyncio.iscoroutine(out):
        return out

    async def _passthrough():
        return out

    return _passthrough()


def render_report(results: list[PipelineResult]) -> str:
    lines = []
    lines.append(f"# PHA-787 — Live pipeline test: {SUBJECT_NAME}")
    lines.append("")
    lines.append(f"- Subject: {SUBJECT_NAME} (DOB {SUBJECT_DOB})")
    lines.append(f"- Location: Franklin County, Ohio")
    lines.append(f"- Consent: confirmed via Brandon")
    lines.append(f"- Run at: {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
    lines.append("")
    ok = sum(1 for r in results if r.ok)
    lines.append(f"**Pipelines exercised:** {len(results)} — **hits (content > 40 chars, no error sentinel):** {ok}")
    lines.append("")
    lines.append("| Pipeline | Seed | OK | Chars | ms | Note / Error |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        flag = "✅" if r.ok else "❌"
        msg = (r.error or r.note or "").replace("|", "/")[:160]
        lines.append(f"| {r.name} | `{r.seed[:60]}` | {flag} | {r.chars} | {r.elapsed_ms} | {msg} |")
    lines.append("")
    lines.append("## Previews (first 400 chars per pipeline)")
    for r in results:
        lines.append(f"\n### {r.name} — seed `{r.seed[:80]}` — ok={r.ok} chars={r.chars}")
        if r.error:
            lines.append(f"_error_: `{r.error}`")
        if r.preview:
            lines.append("```")
            lines.append(r.preview)
            lines.append("```")
    return "\n".join(lines)


if __name__ == "__main__":
    try:
        results = asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)

    report = render_report(results)
    out_md = REPO / "data" / "pha787_live_test_report.md"
    out_json = REPO / "data" / "pha787_live_test_results.json"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(report, encoding="utf-8")
    out_json.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    print(report)
    print(f"\n[wrote {out_md}]")
    print(f"[wrote {out_json}]")
