"""
Ohio SoS bulk voter-file ingestion and lookup.

Downloads the county-level voter CSV through Cloudflare clearance (via
get_clearance()) and indexes it into SQLite for offline name+DOB lookups.

Replaces the reCAPTCHA-gated voterlookup.ohiosos.gov per-query path.

Download index: https://www6.ohiosos.gov/ords/f?p=VOTERFTP:HOME
Franklin County CSV: ~509 MB, refreshed weekly by SoS.
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from typing import Any

import httpx

from app.services.scraper.sos.base import get_clearance

log = logging.getLogger(__name__)

VOTER_FTP_UI_ORIGIN = "https://www6.ohiosos.gov"

# County → download URL. Expand as needed.
COUNTY_DOWNLOADS: dict[str, str] = {
    "franklin": (
        "https://www6.ohiosos.gov/ords/f?p=VOTERFTP:DOWNLOAD::FILE:NO:2:P2_PRODUCT_NUMBER:25"
    ),
}

_BATCH_SIZE = 5_000
_DOWNLOAD_TIMEOUT = 3600  # 1 hr — 509 MB takes time
_CHUNK_SIZE = 65_536      # 64 KB read chunks


def _get_db():
    from app.database import SessionLocal
    return SessionLocal()


# ── Ingestion ──────────────────────────────────────────────────────────────────

async def ingest_county(
    county: str = "franklin",
    *,
    force: bool = False,
) -> dict[str, Any]:
    """
    Stream-download the county voter file and upsert into SQLite.

    Returns summary dict: county, rows_inserted, elapsed_sec.
    Raises on Cloudflare error or HTTP failure (caller is responsible for
    writing error status to the import record).
    """
    from app.database import engine
    from sqlalchemy import text

    county = county.lower()
    url = COUNTY_DOWNLOADS.get(county)
    if not url:
        raise ValueError(f"No download URL configured for county={county!r}")

    import_id = _start_import(county, url)

    try:
        cookies, ua = await get_clearance(VOTER_FTP_UI_ORIGIN)
        log.info("ohio_voter_file: clearance acquired for %s", VOTER_FTP_UI_ORIGIN)

        # WAL mode + large cache for bulk writes
        with engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))
            conn.execute(text("PRAGMA cache_size=-65536"))

        t0 = time.monotonic()
        rows_inserted = 0
        headers_map: dict[str, int] | None = None
        batch: list[dict] = []
        tail = ""  # incomplete line buffer

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_DOWNLOAD_TIMEOUT, connect=30),
            follow_redirects=True,
            headers={
                "User-Agent": ua,
                "Accept": "text/csv,text/plain,*/*",
                "Referer": f"{VOTER_FTP_UI_ORIGIN}/",
            },
            cookies=cookies,
        ) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                log.info(
                    "ohio_voter_file: download started, content-type=%s",
                    resp.headers.get("content-type", "?"),
                )

                async for raw_chunk in resp.aiter_bytes(_CHUNK_SIZE):
                    text_chunk = raw_chunk.decode("utf-8", errors="replace")
                    block = tail + text_chunk
                    lines = block.split("\n")
                    tail = lines[-1]  # last item may be an incomplete line

                    for line in lines[:-1]:
                        line = line.rstrip("\r")
                        if not line:
                            continue
                        if headers_map is None:
                            headers_map = _parse_header(line)
                            log.info(
                                "ohio_voter_file: header parsed — %d columns",
                                len(headers_map),
                            )
                            continue
                        row = _extract_row(line, headers_map, county)
                        if row:
                            batch.append(row)
                            if len(batch) >= _BATCH_SIZE:
                                rows_inserted += _flush_batch(batch)
                                batch.clear()
                                log.debug(
                                    "ohio_voter_file: %d rows inserted so far",
                                    rows_inserted,
                                )

                # flush remaining tail
                if tail.strip() and headers_map is not None:
                    row = _extract_row(tail.rstrip("\r"), headers_map, county)
                    if row:
                        batch.append(row)

                if batch:
                    rows_inserted += _flush_batch(batch)

        elapsed = time.monotonic() - t0
        _finish_import(import_id, rows_inserted, None)
        log.info(
            "ohio_voter_file: done — %d rows in %.1fs for county=%s",
            rows_inserted, elapsed, county,
        )
        return {"county": county, "rows_inserted": rows_inserted, "elapsed_sec": round(elapsed, 1)}

    except Exception as exc:
        _finish_import(import_id, 0, str(exc)[:500])
        raise


def _parse_header(line: str) -> dict[str, int]:
    """Parse the CSV header line into a col→index map (uppercase keys)."""
    reader = csv.reader([line])
    cols = next(reader, [])
    return {h.strip().upper(): i for i, h in enumerate(cols)}


def _extract_row(line: str, headers_map: dict[str, int], county: str) -> dict | None:
    """Parse a single CSV data line into a DB-insert dict."""
    reader = csv.reader([line])
    try:
        vals = next(reader)
    except StopIteration:
        return None

    def g(col: str) -> str:
        idx = headers_map.get(col)
        if idx is None or idx >= len(vals):
            return ""
        return vals[idx].strip()

    sos_id = g("SOS_VOTERID")
    if not sos_id:
        return None

    # Collect voting-history columns into a compact JSON dict
    history: dict[str, str] = {}
    for col, idx in headers_map.items():
        if (
            col.startswith("PRIMARY_")
            or col.startswith("GENERAL_")
            or col.startswith("SPECIAL_")
        ):
            if idx < len(vals):
                v = vals[idx].strip()
                if v:
                    history[col] = v

    return {
        "sos_voterid": sos_id,
        "county": county,
        "last_name": g("LAST_NAME").upper(),
        "first_name": g("FIRST_NAME").upper(),
        "middle_name": g("MIDDLE_NAME"),
        "suffix": g("SUFFIX"),
        "dob": _normalize_dob(g("DATE_OF_BIRTH")),
        "registration_date": g("REGISTRATION_DATE"),
        "voter_status": g("VOTER_STATUS"),
        "party": g("PARTY_AFFILIATION"),
        "address1": g("RESIDENTIAL_ADDRESS1"),
        "address2": g("RESIDENTIAL_SECONDARY_ADDR"),
        "city": g("RESIDENTIAL_CITY"),
        "state": g("RESIDENTIAL_STATE") or "OH",
        "zipcode": g("RESIDENTIAL_ZIP"),
        "precinct_name": g("PRECINCT_NAME"),
        "precinct_code": g("PRECINCT_CODE"),
        "congressional_district": g("CONGRESSIONAL_DISTRICT"),
        "state_rep_district": g("STATE_REPRESENTATIVE_DISTRICT"),
        "state_senate_district": g("STATE_SENATE_DISTRICT"),
        "voting_history": json.dumps(history) if history else "",
    }


def _normalize_dob(raw: str) -> str:
    """MM/DD/YYYY → YYYY-MM-DD. Returns raw string unchanged if format differs."""
    if not raw:
        return ""
    parts = raw.split("/")
    if len(parts) == 3 and len(parts[2]) == 4:
        return f"{parts[2]}-{parts[0].zfill(2)}-{parts[1].zfill(2)}"
    return raw


def _flush_batch(batch: list[dict]) -> int:
    """Upsert a batch into ohio_voter_file using ON CONFLICT on sos_voterid."""
    from app.database import engine
    from sqlalchemy import text

    cols = list(batch[0].keys())
    col_list = ", ".join(cols)
    placeholders = ", ".join(f":{c}" for c in cols)
    update_clause = ", ".join(
        f"{c}=excluded.{c}" for c in cols if c != "sos_voterid"
    )
    sql = text(
        f"INSERT INTO ohio_voter_file ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT(sos_voterid) DO UPDATE SET {update_clause}"
    )
    with engine.begin() as conn:
        conn.execute(sql, batch)
    return len(batch)


def _start_import(county: str, url: str) -> int:
    from app.database import OhioVoterFileImport
    db = _get_db()
    try:
        rec = OhioVoterFileImport(
            county=county,
            download_url=url,
            status="running",
            started_at=int(time.time()),
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return rec.id
    finally:
        db.close()


def _finish_import(import_id: int, rows: int, error: str | None) -> None:
    from app.database import OhioVoterFileImport
    db = _get_db()
    try:
        rec = db.query(OhioVoterFileImport).filter(OhioVoterFileImport.id == import_id).first()
        if rec:
            rec.status = "error" if error else "done"
            rec.rows_inserted = rows
            rec.error = error or ""
            rec.finished_at = int(time.time())
            db.commit()
    finally:
        db.close()


# ── Lookup ─────────────────────────────────────────────────────────────────────

def lookup_voter(
    last_name: str,
    first_name: str,
    *,
    dob: str | None = None,
    county: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Query the local voter file by name (+ optional DOB / county).

    Names are stored uppercased; matching is exact on the normalized value.
    DOB should be YYYY-MM-DD or MM/DD/YYYY (auto-normalized).
    Returns list of voter record dicts including voting_history.
    """
    from app.database import OhioVoterFile
    db = _get_db()
    try:
        q = db.query(OhioVoterFile).filter(
            OhioVoterFile.last_name == last_name.strip().upper(),
            OhioVoterFile.first_name == first_name.strip().upper(),
        )
        if dob:
            q = q.filter(OhioVoterFile.dob == _normalize_dob(dob))
        if county:
            q = q.filter(OhioVoterFile.county == county.lower())
        rows = q.limit(limit).all()
        return [_row_to_dict(r) for r in rows]
    finally:
        db.close()


def _row_to_dict(r) -> dict:
    history: dict = {}
    if r.voting_history:
        try:
            history = json.loads(r.voting_history)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "sos_voterid": r.sos_voterid,
        "county": r.county,
        "last_name": r.last_name,
        "first_name": r.first_name,
        "middle_name": r.middle_name or "",
        "suffix": r.suffix or "",
        "dob": r.dob or "",
        "registration_date": r.registration_date or "",
        "voter_status": r.voter_status or "",
        "party": r.party or "",
        "address1": r.address1 or "",
        "address2": r.address2 or "",
        "city": r.city or "",
        "state": r.state or "OH",
        "zipcode": r.zipcode or "",
        "precinct_name": r.precinct_name or "",
        "precinct_code": r.precinct_code or "",
        "congressional_district": r.congressional_district or "",
        "state_rep_district": r.state_rep_district or "",
        "state_senate_district": r.state_senate_district or "",
        "voting_history": history,
    }


def format_voter_text(voters: list[dict], query: str) -> str:
    """Format voter records as readable text for inclusion in profile raw_content."""
    if not voters:
        return ""
    _PARTY = {"D": "Democrat", "R": "Republican", "G": "Green", "L": "Libertarian", "N": "No affiliation"}
    lines = [f"[Ohio voter registration for: {query}]"]
    for v in voters:
        name_parts = [v["first_name"], v.get("middle_name") or "", v["last_name"], v.get("suffix") or ""]
        name = " ".join(p for p in name_parts if p).strip()
        addr = ", ".join(p for p in [v["address1"], v.get("address2") or "", v["city"], v["state"], v["zipcode"]] if p)
        party = _PARTY.get(v.get("party", ""), v.get("party", "Unknown"))
        lines.append(f"- Voter: {name}")
        lines.append(f"  DOB: {v.get('dob', '')} | Status: {v.get('voter_status', '')} | Party: {party}")
        lines.append(f"  Address: {addr}")
        lines.append(f"  Precinct: {v.get('precinct_name', '')} ({v.get('precinct_code', '')})")
        lines.append(
            f"  Congressional: {v.get('congressional_district', '')} | "
            f"State Rep: {v.get('state_rep_district', '')} | "
            f"State Senate: {v.get('state_senate_district', '')}"
        )
        vh = v.get("voting_history", {})
        if vh:
            participated = sorted(k for k, val in vh.items() if val in ("Y", "X", "1", "S"))
            if participated:
                lines.append(f"  Voted in: {', '.join(participated[-12:])}")
    return "\n".join(lines)


def parse_voter_query(voter_query: str) -> tuple[str, str, str | None]:
    """
    Parse a voter_query string into (last_name, first_name, dob_or_None).

    Accepted formats:
      "Smith, John"
      "Smith, John, 1980-06-15"
      "Smith, John, 06/15/1980"
    """
    parts = [p.strip() for p in voter_query.split(",")]
    if len(parts) < 2:
        return "", "", None
    last = parts[0]
    first = parts[1]
    dob = parts[2] if len(parts) >= 3 else None
    return last, first, dob


def get_import_status(county: str | None = None) -> list[dict]:
    """Return recent import records (most recent first)."""
    from app.database import OhioVoterFileImport
    db = _get_db()
    try:
        q = db.query(OhioVoterFileImport).order_by(OhioVoterFileImport.started_at.desc())
        if county:
            q = q.filter(OhioVoterFileImport.county == county.lower())
        recs = q.limit(10).all()
        return [
            {
                "id": r.id,
                "county": r.county,
                "status": r.status,
                "rows_inserted": r.rows_inserted or 0,
                "error": r.error or "",
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "download_url": r.download_url,
            }
            for r in recs
        ]
    finally:
        db.close()


def voter_file_row_count(county: str | None = None) -> int:
    """Return total rows indexed for the given county (or all counties)."""
    from app.database import OhioVoterFile
    db = _get_db()
    try:
        q = db.query(OhioVoterFile)
        if county:
            q = q.filter(OhioVoterFile.county == county.lower())
        return q.count()
    finally:
        db.close()
