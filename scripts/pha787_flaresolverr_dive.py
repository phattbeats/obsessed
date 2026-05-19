"""PHA-787 round 3 — break the Cloudflare/DataDome walls via FlareSolverr.

Targets all the pipelines that were walled in round 2:
- Ohio SOS Business Search (Cloudflare)
- Ohio voter lookup (Cloudflare)
- FastPeopleSearch (Cloudflare)
- TruePeopleSearch (DataDome)
- Legacy.com obituaries (DataDome)

Captures HTML + parsed findings + walls to data/pha787_round3.md.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

REPO = Path(__file__).resolve().parents[1]
ARTIFACTS = REPO / "data" / "pha787_round3"
ARTIFACTS.mkdir(parents=True, exist_ok=True)

FS = "http://flaresolverr:8191/v1"
SUBJECT = "Aaron Tom"
SUBJECT_DOB = "02/14/1984"
SUBJECT_CITY = "Hilliard"
SUBJECT_STATE = "OH"


@dataclass
class Probe:
    name: str
    url: str
    method: str = "GET"
    ok: bool = False
    http_status: int = 0
    chars: int = 0
    findings: list[str] = field(default_factory=list)
    artifact: str = ""
    error: str = ""
    elapsed_ms: int = 0


async def fs_request(client: httpx.AsyncClient, *, cmd: str, url: str, post_data: str | None = None) -> dict:
    payload = {"cmd": cmd, "url": url, "maxTimeout": 60000}
    if post_data is not None:
        payload["postData"] = post_data
    r = await client.post(FS, json=payload, timeout=80)
    return r.json()


def save_artifact(name: str, html: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    path = ARTIFACTS / f"{safe}.html"
    path.write_text(html or "", encoding="utf-8")
    return str(path.relative_to(REPO))


async def probe(client: httpx.AsyncClient, name: str, url: str, *, post_data: str | None = None) -> Probe:
    p = Probe(name=name, url=url, method="POST" if post_data else "GET")
    t0 = time.perf_counter()
    try:
        out = await fs_request(client, cmd="request.post" if post_data else "request.get", url=url, post_data=post_data)
    except Exception as exc:
        p.error = f"{type(exc).__name__}: {exc}"[:240]
    else:
        sol = out.get("solution") or {}
        html = sol.get("response", "") or ""
        p.http_status = sol.get("status") or 0
        p.chars = len(html)
        p.artifact = save_artifact(name, html)
        if out.get("status") == "ok" and p.http_status and p.http_status < 400 and p.chars > 1000:
            p.ok = True
    p.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    return p


def text_of(html: str) -> str:
    return BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)


# ── Probes ──────────────────────────────────────────────────────────────────

async def ohio_sos(client: httpx.AsyncClient) -> list[Probe]:
    """Ohio SOS business search — POST against their entity name search."""
    out = []
    landing = await probe(client, "ohio_sos_landing", "https://businesssearch.ohiosos.gov/")
    out.append(landing)
    if not landing.ok:
        return out

    # The form is JS-driven (Angular) and the actual search hits an API.
    # First, hit their entity-search API endpoint discovered via inspection.
    api_calls = [
        ("ohio_sos_byname_aaron_tom",
         "https://businesssearch.ohiosos.gov/?=businessdetails/Name%2C%20Aaron%20Tom"),
        ("ohio_sos_search_aaron_tom",
         "https://bizimage.ohiosos.gov/api/?nameTermStartsOrContains=contains&name=tom+aaron"),
        ("ohio_sos_search_tom_aaron",
         "https://bizimage.ohiosos.gov/api/?nameTermStartsOrContains=contains&name=aaron+tom"),
    ]
    for name, url in api_calls:
        out.append(await probe(client, name, url))
    return out


async def ohio_voter(client: httpx.AsyncClient) -> list[Probe]:
    """Ohio voter file — voterlookup.ohiosos.gov."""
    out = []
    # Landing
    landing = await probe(client, "ohio_voter_landing", "https://voterlookup.ohiosos.gov/voterlookup.aspx")
    out.append(landing)
    if not landing.ok:
        return out

    # ASP.NET WebForms — extract viewstate + event-validation, then POST search.
    soup = BeautifulSoup(Path(REPO / landing.artifact).read_text(encoding="utf-8"), "html.parser")
    vs = {
        "__VIEWSTATE": (soup.find("input", {"name": "__VIEWSTATE"}) or {}).get("value", ""),
        "__VIEWSTATEGENERATOR": (soup.find("input", {"name": "__VIEWSTATEGENERATOR"}) or {}).get("value", ""),
        "__EVENTVALIDATION": (soup.find("input", {"name": "__EVENTVALIDATION"}) or {}).get("value", ""),
    }
    if not any(vs.values()):
        landing.error = "no asp.net viewstate found — page shape changed"
        return out

    # Field names are the standard Ohio SOS voter-lookup ones.
    form = {
        **vs,
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "ctl00$MainContent$ddlCounty": "Franklin",
        "ctl00$MainContent$txtFirstName": "Aaron",
        "ctl00$MainContent$txtLastName": "Tom",
        "ctl00$MainContent$btnSearch": "Search",
    }
    p = await probe(client, "ohio_voter_search_aaron_tom_franklin",
                    "https://voterlookup.ohiosos.gov/voterlookup.aspx",
                    post_data=urlencode(form))
    out.append(p)

    if p.ok:
        soup2 = BeautifulSoup(Path(REPO / p.artifact).read_text(encoding="utf-8"), "html.parser")
        rows = soup2.select("table tr")
        for r in rows[:20]:
            txt = r.get_text(" ", strip=True)
            if "Tom" in txt and len(txt) < 400:
                p.findings.append(txt)

    # Also try statewide (no county) in case Franklin filter is too narrow.
    form2 = {
        **vs,
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "ctl00$MainContent$ddlCounty": "",
        "ctl00$MainContent$txtFirstName": "Aaron",
        "ctl00$MainContent$txtLastName": "Tom",
        "ctl00$MainContent$btnSearch": "Search",
    }
    out.append(await probe(client, "ohio_voter_search_aaron_tom_statewide",
                           "https://voterlookup.ohiosos.gov/voterlookup.aspx",
                           post_data=urlencode(form2)))
    return out


async def fastpeoplesearch(client: httpx.AsyncClient) -> list[Probe]:
    out = []
    urls = [
        ("fastpeoplesearch_aaron_tom_oh",
         "https://www.fastpeoplesearch.com/name/aaron-tom_oh"),
        ("fastpeoplesearch_aaron_tom_hilliard",
         "https://www.fastpeoplesearch.com/name/aaron-tom_hilliard-oh"),
        ("fastpeoplesearch_aaron_tom_dublin",
         "https://www.fastpeoplesearch.com/name/aaron-tom_dublin-oh"),
    ]
    for name, url in urls:
        p = await probe(client, name, url)
        out.append(p)
        if p.ok:
            soup = BeautifulSoup(Path(REPO / p.artifact).read_text(encoding="utf-8"), "html.parser")
            for card in soup.select(".card-block, .people-list-card, .person-info"):
                txt = card.get_text(" ", strip=True)[:500]
                if "Aaron" in txt and "Tom" in txt:
                    p.findings.append(txt)
            # generic age + city heuristic
            for el in soup.find_all(string=re.compile(r"\b(Aaron .* Tom|Tom, Aaron)\b", re.I)):
                t = el.strip()[:250]
                if t and t not in p.findings:
                    p.findings.append(t)
    return out


async def truepeoplesearch(client: httpx.AsyncClient) -> list[Probe]:
    out = []
    urls = [
        ("truepeoplesearch_aaron_tom_oh",
         "https://www.truepeoplesearch.com/results?name=Aaron%20Tom&citystatezip=OH"),
        ("truepeoplesearch_aaron_tom_hilliard",
         "https://www.truepeoplesearch.com/results?name=Aaron%20Tom&citystatezip=Hilliard%2C%20OH"),
        ("truepeoplesearch_aaron_tom_franklin",
         "https://www.truepeoplesearch.com/results?name=Aaron%20Tom&citystatezip=Franklin%20County%2C%20OH"),
    ]
    for name, url in urls:
        p = await probe(client, name, url)
        out.append(p)
        if p.ok:
            soup = BeautifulSoup(Path(REPO / p.artifact).read_text(encoding="utf-8"), "html.parser")
            for card in soup.select(".card-summary, .card"):
                txt = card.get_text(" ", strip=True)[:600]
                if "Aaron" in txt and "Tom" in txt:
                    p.findings.append(txt)
    return out


async def legacy(client: httpx.AsyncClient) -> list[Probe]:
    out = []
    urls = [
        ("legacy_aaron_tom_oh",
         "https://www.legacy.com/us/obituaries/search?firstname=Aaron&lastname=Tom&location=Ohio&country=United+States+of+America"),
        ("legacy_tom_family_franklin",
         "https://www.legacy.com/us/obituaries/search?firstname=&lastname=Tom&location=Franklin%2C+OH&country=United+States+of+America"),
    ]
    for name, url in urls:
        p = await probe(client, name, url)
        out.append(p)
        if p.ok:
            soup = BeautifulSoup(Path(REPO / p.artifact).read_text(encoding="utf-8"), "html.parser")
            for card in soup.select("[data-component='ObituaryCard'], .obituary-card, article")[:20]:
                txt = card.get_text(" ", strip=True)[:500]
                if "Tom" in txt:
                    p.findings.append(txt)
    return out


async def auditor_addresses(client: httpx.AsyncClient) -> list[Probe]:
    """Re-run the auditor street searches against current Aaron Tom records."""
    out = []
    # The Franklin auditor is not Cloudflare-blocked but worth re-running with
    # FlareSolverr too for consistency. We just hit the basic owner search.
    p = await probe(client, "franklin_auditor_owner_tom_aaron",
                    "https://property.franklincountyauditor.com/_web/search/CommonSearch.aspx?mode=OWNER")
    out.append(p)
    return out


async def main():
    async with httpx.AsyncClient() as client:
        # confirm FlareSolverr health
        h = await client.get("http://flaresolverr:8191/", timeout=10)
        print(f"[flaresolverr] {h.json().get('msg')}")

        all_probes: list[Probe] = []
        for label, fn in [
            ("ohio_sos", ohio_sos),
            ("ohio_voter", ohio_voter),
            ("fastpeoplesearch", fastpeoplesearch),
            ("truepeoplesearch", truepeoplesearch),
            ("legacy", legacy),
            ("auditor", auditor_addresses),
        ]:
            print(f"\n=== {label} ===")
            try:
                results = await fn(client)
            except Exception as exc:
                results = [Probe(name=label, url="", error=f"driver crashed: {type(exc).__name__}: {exc}")]
            for p in results:
                flag = "✅" if p.ok else "❌"
                print(f"  {flag} {p.name:<55} http={p.http_status} chars={p.chars} ms={p.elapsed_ms} {('err='+p.error) if p.error else ''}")
                if p.findings:
                    for f in p.findings[:5]:
                        print(f"      • {f[:220]}")
                all_probes.append(p)

        # Markdown report
        lines = []
        lines.append("# PHA-787 round 3 — FlareSolverr unblock pass\n")
        lines.append(f"- Subject: {SUBJECT} (DOB {SUBJECT_DOB})")
        lines.append(f"- Run: {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
        lines.append(f"- FlareSolverr: phattvip://flaresolverr:8191 (v3.4.6)\n")
        ok = sum(1 for p in all_probes if p.ok)
        lines.append(f"**{len(all_probes)} probes — {ok} returned real content past challenge.**\n")
        lines.append("| Probe | HTTP | Chars | ms | OK | Note |")
        lines.append("|---|---|---|---|---|---|")
        for p in all_probes:
            note = (p.error or (f"{len(p.findings)} findings" if p.findings else ""))[:140].replace("|", "/")
            lines.append(f"| {p.name} | {p.http_status} | {p.chars} | {p.elapsed_ms} | {'✅' if p.ok else '❌'} | {note} |")
        lines.append("\n## Findings\n")
        for p in all_probes:
            if p.findings:
                lines.append(f"\n### {p.name}")
                lines.append(f"`{p.url}` → `{p.artifact}`")
                for f in p.findings[:20]:
                    lines.append(f"- {f[:500]}")
        report = "\n".join(lines)
        (REPO / "data" / "pha787_round3.md").write_text(report, encoding="utf-8")
        (REPO / "data" / "pha787_round3.json").write_text(
            json.dumps([asdict(p) for p in all_probes], indent=2), encoding="utf-8")
        print("\n--- REPORT ---")
        print(report)
        print(f"\n[wrote data/pha787_round3.md, data/pha787_round3.json, {len(all_probes)} HTML artifacts under data/pha787_round3/]")


if __name__ == "__main__":
    asyncio.run(main())
