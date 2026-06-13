"""
PHA-820 live integration driver for the TruePeopleSearch DataDome scraper.

Tests the full solve flow end-to-end:
  1. Confirm challenge is present on the results URL
  2. Solve via 2Captcha + residential-IP proxy
  3. Parse JSON-LD Person records from the result page

Usage:
    cd obsessed
    TWOCAPTCHA_API_KEY=<key> DATADOME_SOLVE_PROXY=user:pass@HOST:PORT \
        .venv/bin/python scripts/pha820_tps_live.py

DATADOME_SOLVE_PROXY must be an HTTP forward proxy running on PHATT-RAID's
Breezeline IP (23.245.109.252). See docker/proxy/ for the proxy image.

This burns ~$0.003 (one DataDome solve at $3/1000). Do NOT wire into CI.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.config import settings
from app.services.scraper import truepeoplesearch
from app.services.scraper.truepeoplesearch import (
    DataDomeChallengePresent,
    DataDomeProxyNotConfigured,
    DataDomeSolveLimitReached,
    _fetch,
    _is_dd_challenge,
)


def _sep(title: str) -> None:
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


async def check_challenge_present() -> bool:
    """Confirm the results endpoint returns a DataDome challenge without a cookie."""
    _sep("Step 1 — confirm DataDome challenge on results endpoint")
    url = "https://www.truepeoplesearch.com/results?name=John%20Smith&citystatezip=Columbus%2C%20OH"
    print(f"  GET {url}")
    html, status = await _fetch(url)
    if _is_dd_challenge(html, status):
        print(f"  ✓ challenge present (status={status}, body={len(html)}B)")
        return True
    print(f"  ✗ no challenge (status={status}, body={len(html)}B) — check if TPS changed")
    print("  first 400 chars:")
    print("  " + html[:400])
    return False


async def run_search() -> bool:
    """Run a real search and print results."""
    _sep("Step 2 — search_people with DataDome solve")
    print(f"  proxy: {settings.datadome_solve_proxy!r}")
    t0 = time.time()
    result = await truepeoplesearch.search_people("Aaron", "Tom", city="Hilliard", state="OH")
    elapsed = time.time() - t0
    if result.get("wall"):
        print(f"  ✗ wall encountered in {elapsed:.1f}s: {result['wall']}")
        return False
    people = result.get("people", [])
    print(f"  ✓ {len(people)} person record(s) in {elapsed:.1f}s")
    for p in people[:3]:
        print(f"    - {p.get('name')} | addresses={len(p.get('addresses', []))} | relatives={p.get('relatives', [])[:3]}")
    if not people:
        print("  (no records parsed — page may have loaded but JSON-LD changed)")
        return False
    return True


async def main() -> int:
    key = os.environ.get("TWOCAPTCHA_API_KEY", "").strip()
    proxy = os.environ.get("DATADOME_SOLVE_PROXY", "").strip()
    if not key:
        print("error: set TWOCAPTCHA_API_KEY")
        return 2
    if not proxy:
        print("error: set DATADOME_SOLVE_PROXY=user:pass@HOST:PORT")
        return 2
    settings.twocaptcha_api_key = key
    settings.datadome_solve_proxy = proxy

    ok_challenge = await check_challenge_present()
    if not ok_challenge:
        print("\nWARNING: challenge not present — TPS may have changed or IP is already trusted")

    ok_search = await run_search()
    _sep("Summary")
    print(f"  challenge confirmed:  {'YES' if ok_challenge else 'NO / SKIP'}")
    print(f"  search result:        {'OK' if ok_search else 'FAIL'}")
    return 0 if ok_search else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
