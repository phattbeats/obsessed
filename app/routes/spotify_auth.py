"""
Spotify "link your account" flow — Authorization Code with PKCE.
Spotify's Web API has no public per-user scrape path (unlike last.fm's open
API), so a profile's own listening data can only be pulled once that person
authorizes this app. The state param round-trips the profile_id so the
callback knows which Profile row to attach tokens to; the code_verifier
travels the same way instead of a client secret (this is a public-client
flow by design — see app/services/scraper/spotify.py).
"""
import secrets
import time

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.config import settings
from app.database import SessionLocal, Profile
from app.services.scraper.spotify import build_authorize_url, exchange_code_for_token, generate_pkce_pair

router = APIRouter(prefix="/api/profiles", tags=["spotify"])


@router.get("/{profile_id}/spotify/connect")
def spotify_connect(profile_id: int):
    """Redirect the browser to Spotify's consent screen for this profile."""
    if not settings.spotify_client_id:
        raise HTTPException(status_code=503, detail="Spotify integration is not configured (SPOTIFY_CLIENT_ID unset)")

    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")

        code_verifier, code_challenge = generate_pkce_pair()
        state = secrets.token_urlsafe(24)
        p.spotify_oauth_state = state
        p.spotify_code_verifier = code_verifier
        db.commit()

        return RedirectResponse(build_authorize_url(state, code_challenge))
    finally:
        db.close()


@router.get("/spotify/callback")
async def spotify_callback(
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """Spotify redirects here after the guest approves/denies consent."""
    if error or not code or not state:
        return RedirectResponse(f"/?spotify_error={error or 'missing_code'}")

    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.spotify_oauth_state == state).first()
        if not p:
            return RedirectResponse("/?spotify_error=unknown_state")

        code_verifier = p.spotify_code_verifier
        profile_id = p.id
        # Clear the one-time handshake state now — it's single-use regardless of outcome.
        p.spotify_oauth_state = ""
        p.spotify_code_verifier = ""
        db.commit()
    finally:
        db.close()

    try:
        token_data = await exchange_code_for_token(code, code_verifier)
    except Exception:
        return RedirectResponse(f"/?spotify_error=token_exchange_failed&profile_id={profile_id}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        me_resp = await client.get(
            "https://api.spotify.com/v1/me",
            headers={"Authorization": f"Bearer {token_data['access_token']}"},
        )
    me = me_resp.json() if me_resp.status_code == 200 else {}

    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            return RedirectResponse(f"/?spotify_error=profile_missing&profile_id={profile_id}")
        p.spotify_access_token = token_data["access_token"]
        p.spotify_refresh_token = token_data.get("refresh_token", "")
        p.spotify_token_expires_at = int(time.time()) + int(token_data.get("expires_in", 3600))
        p.spotify_user_id = me.get("id", "")
        p.spotify_display_name = me.get("display_name", "")
        db.commit()
    finally:
        db.close()

    return RedirectResponse(f"/?spotify_linked={profile_id}")
