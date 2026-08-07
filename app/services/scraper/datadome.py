"""
Shared DataDome solve core (PHA-820).

Several data-bearing endpoints Obsessed wants are path-dependent behind DataDome:
homepages and warm sessions return clean HTML, but the data routes (search
results, obituary detail, partner-affiliate listings) intermittently answer with
a 403 challenge stub (`var dd={...}` JS + a `geo.captcha-delivery.com` iframe).
The challenge does not auto-clear — a residential IP + real browser is necessary
but not sufficient.

This module factors the solve machinery that `truepeoplesearch.py` proved out so
the Legacy.com / dispatch.com obituary scrapers can reuse it instead of copying
the same regex/2Captcha dance three times.

Standalone-safe by design:
  - DATADOME_SOLVE_PROXY unset (the default) -> `fetch_with_solve` raises
    `DataDomeProxyNotConfigured` the moment a challenge is hit, so every caller
    degrades to a typed wall dict and zero paid solves happen.
  - A process-wide solve cap (DATADOME_MAX_SOLVES_PER_RUN) and a per-domain
    cookie cache keep the 2Captcha balance from draining on retries.

To activate:
  TWOCAPTCHA_API_KEY   — 2captcha.com solver key
  DATADOME_SOLVE_PROXY — proxy URL that egresses the SAME residential IP as this
                         container (USER:PASS@HOST:PORT or HOST:PORT; scheme
                         optional). DataDome binds the solved cookie to the IP
                         that solved it, so ALL requests (solve + data fetch)
                         route through this one proxy.

On PHATT-RAID the existing privoxy inside the deluge container works:
  DATADOME_SOLVE_PROXY=10.0.0.100:8118   (egresses via AirVPN)
The AirVPN exit IP must be stable across the solve + retry cycle; the in-process
cookie cache limits how often a re-solve is needed.
"""

from __future__ import annotations

import re
import time
from typing import Optional
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.services.scraper import captcha_solver


_REQUEST_TIMEOUT = 30.0
_COOKIE_TTL = 12 * 3600  # 12h; DataDome cookies usually live 24h–7d

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.6367.118 Safari/537.36"
)
_DEFAULT_HEADERS = {
    "User-Agent": _DEFAULT_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# In-process DataDome cookie cache keyed by registrable domain.
_cookie_cache: dict[str, tuple[str, float]] = {}

# Process-wide solve counter (resets on restart) — shared across every DataDome
# site so the per-run cap protects the whole 2Captcha balance, not one domain.
_solves_this_run: int = 0

_DD_CHALLENGE_RE = re.compile(
    r"var\s+dd\s*=\s*\{|geo\.captcha-delivery\.com|datadome\.co/captcha",
    re.IGNORECASE,
)
_DD_OBJECT_RE = re.compile(r"var\s+dd\s*=\s*\{([^}]+)\}", re.IGNORECASE | re.DOTALL)
# Generic (non-DataDome) anti-bot block — usually Cloudflare's JS interstitial.
_GENERIC_BLOCK_RE = re.compile(
    r"Just a moment\.\.\.|cf-browser-verification|/cdn-cgi/challenge|"
    r"challenge-platform|Attention Required",
    re.IGNORECASE,
)


class DataDomeError(Exception):
    """Base for DataDome solve-path failures."""


class DataDomeProxyNotConfigured(DataDomeError):
    """DATADOME_SOLVE_PROXY is unset but a challenge was hit."""


class DataDomeSolveLimitReached(DataDomeError):
    """Per-run solve cap exhausted — prevents 2Captcha balance drain."""


class DataDomeChallengePresent(DataDomeError):
    """Page is still a challenge after solving (likely an IP mismatch)."""


class UpstreamBlocked(DataDomeError):
    """A non-DataDome block (Cloudflare interstitial / 403 / 429 / 503).

    The DataDome 2Captcha solve doesn't apply; callers should escalate to a
    rendered fetch (crawl4ai) rather than treat the body as data.
    """


def is_configured() -> bool:
    """True when both the 2Captcha key and the same-IP proxy are set."""
    return captcha_solver.is_configured() and bool(
        (settings.datadome_solve_proxy or "").strip()
    )


def reset_run_state() -> None:
    """Clear the cookie cache and solve counter (used by tests)."""
    global _solves_this_run
    _solves_this_run = 0
    _cookie_cache.clear()


def is_challenge(html: str, status: int) -> bool:
    """True if the response looks like a DataDome challenge page."""
    if not html:
        return False
    if status == 403 and _DD_CHALLENGE_RE.search(html):
        return True
    # status 200 but body is the tiny DD stub (<8 KB)
    if len(html) < 8192 and _DD_CHALLENGE_RE.search(html):
        return True
    return False


def is_generic_block(html: str, status: int) -> bool:
    """True for a non-DataDome anti-bot block (Cloudflare JS / 403 / 429 / 503)."""
    if status in (403, 429, 503):
        return True
    return bool(_GENERIC_BLOCK_RE.search(html or ""))


def _extract_dd_params(html: str) -> Optional[dict]:
    """Parse the inline `dd={cid, hsh, host, t, s, ...}` object from the body.

    Handles single- or double-quoted JS literals. Returns None when the object
    is absent or missing the cid/hsh the captcha URL needs.
    """
    m = _DD_OBJECT_RE.search(html)
    if not m:
        return None
    body = m.group(0)
    params: dict[str, str] = {}
    for fm in re.finditer(r'["\'](\w+)["\']\s*:\s*["\']([^"\']*)["\']', body):
        params[fm.group(1)] = fm.group(2)
    for fm in re.finditer(r'["\'](\w+)["\']\s*:\s*(\d+)', body):
        params.setdefault(fm.group(1), fm.group(2))
    return params if ("cid" in params and "hsh" in params) else None


def _build_captcha_url(dd: dict, referer: str) -> str:
    """Build the geo.captcha-delivery.com URL 2Captcha expects."""
    host = dd.get("host", "geo.captcha-delivery.com")
    cid = dd["cid"]
    hsh = dd["hsh"]
    t = dd.get("t", "fe")
    s = dd.get("s", "0")
    return (
        f"https://{host}/captcha/?initialCid={cid}&hash={hsh}&cid={cid}"
        f"&t={t}&s={s}&referer={referer}"
    )


def _proxy_url() -> Optional[str]:
    """HTTP proxy URL for all DataDome-site requests, or None when unconfigured.

    When set, both the 2Captcha solve and the data fetch route through it so a
    single exit IP is used — DataDome binds the solved cookie to the solving IP.
    """
    raw = (settings.datadome_solve_proxy or "").strip()
    if not raw:
        return None
    return raw if "://" in raw else f"http://{raw}"


def _registrable_domain(url: str) -> str:
    """Host stripped to its last two labels (cookie cache key)."""
    host = (urlsplit(url).hostname or "").lower()
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _get_cached_cookie(domain: str) -> Optional[str]:
    entry = _cookie_cache.get(domain)
    if entry is None:
        return None
    value, expires_at = entry
    if time.monotonic() > expires_at:
        del _cookie_cache[domain]
        return None
    return value


def _cache_cookie(domain: str, value: str) -> None:
    _cookie_cache[domain] = (value, time.monotonic() + _COOKIE_TTL)


async def _solve(captcha_url: str, page_url: str, user_agent: str) -> str:
    """Solve via 2Captcha, applying the per-run cap and the same-IP proxy."""
    global _solves_this_run
    max_solves = settings.datadome_max_solves_per_run
    if _solves_this_run >= max_solves:
        raise DataDomeSolveLimitReached(
            f"DataDome solve cap reached ({max_solves} per run); "
            "restart the process or raise DATADOME_MAX_SOLVES_PER_RUN"
        )
    proxy = (settings.datadome_solve_proxy or "").strip()
    if not proxy:
        raise DataDomeProxyNotConfigured(
            "DATADOME_SOLVE_PROXY is not configured. Set it to USER:PASS@HOST:PORT "
            "pointing to an HTTP forward proxy that egresses from the same "
            "residential IP as the scraper."
        )
    _solves_this_run += 1
    return await captcha_solver.solve_datadome(
        captcha_url, page_url, proxy=proxy, proxytype="HTTP", user_agent=user_agent
    )


async def _fetch(
    url: str,
    *,
    headers: dict,
    datadome_cookie: Optional[str] = None,
) -> tuple[str, int]:
    cookies: dict[str, str] = {}
    if datadome_cookie:
        cookies["datadome"] = datadome_cookie
    async with httpx.AsyncClient(
        timeout=_REQUEST_TIMEOUT,
        follow_redirects=True,
        headers=headers,
        proxy=_proxy_url(),
    ) as client:
        resp = await client.get(url, cookies=cookies)
        return resp.text, resp.status_code


async def fetch_with_solve(
    url: str,
    *,
    headers: Optional[dict] = None,
) -> tuple[str, int]:
    """Fetch `url`, transparently solving a DataDome challenge if one appears.

    Order: cached cookie -> bare fetch -> on challenge, solve + retry.

    Raises:
        DataDomeProxyNotConfigured  — proxy unset and a challenge was hit.
        DataDomeSolveLimitReached   — per-run solve cap exhausted.
        DataDomeChallengePresent    — still challenged after solving (IP mismatch),
                                      or the dd object could not be parsed.
        captcha_solver.CaptchaSolverNotConfigured / CaptchaSolverError — from 2Captcha.
    """
    hdrs = dict(headers or _DEFAULT_HEADERS)
    ua = hdrs.get("User-Agent", _DEFAULT_UA)
    domain = _registrable_domain(url)

    cached = _get_cached_cookie(domain)
    if cached:
        html, status = await _fetch(url, headers=hdrs, datadome_cookie=cached)
        if not is_challenge(html, status):
            if is_generic_block(html, status):
                raise UpstreamBlocked(f"non-DataDome block on {url} (status {status})")
            return html, status
        _cookie_cache.pop(domain, None)  # stale cookie — drop and re-solve

    html, status = await _fetch(url, headers=hdrs)
    if not is_challenge(html, status):
        # Not DataDome — but a Cloudflare interstitial / 4xx-5xx block must not be
        # mistaken for empty data. Surface it so callers can escalate to a render.
        if is_generic_block(html, status):
            raise UpstreamBlocked(f"non-DataDome block on {url} (status {status})")
        return html, status

    dd_params = _extract_dd_params(html)
    if not dd_params:
        raise DataDomeChallengePresent(
            f"DataDome challenge on {url} but could not parse dd params from body"
        )
    captcha_url = _build_captcha_url(dd_params, url)
    solved = await _solve(captcha_url, url, ua)
    _cache_cookie(domain, solved)

    html, status = await _fetch(url, headers=hdrs, datadome_cookie=solved)
    if is_challenge(html, status):
        raise DataDomeChallengePresent(
            f"DataDome challenge remained on {url} after solve — cookie may be an "
            "IP mismatch; check that DATADOME_SOLVE_PROXY egresses the same IP as "
            "this container"
        )
    return html, status


def wall_for(exc: Exception) -> Optional[dict]:
    """Map a solve-path exception to a typed wall dict, or None if it's not one.

    Lets every caller share one `except DataDomeError / CaptchaSolverError` block:

        try:
            html, _ = await datadome.fetch_with_solve(url)
        except (datadome.DataDomeError, CaptchaSolverError) as e:
            wall = datadome.wall_for(e)
    """
    kind = {
        DataDomeProxyNotConfigured: "datadome_proxy_missing",
        DataDomeSolveLimitReached: "datadome_solve_limit",
        DataDomeChallengePresent: "datadome_challenge",
        UpstreamBlocked: "upstream_blocked",
        captcha_solver.CaptchaSolverNotConfigured: "captcha_not_configured",
    }.get(type(exc))
    if kind is None:
        if isinstance(exc, captcha_solver.CaptchaSolverError):
            kind = "captcha_error"
        elif isinstance(exc, DataDomeError):
            kind = "datadome_challenge"
        else:
            return None
    return {"kind": kind, "detail": str(exc)}
