"""Tests for the 2Captcha solver shim (PHA-792).

These tests exercise the API surface without hitting the real 2Captcha service —
httpx is patched at module level to return canned in.php / res.php responses.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.scraper import captcha_solver
from app.services.scraper.captcha_solver import (
    CaptchaSolverError,
    CaptchaSolverNotConfigured,
    is_configured,
    solve_datadome,
    solve_recaptcha_v2,
    solve_turnstile,
)


# --- Configuration gating ------------------------------------------------


def test_is_configured_false_when_key_empty():
    with patch.object(captcha_solver.settings, "twocaptcha_api_key", ""):
        assert is_configured() is False


def test_is_configured_true_when_key_set():
    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "abc123"):
        assert is_configured() is True


def test_solve_recaptcha_raises_when_key_missing():
    with patch.object(captcha_solver.settings, "twocaptcha_api_key", ""):
        with pytest.raises(CaptchaSolverNotConfigured):
            asyncio.run(solve_recaptcha_v2("sk", "https://example.com"))


def test_solve_datadome_raises_when_key_missing():
    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "   "):
        with pytest.raises(CaptchaSolverNotConfigured):
            asyncio.run(solve_datadome(
                "https://dd.example/c", "https://example.com", proxy="1.2.3.4:8080",
            ))


def test_solve_datadome_raises_when_proxy_missing():
    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "k"):
        with pytest.raises(captcha_solver.CaptchaSolverError) as exc:
            asyncio.run(solve_datadome("https://dd.example/c", "https://example.com", proxy=""))
    assert "proxy" in str(exc.value).lower()


def test_solve_turnstile_raises_when_key_missing():
    with patch.object(captcha_solver.settings, "twocaptcha_api_key", ""):
        with pytest.raises(CaptchaSolverNotConfigured):
            asyncio.run(solve_turnstile("sk", "https://example.com"))


# --- Submit + poll happy path -------------------------------------------


def _build_mock_client(responses: list[dict]) -> tuple[MagicMock, MagicMock]:
    """Create a fake httpx.AsyncClient whose .get() walks `responses` in order."""
    iter_responses = iter(responses)

    async def fake_get(*args, **kwargs):
        resp = MagicMock()
        resp.json.return_value = next(iter_responses)
        return resp

    client_instance = MagicMock()
    client_instance.get = AsyncMock(side_effect=fake_get)

    async_cm = MagicMock()
    async_cm.__aenter__ = AsyncMock(return_value=client_instance)
    async_cm.__aexit__ = AsyncMock(return_value=None)

    async_client_factory = MagicMock(return_value=async_cm)
    return async_client_factory, client_instance


def test_solve_recaptcha_v2_returns_token_after_polling():
    responses = [
        {"status": 1, "request": "REQ-123"},               # in.php submit
        {"status": 0, "request": "CAPCHA_NOT_READY"},      # res.php poll #1
        {"status": 1, "request": "03AGdBq25_TOKEN_xyz"},   # res.php poll #2 -> ready
    ]
    factory, client = _build_mock_client(responses)

    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "key-xyz"), \
         patch.object(captcha_solver, "_POLL_INTERVAL", 0), \
         patch("app.services.scraper.captcha_solver.httpx.AsyncClient", factory):
        token = asyncio.run(solve_recaptcha_v2("site-key-abc", "https://voter.example/lookup"))

    assert token == "03AGdBq25_TOKEN_xyz"
    # Verify the submit call carried the right params
    submit_call = client.get.call_args_list[0]
    assert submit_call.args[0] == captcha_solver.TWOCAPTCHA_IN
    submit_params = submit_call.kwargs["params"]
    assert submit_params["method"] == "userrecaptcha"
    assert submit_params["googlekey"] == "site-key-abc"
    assert submit_params["pageurl"] == "https://voter.example/lookup"
    assert submit_params["key"] == "key-xyz"


def test_solve_datadome_uses_datadome_method_and_captcha_url():
    responses = [
        {"status": 1, "request": "REQ-DD"},
        {"status": 1, "request": "datadome=COOKIE_VALUE; path=/"},
    ]
    factory, client = _build_mock_client(responses)

    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "k"), \
         patch.object(captcha_solver, "_POLL_INTERVAL", 0), \
         patch("app.services.scraper.captcha_solver.httpx.AsyncClient", factory):
        token = asyncio.run(
            solve_datadome(
                "https://dd-c.captcha-delivery.com/c/abc",
                "https://truepeoplesearch.example/find",
                proxy="user:pass@1.2.3.4:8080",
                proxytype="HTTP",
                user_agent="Mozilla/5.0 ...",
            )
        )

    assert token == "datadome=COOKIE_VALUE; path=/"
    submit_params = client.get.call_args_list[0].kwargs["params"]
    assert submit_params["method"] == "datadome"
    assert submit_params["captcha_url"] == "https://dd-c.captcha-delivery.com/c/abc"
    assert submit_params["userAgent"] == "Mozilla/5.0 ..."
    assert submit_params["proxy"] == "user:pass@1.2.3.4:8080"
    assert submit_params["proxytype"] == "HTTP"


def test_solve_turnstile_sends_turnstile_method():
    responses = [
        {"status": 1, "request": "REQ-TS"},
        {"status": 1, "request": "0.TURNSTILE_TOKEN"},
    ]
    factory, client = _build_mock_client(responses)

    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "k"), \
         patch.object(captcha_solver, "_POLL_INTERVAL", 0), \
         patch("app.services.scraper.captcha_solver.httpx.AsyncClient", factory):
        token = asyncio.run(solve_turnstile("0x4A...", "https://fps.example/detail/1"))

    assert token == "0.TURNSTILE_TOKEN"
    submit_params = client.get.call_args_list[0].kwargs["params"]
    assert submit_params["method"] == "turnstile"
    assert submit_params["sitekey"] == "0x4A..."


# --- Failure modes -------------------------------------------------------


def test_submit_error_response_raises():
    responses = [{"status": 0, "request": "ERROR_KEY_DOES_NOT_EXIST"}]
    factory, _ = _build_mock_client(responses)

    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "bad"), \
         patch.object(captcha_solver, "_POLL_INTERVAL", 0), \
         patch("app.services.scraper.captcha_solver.httpx.AsyncClient", factory):
        with pytest.raises(CaptchaSolverError) as exc:
            asyncio.run(solve_recaptcha_v2("sk", "https://example.com"))

    assert "ERROR_KEY_DOES_NOT_EXIST" in str(exc.value)


def test_poll_error_response_raises():
    responses = [
        {"status": 1, "request": "REQ-1"},
        {"status": 0, "request": "ERROR_CAPTCHA_UNSOLVABLE"},
    ]
    factory, _ = _build_mock_client(responses)

    with patch.object(captcha_solver.settings, "twocaptcha_api_key", "k"), \
         patch.object(captcha_solver, "_POLL_INTERVAL", 0), \
         patch("app.services.scraper.captcha_solver.httpx.AsyncClient", factory):
        with pytest.raises(CaptchaSolverError) as exc:
            asyncio.run(solve_recaptcha_v2("sk", "https://example.com"))

    assert "ERROR_CAPTCHA_UNSOLVABLE" in str(exc.value)
