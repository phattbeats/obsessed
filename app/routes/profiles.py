import asyncio
from fastapi import APIRouter, HTTPException
from app.config import settings
from app.database import SessionLocal, Profile, Question
from app.models import ProfileCreate, ProfileUpdate, ProfileResponse, QuestionResponse
from app.services.scraper.reddit import scrape_reddit, generate_questions
from app.services.scraper.pinterest import scrape_pinterest
from app.services.scraper.threads import scrape_threads
from app.services.scraper.instagram import scrape_instagram
from app.services.scraper.crawl4ai import crawl4ai_scrape
from app.services.scraper.places import scrape_places
from app.services.scraper.things import scrape_things
from app.services.scraper.events import scrape_events
from app.services.generator import generate_from_manual
import json
import hashlib, hmac, time as _time_module

router = APIRouter(prefix="/api/profiles", tags=["profiles"])

def _profile(p: Profile) -> ProfileResponse:
    return ProfileResponse(
        id=p.id, name=p.name, bio=p.bio,
        reddit_handle=p.reddit_handle, twitter_handle=p.twitter_handle,
        steam_id=p.steam_id, discord_handle=p.discord_handle,
        pinterest_handle=p.pinterest_handle,
        threads_handle=p.threads_handle,
        instagram_handle=p.instagram_handle,
        google_places_handle=p.google_places_handle or "",
        wikipedia_handle=p.wikipedia_handle or "",
        osm_query=p.osm_query or "",
        travel_url=p.travel_url or "",
        wikidata_query=p.wikidata_query or "",
        openlibrary_query=p.openlibrary_query or "",
        gdelt_query=p.gdelt_query or "",
        manual_link=p.manual_link, manual_facts=p.manual_facts,
        scrape_status=p.scrape_status, scrape_error=p.scrape_error,
        question_count=p.question_count,
        llm_calls=p.llm_calls or 0,
        llm_spend_cents=p.llm_spend_cents or 0,
        question_budget=p.question_budget or 50,
        consent_obtained=bool(p.consent_obtained),
        content_quality=p.content_quality or "",
        content_chunks=p.content_chunks or 0,
        entity_type=p.entity_type or "person",
        created_at=p.created_at, updated_at=p.updated_at,
    )

@router.post("", response_model=ProfileResponse)
def create_profile(data: ProfileCreate):
    db = SessionLocal()
    try:
        p = Profile(name=data.name, bio=data.bio, reddit_handle=data.reddit_handle,
                    twitter_handle=data.twitter_handle, steam_id=data.steam_id,
                    discord_handle=data.discord_handle, pinterest_handle=data.pinterest_handle,
                    wikipedia_handle=getattr(data, "wikipedia_handle", "") or "",
                    osm_query=getattr(data, "osm_query", "") or "",
                    travel_url=getattr(data, "travel_url", "") or "",
                    wikidata_query=getattr(data, "wikidata_query", "") or "",
                    openlibrary_query=getattr(data, "openlibrary_query", "") or "",
                    gdelt_query=getattr(data, "gdelt_query", "") or "",
                    threads_handle=getattr(data, "threads_handle", "") or "",
                    instagram_handle=getattr(data, "instagram_handle", "") or "",
                    google_places_handle=getattr(data, "google_places_handle", "") or "",
                    manual_link=data.manual_link,
                    manual_facts=data.manual_facts,
                    entity_type=getattr(data, "entity_type", "person") or "person",
                    llm_calls=0, llm_spend_cents=0,
                    question_budget=getattr(data, "question_budget", 50) or 50,
                    consent_obtained=getattr(data, "consent_obtained", False) or False,
                    content_quality="", content_chunks=0)
        db.add(p)
        db.commit()
        db.refresh(p)
        return _profile(p)
    finally:
        db.close()

@router.get("", response_model=list[ProfileResponse])
def list_profiles():
    db = SessionLocal()
    try:
        profiles = db.query(Profile).order_by(Profile.created_at.desc()).all()
        return [_profile(p) for p in profiles]
    finally:
        db.close()

@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(profile_id: int):
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")
        return _profile(p)
    finally:
        db.close()

@router.put("/{profile_id}", response_model=ProfileResponse)
def update_profile(profile_id: int, data: ProfileUpdate):
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(p, field, value)
        p.updated_at = int(__import__("datetime").datetime.utcnow().timestamp())
        db.commit()
        db.refresh(p)
        return _profile(p)
    finally:
        db.close()

@router.delete("/{profile_id}")
def delete_profile(profile_id: int):
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")
        db.delete(p)
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@router.get("/{profile_id}/stats")
def get_profile_stats(profile_id: int):
    """Returns question count, LLM call stats, spend, and budget info."""
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")
        return {
            "profile_id": p.id,
            "name": p.name,
            "question_count": p.question_count or 0,
            "llm_calls": p.llm_calls or 0,
            "llm_spend_cents": p.llm_spend_cents or 0,
            "llm_spend_dollars": round((p.llm_spend_cents or 0) / 100, 4),
            "question_budget": p.question_budget or 50,
            "scrape_status": p.scrape_status,
            "updated_at": p.updated_at,
        }
    finally:
        db.close()


CONSENT_SECRET = b"obsessed-consent-2026"

@router.get("/{profile_id}/consent-link")
def generate_consent_link(profile_id: int):
    """Generate a signed consent URL for the guest to visit."""
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")
        token = p.consent_token or ""
        if not token:
            token = hashlib.sha256(f"{p.id}-{p.name}-{_time_module.time() * 1e9}".encode()).hexdigest()[:32]
            p.consent_token = token
            db.commit()
        link = f"/profiles/consent/verify?token={token}"
        return {"consent_link": link, "profile_name": p.name, "token": token}
    finally:
        db.close()

@router.get("/profiles/consent/verify")
def verify_consent(token: str):
    """Guest visits this URL to grant consent."""
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.consent_token == token).first()
        if not p:
            raise HTTPException(status_code=404, detail="Invalid consent link")
        p.consent_obtained = True
        db.commit()
        return {"ok": True, "message": f"Consent confirmed for {p.name}"}
    finally:
        db.close()

@router.post("/{profile_id}/scrape")
async def trigger_scrape(profile_id: int):
    db = SessionLocal()
    p = db.query(Profile).filter(Profile.id == profile_id).first()
    if not p:
        db.close()
        raise HTTPException(status_code=404, detail="Profile not found")
    try:
        p.scrape_status = "scraping"
        p.scrape_error = ""
        db.commit()

        raw_parts: list[str] = []
        scraper_errors: list[str] = []

        async def _safe(label: str, coro):
            try:
                text, _ = await coro
                if text and not text.startswith(f"[{label}"):
                    raw_parts.append(text)
            except Exception as exc:
                scraper_errors.append(f"{label}: {exc}")

        # ── Cache check ──────────────────────────────────────────────────────
        from app.services.entity_cache import get_cached, write_cached
        cache_hit = get_cached(p.name, p.entity_type)
        cached_raw = ""
        if cache_hit:
            cached_raw, _ = cache_hit

        if cached_raw:
            raw = cached_raw
        else:
            # ── Scrape ────────────────────────────────────────────────────────
            if p.reddit_handle:
                await _safe("Reddit", scrape_reddit(p.reddit_handle))
            if p.pinterest_handle:
                await _safe("Pinterest", scrape_pinterest(p.pinterest_handle))
            if p.threads_handle:
                await _safe("Threads", scrape_threads(p.threads_handle))
            if p.instagram_handle:
                await _safe("Instagram", scrape_instagram(p.instagram_handle))
            if p.manual_facts:
                raw_parts.append(p.manual_facts)
            if p.manual_link:
                try:
                    text, _ = await crawl4ai_scrape(p.manual_link)
                    if text and len(text) > 20 and not text.startswith("[crawl4ai"):
                        raw_parts.append(text)
                except Exception as exc:
                    scraper_errors.append(f"crawl4ai: {exc}")
            if p.google_places_handle:
                await _safe("Places", scrape_places(google_places_query=p.google_places_handle))
            if p.wikipedia_handle:
                from app.services.scraper.wikipedia import scrape_wikipedia
                await _safe("Wikipedia", scrape_wikipedia(p.wikipedia_handle))
            if p.osm_query:
                from app.services.scraper.osm import scrape_osm
                await _safe("OpenStreetMap", scrape_osm(p.osm_query))
            if p.travel_url:
                from app.services.scraper.travel import scrape_travel_blog
                await _safe("Travel", scrape_travel_blog(p.travel_url))
            if p.wikidata_query:
                await _safe("Things", scrape_things(wikidata_query=p.wikidata_query))
            if p.openlibrary_query:
                await _safe("Things", scrape_things(openlibrary_query=p.openlibrary_query))
            if p.gdelt_query:
                await _safe("Events", scrape_events(gdelt_query=p.gdelt_query))

            raw = "\n".join(raw_parts)
            if raw.strip():
                write_cached(p.name, p.entity_type, raw)

        p.raw_content = raw[: settings.content_max_chars]

        # Estimate content quality from scraped chunks
        chunks = [ch.strip() for ch in raw.split("\n\n") if len(ch.strip()) > 40]
        p.content_chunks = len(chunks)
        if len(chunks) < 15:
            p.content_quality = "insufficient"
        elif len(chunks) < 30:
            p.content_quality = "limited"
        elif len(chunks) <= 100:
            p.content_quality = "adequate"
        else:
            p.content_quality = "rich"
            p.content_chunks = 100  # cap at 100 for rich

        p.scrape_status = "done"
        p.updated_at = int(__import__("datetime").datetime.utcnow().timestamp())
        if scraper_errors:
            p.scrape_error = "; ".join(scraper_errors)[:500]
        db.commit()

        scrape_warning = None
        if p.content_quality == "insufficient":
            scrape_warning = f"Not enough content. Need at least 15 facts, got {len(chunks)}. Add more handles or use manual entry."
        elif p.content_quality == "limited":
            scrape_warning = f"Limited content ({len(chunks)} facts) - generating shortened 25-question game."

        # Trigger question generation (cache hit included — questions live on Profile, not cache)
        await _generate_questions_async(p.id, raw, p.name, budget=p.question_budget)

        return {
            "ok": True,
            "status": "done",
            "warning": scrape_warning,
            "content_quality": p.content_quality,
            "content_chunks": p.content_chunks,
            "cached": bool(cached_raw),
            "scraper_errors": scraper_errors,
        }
    except HTTPException:
        raise
    except Exception as e:
        if p is not None:
            p.scrape_status = "failed"
            p.scrape_error = str(e)[:500]
            db.commit()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

async def _generate_questions_async(profile_id: int, raw_content: str, name: str, budget: int = 50):
    from app.services.scraper.reddit import generate_questions as gen_llm
    from app.services.generator import generate_from_manual
    import httpx

    db = SessionLocal()
    total_calls = 0
    total_spend_cents = 0
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        questions = []
        if raw_content.strip():
            # Try LLM generation (counted against budget)
            try:
                result = await gen_llm(profile_id, raw_content, name)
                questions = result
                # Extract usage from the LiteLLM response if available
                # gen_llm doesn't return usage directly - estimate from output length
                # One call per generate_questions invocation
                total_calls = 1
                est_tokens = len(raw_content) // 4 + 2000  # rough estimate
                total_spend_cents = int(est_tokens * 0.003)  # ~$3/MTok input, rounded up
            except Exception:
                pass
        if not questions and raw_content.strip():
            questions = generate_from_manual(raw_content, name)

        budget = budget or 50
        # Reduce question count for limited content
        max_q = budget if budget else 50
        if p and p.content_quality == "limited":
            max_q = min(25, max_q)
        questions = questions[:max_q]  # enforce budget + quality cap

        for q in questions:
            db.add(Question(
                profile_id=profile_id,
                category=q.get("category", "history"),
                question_text=q["question_text"],
                correct_answer=q["correct_answer"],
                wrong_answers=json.dumps(q.get("wrong_answers", [])),
                difficulty=q.get("difficulty", 1),
                source_snippet=q.get("source_snippet", "")[:500],
            ))

        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if p:
            p.question_count = db.query(Question).filter(Question.profile_id == profile_id).count()
            p.llm_calls = (p.llm_calls or 0) + min(total_calls, budget)
            p.llm_spend_cents = (p.llm_spend_cents or 0) + total_spend_cents
        db.commit()
    finally:
        db.close()

@router.get("/{profile_id}/questions", response_model=list[QuestionResponse])
def preview_questions(profile_id: int):
    db = SessionLocal()
    try:
        qs = db.query(Question).filter(Question.profile_id == profile_id).limit(50).all()
        return [QuestionResponse(
            id=q.id, category=q.category, question_text=q.question_text,
            correct_answer=q.correct_answer,
            wrong_answers=json.loads(q.wrong_answers) if q.wrong_answers else [],
            difficulty=q.difficulty, source_snippet=q.source_snippet,
        ) for q in qs]
    finally:
        db.close()

@router.post("/{profile_id}/generate")
async def trigger_generate(profile_id: int):
    db = SessionLocal()
    try:
        p = db.query(Profile).filter(Profile.id == profile_id).first()
        if not p:
            raise HTTPException(status_code=404, detail="Profile not found")
        raw = p.raw_content or p.manual_facts
        await _generate_questions_async(p.id, raw, p.name, budget=p.question_budget)
        db.refresh(p)
        return {"ok": True, "question_count": p.question_count}
    finally:
        db.close()
