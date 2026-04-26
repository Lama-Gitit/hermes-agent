"""
Ingestion runner — the code that stitches adapters to Supabase.

Responsibilities:
  1. Load enabled rows from `hermes_sources`.
  2. For each row, look up its adapter in REGISTRY and call adapter.fetch().
  3. De-duplicate returned entries by (source_id, dedup_key) against the last
     30 days of hermes_entries — so re-running the same fetch is idempotent.
  4. Bulk-insert survivors into hermes_entries.
  5. Log a summary row to hermes_ingestion_jobs.
  6. Bump hermes_sources.last_checked_at.

Entry points:
  - run_all(enabled_only=True, only_source_ids=None)  → run many
  - run_one(source_id)                                → run one by id
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from tools.fetchers.base import FetchEntry, FetchResult, REGISTRY, get_adapter

logger = logging.getLogger(__name__)


# ── Internal helpers ────────────────────────────────────────────────────────

def _get_client():
    from tools.supabase_client import get_client
    return get_client()


def _recent_dedup_keys(client, source_id: int, days: int = 30) -> set:
    """
    Return the set of dedup_keys already stored for this source in the last
    `days` days, so we can skip re-inserting them. We stash dedup_key inside
    the `value` JSONB column (see FetchEntry.for_insert).
    """
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    try:
        rows = (
            client.table("hermes_entries")
            .select("value, source")
            .gte("date_processed", cutoff)
            .execute()
        ).data or []
    except Exception as e:
        logger.warning("[runner] dedup lookup failed: %s", e)
        return set()

    keys = set()
    for r in rows:
        # only compare entries that came from the same source_id
        src = r.get("source") or {}
        if isinstance(src, dict) and src.get("source_id") == source_id:
            val = r.get("value") or {}
            k = val.get("dedup_key") if isinstance(val, dict) else None
            if k:
                keys.add(k)
    return keys


def _log_job(client, result: FetchResult, entries_created: int) -> None:
    """Insert one row in hermes_ingestion_jobs summarising this run."""
    try:
        client.table("hermes_ingestion_jobs").insert({
            "source_id": result.source_id,
            "started_at": result.started_at.isoformat(),
            "finished_at": (result.finished_at or datetime.now(timezone.utc)).isoformat(),
            "items_found": result.items_found,
            "entries_created": entries_created,
            "status": result.status,
            "error": (result.error or "")[:2000],
        }).execute()
    except Exception as e:
        logger.error("[runner] could not log ingestion job: %s", e)


def _bump_last_checked(client, source_id: int) -> None:
    try:
        client.table("hermes_sources").update({
            "last_checked_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", source_id).execute()
    except Exception as e:
        logger.warning("[runner] could not update last_checked_at: %s", e)


def _insert_entries(client, entries: List[FetchEntry]) -> int:
    """Bulk-insert entries, chunked. Returns actual number inserted."""
    if not entries:
        return 0
    rows = [e.for_insert() for e in entries]
    inserted = 0
    CHUNK = 100
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i : i + CHUNK]
        try:
            res = client.table("hermes_entries").insert(chunk).execute()
            inserted += len(res.data or [])
        except Exception as e:
            logger.error("[runner] insert chunk failed (%d-%d): %s", i, i + len(chunk), e)
    return inserted


# ── Public entry points ─────────────────────────────────────────────────────

def run_one(source_id: int) -> Dict[str, Any]:
    """Run ingestion for a single source row by id."""
    client = _get_client()
    if not client:
        return {"error": "supabase client not available"}

    try:
        row = (
            client.table("hermes_sources")
            .select("*")
            .eq("id", source_id)
            .single()
            .execute()
        ).data
    except Exception as e:
        return {"error": f"source {source_id} not found: {e}"}

    return _run_row(client, row)


def run_all(
    enabled_only: bool = True,
    only_source_ids: Optional[List[int]] = None,
    only_source_types: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Run ingestion across matching sources. Returns per-source summary."""
    client = _get_client()
    if not client:
        return {"error": "supabase client not available"}

    q = client.table("hermes_sources").select("*")
    if enabled_only:
        q = q.eq("enabled", True)
    if only_source_types:
        q = q.in_("source_type", only_source_types)
    rows = (q.execute()).data or []

    if only_source_ids:
        want = set(only_source_ids)
        rows = [r for r in rows if r.get("id") in want]

    summaries = []
    for row in rows:
        summaries.append(_run_row(client, row))

    return {
        "ran": len(summaries),
        "summaries": summaries,
    }


def _run_row(client, row: Dict[str, Any]) -> Dict[str, Any]:
    """Run one source row — the shared path for run_one and run_all."""
    source_id = row["id"]
    source_type = row.get("source_type", "")
    name = row.get("name", f"id={source_id}")

    adapter = get_adapter(source_type)
    if adapter is None:
        summary = {
            "source_id": source_id,
            "name": name,
            "source_type": source_type,
            "status": "skipped",
            "error": f"no adapter registered for source_type={source_type!r}",
            "entries_created": 0,
            "items_found": 0,
        }
        # Still log the skip so the user can see it in hermes_ingestion_jobs
        fake = FetchResult(source_id=source_id, status="skipped", error=summary["error"])
        fake.mark_done("skipped", summary["error"])
        _log_job(client, fake, entries_created=0)
        return summary

    logger.info("[runner] %s (id=%s, type=%s) — starting", name, source_id, source_type)
    try:
        result = adapter.fetch(row)
    except Exception as e:
        logger.exception("[runner] adapter.fetch raised for source %s", source_id)
        fake = FetchResult(source_id=source_id)
        fake.mark_done("error", f"{type(e).__name__}: {e}")
        _log_job(client, fake, entries_created=0)
        _bump_last_checked(client, source_id)
        return {
            "source_id": source_id,
            "name": name,
            "source_type": source_type,
            "status": "error",
            "error": str(e)[:500],
            "items_found": 0,
            "entries_created": 0,
        }

    # De-duplicate
    skip_keys = _recent_dedup_keys(client, source_id, days=30)
    fresh = [e for e in result.entries if e.dedup_key not in skip_keys]

    # Stamp source_id into the source JSONB of each row so dedup works next time
    for e in fresh:
        e.source = {**(e.source or {}), "source_id": source_id}

    inserted = _insert_entries(client, fresh)

    if result.status == "success" and inserted < len(fresh):
        result.mark_done("partial", f"inserted {inserted}/{len(fresh)}")
    else:
        result.mark_done(result.status, result.error)

    _log_job(client, result, entries_created=inserted)
    _bump_last_checked(client, source_id)

    return {
        "source_id": source_id,
        "name": name,
        "source_type": source_type,
        "status": result.status,
        "error": result.error,
        "items_found": result.items_found,
        "entries_returned": len(result.entries),
        "entries_inserted": inserted,
        "deduped_out": len(result.entries) - len(fresh),
    }
