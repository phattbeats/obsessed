"""PHA-870 — full pipeline test seeded from an ADDRESS (not a name).

Subject: 1797 Fort Henry Dr, Kingsport, TN 37664  (Sullivan County, Tennessee)

The PHA-787 sibling started from name+DOB+county in Ohio. This run inverts the
problem: we only get a street address, and we want to see how far the platform
can walk address -> property owner -> identity -> records, OUTSIDE Ohio (TN).

Walks every address-relevant pipeline with the address (and, where a name is
required, with whatever owner name the auditor/people-search step discovers),
captures a short preview + wall-hit, and writes a structured Markdown report.
Re-runnable: edit the ADDR_* constants at the top.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

# ── Address seeds ────────────────────────────────────────────────────────────
ADDR_FULL = "1797 Fort Henry Dr, Kingsport, TN 37664"
ADDR_STREET = "1797 Fort Henry Dr"
ADDR_CITY = "Kingsport"
ADDR_STATE = "Tennessee"
ADDR_STATE_ABBR = "TN"
ADDR_COUNTY = "Sullivan"  # Kingsport (the 37664 ZIP) sits in Sullivan County, TN
ADDR_ZIP = "37664"


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
        text = ""
        meta = None
        if isinstance(out, tuple) and out:
            text = out[0] if isinstance(out[0], str) else json.dumps(out[0])[:1000]
            meta = out[1] if len(out) > 1 else None
        elif isinstance(out, (list, dict)):
            text = json.dumps(out)[:2000]
        elif isinstance(out, str):
            text = out
        else:
            text = str(out)[:2000]

        res.chars = len(text or "")
        head = (text or "")[:120].lower()
        looks_error = (
            not text
            or "scrape error" in head
            or "all sources failed" in head
            or text.startswith("[crawl4ai")
            or text in ("[]", "{}", "null")
        )
        res.ok = not looks_error and res.chars > 40
        res.preview = (text or "")[:500].replace("\n", " ⏎ ")
        if meta:
            try:
                res.note = json.dumps(meta)[:200]
            except Exception:
                res.note = str(meta)[:200]
    res.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return res


def _wrap(fn, *args, **kwargs):
    out = fn(*args, **kwargs)
    if asyncio.iscoroutine(out):
        return out

    async def _passthrough():
        return out

    return _passthrough()


async def main() -> list[PipelineResult]:
    results: list[PipelineResult] = []

    # ── 1. Geolocation — can we even place the address? ──────────────────────
    from app.services.scraper.osm import scrape_osm
    from app.services.scraper.geonames import scrape_geonames
    from app.services.scraper.places import scrape_places

    results.append(await run("osm/full-address", ADDR_FULL, scrape_osm(ADDR_FULL)))
    results.append(await run("osm/city", f"{ADDR_CITY}, {ADDR_STATE}", scrape_osm(f"{ADDR_CITY}, {ADDR_STATE}")))
    results.append(await run("geonames/city", f"{ADDR_CITY}, {ADDR_STATE}", scrape_geonames(f"{ADDR_CITY}, {ADDR_STATE}")))
    results.append(await run("places/aggregate", ADDR_FULL,
                             scrape_places(osm_query=ADDR_FULL, wikipedia_query=f"{ADDR_CITY}, Tennessee")))

    # ── 2. Property owner — the address -> identity hinge ────────────────────
    from app.services.scraper.auditor import (
        find_auditor_url, search_property_records, get_property_by_address,
    )
    results.append(await run("auditor/find-url", f"{ADDR_COUNTY} {ADDR_STATE}",
                             _wrap(find_auditor_url, ADDR_COUNTY, ADDR_STATE)))
    results.append(await run("auditor/by-address", ADDR_FULL,
                             _wrap(get_property_by_address, ADDR_COUNTY, ADDR_STREET, ADDR_STATE)))
    results.append(await run("auditor/search-address", ADDR_STREET,
                             _wrap(search_property_records, ADDR_COUNTY, ADDR_STREET, "address", ADDR_STATE)))

    # ── 3. Reverse people search on the address ──────────────────────────────
    # people_search only builds /name/ URLs; exercise the generic crawl path
    # against FastPeopleSearch + a direct reverse-address provider to show the gap.
    from app.services.scraper.crawl4ai import crawl4ai_scrape
    fps_addr = ("https://www.fastpeoplesearch.com/address/"
                + ADDR_STREET.lower().replace(" ", "-")
                + f"_{ADDR_CITY.lower()}-{ADDR_STATE_ABBR.lower()}")
    results.append(await run("crawl4ai/fps-reverse-address", fps_addr, crawl4ai_scrape(fps_addr)))

    # ── 4. News / events on the address + locality ───────────────────────────
    from app.services.scraper.news import search_news
    from app.services.scraper.gdelt import scrape_gdelt
    results.append(await run("news/address", ADDR_FULL, _wrap(search_news, ADDR_FULL, count=10)))
    results.append(await run("news/locality", f"{ADDR_STREET} {ADDR_CITY} TN",
                             _wrap(search_news, f"{ADDR_STREET} {ADDR_CITY} TN", count=10)))
    results.append(await run("gdelt/address", ADDR_FULL, scrape_gdelt(ADDR_FULL)))

    # ── 5. TN public records (Ohio-defaulted scrapers pointed at TN) ─────────
    from app.services.scraper.court import scrape_court_docket
    from app.services.scraper.sos import search_sos_entities, search_by_owner
    results.append(await run("court/tn-by-address", ADDR_STREET,
                             _wrap(scrape_court_docket, ADDR_COUNTY, ADDR_STREET)))
    results.append(await run("sos/tn-entities-byaddr", ADDR_STREET,
                             _wrap(search_sos_entities, ADDR_STATE.lower(), ADDR_STREET)))

    # ── 6. Generic crawl — "who lives at <address>" surface index ────────────
    google_url = ("https://www.google.com/search?q="
                  + ADDR_FULL.replace(" ", "+").replace(",", "%2C"))
    results.append(await run("crawl4ai/google-address", google_url, crawl4ai_scrape(google_url)))

    return results


def render_report(results: list[PipelineResult]) -> str:
    lines = [f"# PHA-870 — Address-first pipeline test", ""]
    lines.append(f"- Seed address: **{ADDR_FULL}**")
    lines.append(f"- County/State: {ADDR_COUNTY} County, {ADDR_STATE}")
    lines.append(f"- Run at: {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
    lines.append("")
    ok = sum(1 for r in results if r.ok)
    lines.append(f"**Pipelines exercised:** {len(results)} — **hits (>40 chars, no error sentinel):** {ok}")
    lines.append("")
    lines.append("| Pipeline | Seed | OK | Chars | ms | Note / Error |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        flag = "✅" if r.ok else "❌"
        msg = (r.error or r.note or "").replace("|", "/")[:160]
        lines.append(f"| {r.name} | `{r.seed[:50]}` | {flag} | {r.chars} | {r.elapsed_ms} | {msg} |")
    lines.append("\n## Previews (first 500 chars)")
    for r in results:
        lines.append(f"\n### {r.name} — ok={r.ok} chars={r.chars}")
        if r.error:
            lines.append(f"_error_: `{r.error}`")
        if r.preview:
            lines.append("```\n" + r.preview + "\n```")
    return "\n".join(lines)


if __name__ == "__main__":
    results = asyncio.run(main())
    report = render_report(results)
    out_md = REPO / "data" / "pha870_address_test_report.md"
    out_json = REPO / "data" / "pha870_address_test_results.json"
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(report, encoding="utf-8")
    out_json.write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    print(report)
    print(f"\n[wrote {out_md}]\n[wrote {out_json}]")
