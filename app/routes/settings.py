"""
Settings + diagnostics surface for the Obsessed UI.
Read-only snapshot of runtime config + cache stats + LiteLLM reachability.
"""
from __future__ import annotations

import os
import asyncio
import httpx
from fastapi import APIRouter
from app.config import settings
from app.services.entity_cache import count_cached, delete_all_cached

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings():
    litellm_status = await _check_litellm()
    crawl4ai_status = await _check_crawl4ai()
    return {
        "app": {
            "name": settings.app_name,
            "version": "1.0.0",
        },
        "litellm": {
            "base": settings.litellm_base,
            "api_key_set": bool(os.environ.get("LITELLM_API_KEY") or settings.litellm_api_key),
            **litellm_status,
        },
        "crawl4ai": crawl4ai_status,
        "questions": {
            "default_count": settings.question_count,
            "timeout_seconds": settings.question_timeout,
            "categories": settings.categories,
        },
        "content": {
            "max_chars_per_source": settings.content_max_chars,
        },
        "websocket": {
            "heartbeat_seconds": settings.ws_heartbeat,
        },
        "cache": count_cached(),
    }


@router.post("/cache/clear")
def clear_cache():
    """Wipe every cached entity. Mirrored on /api/admin/cache/delete/all for ops."""
    deleted = delete_all_cached()
    return {"deleted": deleted}


async def _check_litellm() -> dict:
    url = f"{settings.litellm_base.rstrip('/')}/health"
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url)
        return {"reachable": resp.status_code < 500, "status_code": resp.status_code}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)[:200]}


async def _check_crawl4ai() -> dict:
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get("http://crawl4ai:11235/health")
        return {"base": "http://crawl4ai:11235", "reachable": resp.status_code < 500, "status_code": resp.status_code}
    except Exception as exc:
        return {"base": "http://crawl4ai:11235", "reachable": False, "error": str(exc)[:200]}
