"""PHA-792 live integration driver for app/services/scraper/captcha_solver.py.

Hits 2Captcha for real with each solver method against publicly available test
endpoints and prints the returned token (truncated) plus timing info.

Targets:
- reCAPTCHA v2: Google's official demo page
- Cloudflare Turnstile: 2Captcha's own demo page (sitekey scraped from the page)
- DataDome:     2Captcha's own demo page (captcha-delivery URL scraped from the page)

The 2Captcha key is read from the TWOCAPTCHA_API_KEY env var (and exported into
the in-process Settings so captcha_solver.is_configured() returns True). Run
with:

    cd obsessed && TWOCAPTCHA_API_KEY=<key> .venv/bin/python scripts/pha792_captcha_live.py

This is a money-burning live test (~$0.01-0.03 per solve method). Do not wire
into CI.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

# Make the app package importable when run as a script
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import httpx

from app.config import settings
from app.services.scraper import captcha_solver
from app.services.scraper.captcha_solver import (
    CaptchaSolverError,
    is_configured,
    solve_datadome,
    solve_recaptcha_v2,
    solve_turnstile,
)


def _print_section(title: str):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _summarize_token(token: str, max_chars: int = 80) -> str:
    if len(token) <= max_chars:
        return token
    return f"{token[:max_chars]}... (total len={len(token)})"


async def _scrape_2captcha_demo(demo_url: str, key_attr: str) -> tuple[str, str]:
    """Return (page_html, extracted_key) — used for Turnstile + DataDome demos."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(demo_url, headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text
    match = re.search(rf'{key_attr}=["\']([^"\']+)["\']', html)
    if not match:
        raise RuntimeError(f"Could not find {key_attr} on {demo_url}")
    return html, match.group(1)


async def test_recaptcha_v2():
    _print_section("reCAPTCHA v2 (Google demo)")
    site_key = "6LeIxAcTAAAAAJcZVRqyHh71UMIEGNQ_MXjiZKhI"
    page_url = "https://www.google.com/recaptcha/api2/demo"
    print(f"sitekey: {site_key}")
    print(f"pageurl: {page_url}")
    t0 = time.time()
    try:
        token = await solve_recaptcha_v2(site_key, page_url)
        elapsed = time.time() - t0
        print(f"OK in {elapsed:.1f}s")
        print(f"token: {_summarize_token(token)}")
        return True
    except CaptchaSolverError as e:
        elapsed = time.time() - t0
        print(f"FAIL in {elapsed:.1f}s: {e}")
        return False


async def test_turnstile():
    _print_section("Cloudflare Turnstile (2Captcha demo)")
    demo_url = "https://2captcha.com/demo/cloudflare-turnstile"
    print(f"fetching demo page: {demo_url}")
    try:
        _, site_key = await _scrape_2captcha_demo(demo_url, "data-sitekey")
    except RuntimeError as e:
        print(f"FAIL extracting sitekey: {e}")
        return False
    print(f"sitekey: {site_key}")
    t0 = time.time()
    try:
        token = await solve_turnstile(site_key, demo_url)
        elapsed = time.time() - t0
        print(f"OK in {elapsed:.1f}s")
        print(f"token: {_summarize_token(token)}")
        return True
    except CaptchaSolverError as e:
        elapsed = time.time() - t0
        print(f"FAIL in {elapsed:.1f}s: {e}")
        return False


async def test_datadome():
    """Trigger a real DataDome challenge against leboncoin.fr and solve it.

    The 2Captcha-hosted demo (https://2captcha.com/demo/datadome) 404s as of
    PHA-792 verification, so we exercise the path against a known DD-protected
    site instead. leboncoin.fr 403s plain HTTP requests with a DD challenge,
    embedding the `dd={cid,hsh,...,host}` JS object — we parse it into the
    captcha_url 2Captcha expects.
    """
    _print_section("DataDome (leboncoin.fr — real DD challenge)")
    page_url = "https://leboncoin.fr/"
    ua = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    print(f"fetching DD-walled page: {page_url}")
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(page_url, headers={"User-Agent": ua})
        html = resp.text
    print(f"  status: {resp.status_code}  (expect 403 with DD challenge)")

    # Pull cid/hsh/host out of the inline dd={...} object
    cid_m  = re.search(r"'cid':'([^']+)'",  html)
    hsh_m  = re.search(r"'hsh':'([^']+)'",  html)
    host_m = re.search(r"'host':'([^']+)'", html)
    s_m    = re.search(r"'s':(\d+)",        html)
    e_m    = re.search(r"'e':'([^']+)'",    html)
    t_m    = re.search(r"'t':'([^']+)'",    html)
    if not (cid_m and hsh_m and host_m):
        print("FAIL: page didn't include a DD challenge (no cid/hsh/host).")
        print("first 500 chars:")
        print(html[:500])
        return False

    cid, hsh, host = cid_m.group(1), hsh_m.group(1), host_m.group(1)
    s_val = s_m.group(1) if s_m else "0"
    e_val = e_m.group(1) if e_m else ""
    t_val = t_m.group(1) if t_m else "fe"
    captcha_url = (
        f"https://{host}/captcha/?initialCid={cid}"
        f"&hash={hsh}&cid={cid}&t={t_val}&referer={page_url}&s={s_val}&e={e_val}"
    )
    print(f"  captcha_url: {captcha_url[:140]}{'...' if len(captcha_url) > 140 else ''}")

    proxy = os.environ.get("CAPTCHA_TEST_PROXY", "").strip()
    if not proxy:
        print(
            "SKIP: set CAPTCHA_TEST_PROXY=IP:PORT (HTTP) — 2Captcha requires "
            "DataDome solves to come from the originating IP via a proxy."
        )
        # We still exercise the validation path: shim should reject empty proxy
        # without hitting 2Captcha.
        try:
            await solve_datadome(captcha_url, page_url, proxy="", user_agent=ua)
            print("FAIL: shim accepted empty proxy")
            return False
        except CaptchaSolverError as e:
            assert "proxy" in str(e).lower()
            print(f"  shim correctly rejects empty proxy: {e}")
            return None  # neither pass nor fail — skipped

    proxy_type = os.environ.get("CAPTCHA_TEST_PROXY_TYPE", "HTTP")
    t0 = time.time()
    try:
        token = await solve_datadome(
            captcha_url, page_url,
            proxy=proxy, proxytype=proxy_type, user_agent=ua,
        )
        elapsed = time.time() - t0
        print(f"OK in {elapsed:.1f}s")
        print(f"token: {_summarize_token(token)}")
        return True
    except CaptchaSolverError as e:
        elapsed = time.time() - t0
        print(f"FAIL in {elapsed:.1f}s: {e}")
        return False


async def check_balance():
    _print_section("2Captcha balance pre-flight")
    api_key = settings.twocaptcha_api_key
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            "https://2captcha.com/res.php",
            params={"key": api_key, "action": "getbalance", "json": 1},
        )
        data = resp.json()
    if data.get("status") == 1:
        print(f"OK — balance: ${data['request']}")
        return True
    print(f"FAIL — {data}")
    return False


async def main() -> int:
    # Bind the env var into the in-process Settings (since settings was already
    # constructed at import time before we read the env).
    key = os.environ.get("TWOCAPTCHA_API_KEY", "").strip()
    if not key:
        print("error: set TWOCAPTCHA_API_KEY in the environment first.")
        return 2
    settings.twocaptcha_api_key = key
    assert is_configured()

    ok_balance = await check_balance()
    if not ok_balance:
        return 1

    results = []
    results.append(("reCAPTCHA v2", await test_recaptcha_v2()))
    results.append(("Turnstile",    await test_turnstile()))
    results.append(("DataDome",     await test_datadome()))

    _print_section("Summary")
    for name, ok in results:
        if ok is None:
            label = "SKIP"
        elif ok:
            label = "OK  "
        else:
            label = "FAIL"
        print(f"  {label}  {name}")

    # SKIP counts as a soft pass for exit purposes — the live test infra simply
    # wasn't available, the shim wiring itself was still exercised.
    return 0 if all(ok is not False for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
