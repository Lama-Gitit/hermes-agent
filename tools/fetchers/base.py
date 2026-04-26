"""
Fetcher base class + registry.

A FetcherAdapter knows how to talk to ONE source_type. The runner (runner.py)
reads enabled rows from `hermes_sources`, looks up the matching adapter in
REGISTRY, calls `adapter.fetch(source_row)`, and persists the returned entries
to `hermes_entries` while logging a row to `hermes_ingestion_jobs`.

Keep adapters stateless and idempotent. De-duplication is the runner's job,
not the adapter's.

Conventions:
  - Every returned FetchEntry MUST include a stable `dedup_key` string; the
    runner skips entries whose dedup_key already exists in hermes_entries
    within the last 30 days (checked via the `value->>'dedup_key'` JSONB path).
  - `card_id` should use the canonical scheme
      <set_code>-<card_number>-<variant>-<language>-<grade>
    but may be "unresolved" when the upstream doesn't give us enough signal.
  - `confidence` must be one of: canonical | observed | claimed |
    user_hypothesis | speculative  (matches the existing save_entry enum).
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class FetchEntry:
    """One row destined for hermes_entries."""

    claim_type: str                      # price | sentiment | fundamental | hypothesis
    value: Dict[str, Any]                # JSONB payload — shape depends on claim_type
    source: Dict[str, Any]               # {url, source_type, author?, author_credibility}
    dedup_key: str                       # stable hash; runner skips duplicates
    card_id: str = "unresolved"
    confidence: str = "observed"
    date_observed: Optional[str] = None  # YYYY-MM-DD, defaults to today

    def for_insert(self) -> Dict[str, Any]:
        """Turn this into a dict matching the hermes_entries schema."""
        # Bake dedup_key into value JSONB so the runner can query it.
        value = dict(self.value)
        value.setdefault("dedup_key", self.dedup_key)
        return {
            "card_id": self.card_id,
            "claim_type": self.claim_type,
            "value": value,
            "source": self.source,
            "confidence": self.confidence,
            "date_observed": self.date_observed or date.today().isoformat(),
            "date_processed": date.today().isoformat(),
        }


@dataclass
class FetchResult:
    """Summary of one fetch run, mirrored into hermes_ingestion_jobs."""

    source_id: int
    entries: List[FetchEntry] = field(default_factory=list)
    items_found: int = 0          # raw items pulled upstream (before filtering)
    status: str = "success"       # success | partial | error | skipped
    error: Optional[str] = None
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: Optional[datetime] = None

    def mark_done(self, status: str = "success", error: Optional[str] = None) -> "FetchResult":
        self.status = status
        self.error = error
        self.finished_at = datetime.now(timezone.utc)
        return self


# ── Base adapter ────────────────────────────────────────────────────────────

class FetcherAdapter:
    """
    Subclass this and set `source_type` to something unique. Implement `fetch`.

    The runner passes in the `source_row` dict straight from hermes_sources
    so the adapter can read url, notes (for per-source config hints), and id.
    """

    source_type: str = ""                  # must match hermes_sources.source_type
    default_credibility: str = "tier3"     # suggested tier for seeded rows

    # Optional per-adapter env var requirements; runner warns if unset.
    required_env: List[str] = []

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        raise NotImplementedError(
            f"{self.__class__.__name__}.fetch() not implemented"
        )

    # Helpers shared across adapters ────────────────────────────────────────

    @staticmethod
    def stable_hash(*parts: Any) -> str:
        """Build a dedup key from arbitrary parts; shortened for readability."""
        joined = "|".join(str(p) for p in parts if p is not None)
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:32]

    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def http_json(
        url: str,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 20,
        retries: int = 2,
        backoff_seconds: float = 1.5,
    ) -> Dict[str, Any]:
        """
        Minimal httpx-free GET→JSON with retry. We avoid the heavier httpx import
        here so adapters stay lightweight; runner code can switch to httpx if
        concurrency becomes a concern.
        """
        import json as _json
        import urllib.request
        import urllib.error

        final_headers = {
            "User-Agent": "Mozilla/5.0 (HermesBot/0.1; +https://github.com/Lama-Gitit/hermes-agent)",
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            final_headers.update(headers)

        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=final_headers)
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    body = resp.read().decode("utf-8", errors="ignore")
                    if not body:
                        return {}
                    return _json.loads(body)
            except urllib.error.HTTPError as e:
                last_err = e
                # Don't retry 4xx other than 429
                if 400 <= e.code < 500 and e.code != 429:
                    raise
            except Exception as e:
                last_err = e
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
        assert last_err is not None
        raise last_err


# ── Registry ────────────────────────────────────────────────────────────────

REGISTRY: Dict[str, FetcherAdapter] = {}


def register(adapter_cls):
    """Decorator: `@register` on a FetcherAdapter subclass."""
    if not issubclass(adapter_cls, FetcherAdapter):
        raise TypeError(f"{adapter_cls} is not a FetcherAdapter subclass")
    if not adapter_cls.source_type:
        raise ValueError(f"{adapter_cls.__name__} has empty source_type")
    if adapter_cls.source_type in REGISTRY:
        logger.warning(
            "[fetchers] source_type %r re-registered; overwriting",
            adapter_cls.source_type,
        )
    REGISTRY[adapter_cls.source_type] = adapter_cls()
    return adapter_cls


def get_adapter(source_type: str) -> Optional[FetcherAdapter]:
    return REGISTRY.get(source_type)
