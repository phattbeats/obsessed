"""
2Captcha solver shim — for the walls FlareSolverr can't break.

Confirmed walls and which solver method to use:

| Wall                       | Captcha type        | Solver method                    |
|----------------------------|---------------------|----------------------------------|
| Ohio Voter Lookup          | Google reCAPTCHA v2 | solve_recaptcha_v2(site_key, url)|
| TruePeopleSearch           | DataDome            | solve_datadome(captcha_url, url, proxy=...) |
| Legacy.com obituaries      | DataDome            | solve_datadome(captcha_url, url, proxy=...) |
| FastPeopleSearch detail    | Cloudflare Turnstile| solve_turnstile(site_key, url)   |

Cost: ~$2 per 1000 reCAPTCHA solves, ~$3 per 1000 DataDome solves.

DataDome requires an HTTP/SOCKS proxy reachable from 2Captcha — DD invalidates
solves that come from a different IP than the one that originally triggered
the challenge. reCAPTCHA v2 and Turnstile do not require a proxy.

The API key is read from `settings.twocaptcha_api_key` (env: TWOCAPTCHA_API_KEY).
When unset, every solve_* raises `CaptchaSolverNotConfigured` so callers can
short-circuit instead of looping into a paid solve attempt.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from app.config import settings


TWOCAPTCHA_IN = "https://2captcha.com/in.php"
TWOCAPTCHA_RES = "https://2captcha.com/res.php"

_POLL_INTERVAL = 5.0   # 2Captcha guidance: don't poll faster than every 5s
_POLL_MAX = 120.0      # give up after 2 minutes — reCAPTCHA usually solves in 15-45s


class CaptchaSolverError(Exception):
    """Raised when 2Captcha returns an error or polling exceeds timeout."""


class CaptchaSolverNotConfigured(CaptchaSolverError):
    """Raised when TWOCAPTCHA_API_KEY is empty — caller should skip solving."""


def _require_key() -> str:
    key = (settings.twocaptcha_api_key or "").strip()
    if not key:
        raise CaptchaSolverNotConfigured(
            "TWOCAPTCHA_API_KEY is empty; set it in .env to enable captcha solving"
        )
    return key


async def solve_recaptcha_v2(site_key: str, page_url: str) -> str:
    """Submit a reCAPTCHA v2 task and return the g-recaptcha-response token.

    Use case: Ohio Voter Lookup form (site_key is in the page HTML as
    `data-sitekey="..."` on the `<div class="g-recaptcha">`).
    """
    params = {
        "key": _require_key(),
        "method": "userrecaptcha",
        "googlekey": site_key,
        "pageurl": page_url,
        "json": 1,
    }
    return await _submit_and_poll(params)


async def solve_datadome(
    captcha_url: str,
    page_url: str,
    *,
    proxy: str,
    proxytype: str = "HTTP",
    user_agent: Optional[str] = None,
) -> str:
    """Submit a DataDome captcha and return the cookie payload.

    `captcha_url` is the URL DataDome surfaces in its challenge iframe
    (the `geo.captcha-delivery.com/...` link). The returned token is the
    value to write into the `datadome` cookie before retrying the request.

    2Captcha requires that DataDome solves go through the *caller's* proxy so
    the captcha is solved from the same IP that triggered the challenge — DD
    invalidates the solve otherwise. `proxy` must be `IP:PORT` or
    `LOGIN:PASS@IP:PORT`; `proxytype` is one of HTTP / HTTPS / SOCKS4 / SOCKS5.

    Use case: TruePeopleSearch, Legacy.com obituaries, leboncoin.fr.
    """
    if not proxy:
        raise CaptchaSolverError(
            "solve_datadome requires `proxy=IP:PORT` (or LOGIN:PASS@IP:PORT) — "
            "2Captcha will not solve DataDome without one because DD invalidates "
            "solves from a different IP than the one that triggered the challenge"
        )
    params: dict[str, object] = {
        "key": _require_key(),
        "method": "datadome",
        "captcha_url": captcha_url,
        "pageurl": page_url,
        "proxy": proxy,
        "proxytype": proxytype,
        "json": 1,
    }
    if user_agent:
        params["userAgent"] = user_agent
    return await _submit_and_poll(params)


async def solve_turnstile(site_key: str, page_url: str, action: Optional[str] = None) -> str:
    """Submit a Cloudflare Turnstile task and return the token.

    Use case: FastPeopleSearch detail pages (FlareSolverr times out at 90s
    on 'elevated' Turnstile variants — 2Captcha tends to clear them).
    """
    params: dict[str, object] = {
        "key": _require_key(),
        "method": "turnstile",
        "sitekey": site_key,
        "pageurl": page_url,
        "json": 1,
    }
    if action:
        params["action"] = action
    return await _submit_and_poll(params)


async def _submit_and_poll(submit_params: dict) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        submit = await client.get(TWOCAPTCHA_IN, params=submit_params)
        submit_data = submit.json()
        if submit_data.get("status") != 1:
            raise CaptchaSolverError(
                f"2Captcha submit failed: {submit_data.get('request', submit_data)}"
            )
        request_id = submit_data["request"]

        api_key = submit_params["key"]
        deadline = asyncio.get_event_loop().time() + _POLL_MAX
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            poll = await client.get(
                TWOCAPTCHA_RES,
                params={"key": api_key, "action": "get", "id": request_id, "json": 1},
            )
            poll_data = poll.json()
            if poll_data.get("status") == 1:
                return poll_data["request"]
            if poll_data.get("request") != "CAPCHA_NOT_READY":
                raise CaptchaSolverError(
                    f"2Captcha poll failed: {poll_data.get('request', poll_data)}"
                )

    raise CaptchaSolverError(f"2Captcha poll timed out after {_POLL_MAX}s")


def is_configured() -> bool:
    """Lightweight check callers can use to decide whether to attempt a solve."""
    return bool((settings.twocaptcha_api_key or "").strip())
