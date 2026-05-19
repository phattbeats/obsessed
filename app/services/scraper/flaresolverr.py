"""
FlareSolverr proxy client — routes HTTP requests through a FlareSolverr instance
to bypass Cloudflare and other browser-based anti-bot walls.

Usage:
    from app.services.scraper.flaresolverr import fs_get, fs_post

    html, status = fs_get("https://example.com")
    html, status = fs_post("https://example.com/login", post_data={"username": "a"})

FlareSolverr must be reachable at FLARESOLVERR_URL (default: http://10.0.0.100:8191).
Responses with status >= 500 are retried once after a short backoff.
"""

import httpx, asyncio, time
from typing import Optional

FLARESOLVERR_URL = "http://10.0.0.100:8191"
_REQUEST_TIMEOUT = 60.0  # FlareSolverr handles browser startup internally


class FlareSolverrError(Exception):
    """Raised when FlareSolverr returns a non-success response."""
    def __init__(self, msg: str, http_status: int = 0):
        super().__init__(msg)
        self.http_status = http_status


class CloudflareWallError(FlareSolverrError):
    """Raised when FlareSolverr detects a Cloudflare challenge page."""
    pass


async def fs_get(url: str, *, max_timeout: float = 90.0) -> tuple[str, int]:
    """
    GET a URL through FlareSolverr. Returns (html, http_status).

    Raises:
        CloudflareWallError — FlareSolverr returned a challenge page
        FlareSolverrError — request failed or timed out
    """
    return await _fs_request("GET", url, max_timeout=max_timeout)


async def fs_post(url: str, post_data: Optional[dict] = None, *, max_timeout: float = 90.0) -> tuple[str, int]:
    """
    POST to a URL through FlareSolverr. Returns (html, http_status).

    Args:
        url: target URL
        post_data: form data dict (application/x-www-form-urlencoded)

    Raises:
        CloudflareWallError — FlareSolverr returned a challenge page
        FlareSolverrError — request failed or timed out
    """
    return await _fs_request("POST", url, post_data=post_data, max_timeout=max_timeout)


async def _fs_request(
    method: str,
    url: str,
    post_data: Optional[dict] = None,
    max_timeout: float = 90.0,
) -> tuple[str, int]:
    """Shared request helper with one automatic retry on 5xx."""
    for attempt in range(2):
        try:
            html, status = await _single_request(method, url, post_data, max_timeout)
            if status >= 500 and attempt == 0:
                # Transient server error — retry once after brief backoff
                await asyncio.sleep(1.5)
                continue
            return html, status
        except asyncio.TimeoutError:
            if attempt == 0:
                await asyncio.sleep(2)
                continue
            raise FlareSolverrError(f"FlareSolverr timed out after {max_timeout}s", 0)
    # Should not reach here; second attempt already raised
    raise FlareSolverrError("Exhausted retries", 0)


async def _single_request(
    method: str,
    url: str,
    post_data: Optional[dict] = None,
    max_timeout: float = 90.0,
) -> tuple[str, int]:
    body = {
        "url": url,
        "maxTimeout": int(max_timeout * 1000),
    }
    if post_data is not None:
        body["postData"] = _url_encode(post_data)
        body["requestMethod"] = "post"
    else:
        body["requestMethod"] = "get"

    async with httpx.AsyncClient(timeout=httpx.Timeout(max_timeout + 10, connect=10)) as client:
        resp = await client.post(f"{FLARESOLVERR_URL}/v1", json=body)
        data = resp.json()

    status = data.get("status", 0)
    solution = data.get("solution", {})

    # Detect Cloudflare challenge pages from response content
    html = solution.get("response", "") or ""
    if _is_cf_challenge(html):
        raise CloudflareWallError(
            f"Cloudflare challenge still present in response for {url}", status
        )

    if status != "ok":
        raise FlareSolverrError(
            f"FlareSolverr returned status={status}: {data.get('message', data)}", status
        )

    return html, status


def _url_encode(data: dict) -> str:
    return "&".join(f"{_quote(k)}={_quote(v)}" for k, v in data.items())


def _quote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(str(s), safe="")


def _is_cf_challenge(html: str) -> bool:
    """Return True if the response body looks like a Cloudflare challenge page."""
    html_lower = html.lower()
    return (
        "cloudflare" in html_lower
        and ("challenge" in html_lower or "ray id" in html_lower)
    ) or "chk" in html  # Cloudflare ray ID cookie param