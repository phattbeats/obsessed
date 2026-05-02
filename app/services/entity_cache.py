"""
Entity cache service — scraped content persists across profiles.
Cache hit = instant response, no HTTP calls. Cache miss = scrape + write.

No expiration. No re-scrape policy. Content is permanent once written.
"""
from app.database import SessionLocal, EntityCache
from sqlalchemy.exc import IntegrityError
from datetime import datetime

CONTENT_MAX_CHARS = 200_000  # 200K chars max per cached entry


def get_cached(entity_name: str, entity_type: str) -> tuple[str, dict] | None:
    """
    Check the cache for an existing entry.
    Returns (raw_content, meta_dict) on cache hit, None on cache miss.
    meta_dict: {scraped_at, source_url}
    """
    db = SessionLocal()
    try:
        entry = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
        ).first()
        if entry and entry.raw_content:
            return entry.raw_content, {
                "scraped_at": entry.scraped_at,
                "source_url": entry.source_url or "",
            }
        return None
    finally:
        db.close()


def write_cached(
    entity_name: str,
    entity_type: str,
    raw_content: str,
    source_url: str = "",
) -> bool:
    """
    Write scraped content to the cache.
    Uses upsert semantics: INSERT OR REPLACE on (entity_name, entity_type).
    Returns True on success, False on failure.
    """
    content = raw_content[:CONTENT_MAX_CHARS]
    db = SessionLocal()
    try:
        existing = db.query(EntityCache).filter(
            EntityCache.entity_name == entity_name,
            EntityCache.entity_type == entity_type,
        ).first()

        if existing:
            existing.raw_content = content
            existing.scraped_at = int(datetime.utcnow().timestamp())
            existing.source_url = source_url
        else:
            entry = EntityCache(
                entity_name=entity_name,
                entity_type=entity_type,
                raw_content=content,
                source_url=source_url,
            )
            db.add(entry)

        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False
    finally:
        db.close()


def delete_cached_by_date(from_ts: int, to_ts: int) -> int:
    """
    Delete all cached entries scraped between from_ts and to_ts (inclusive).
    Returns the number of rows deleted.
    """
    db = SessionLocal()
    try:
        result = db.query(EntityCache).filter(
            EntityCache.scraped_at >= from_ts,
            EntityCache.scraped_at <= to_ts,
        ).delete()
        db.commit()
        return result
    finally:
        db.close()


def delete_all_cached() -> int:
    """Delete all cached entries. Returns the number of rows deleted."""
    db = SessionLocal()
    try:
        count = db.query(EntityCache).delete()
        db.commit()
        return count
    finally:
        db.close()


def count_cached() -> dict:
    """Return cache statistics: total entries and per-type counts."""
    db = SessionLocal()
    try:
        total = db.query(EntityCache).count()
        types = {}
        for etype, in db.query(EntityCache.entity_type).distinct():
            types[etype] = db.query(EntityCache).filter(
                EntityCache.entity_type == etype
            ).count()
        return {"total": total, "by_type": types}
    finally:
        db.close()
