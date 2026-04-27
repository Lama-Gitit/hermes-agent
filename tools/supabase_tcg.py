"""
Supabase TCG Tools — Hermes knowledge-base persistence.

Registers SIX tools via the Hermes tool registry:
  - save_entry              : persist one atomic claim to hermes_entries
  - query_entries           : search/filter past entries
  - list_sources            : list watched sources from hermes_sources
  - add_source              : register a new watched source
  - run_ingestion           : run a marketplace ingestion cycle
  - seed_marketplace_sources: idempotently seed canonical marketplace rows

All tools are gated on SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY being set.

The last two tools are defined in `tools.fetchers.tools_api` (schemas + handlers)
but registered here because Hermes Agent's tool loader picks up this file
reliably, while it does NOT recurse into `tools/fetchers/` subdirectory.
Co-locating the registration calls with the proven-working save_entry et al.
guarantees they reach the registry at gateway startup.
"""

import json
import logging
from datetime import date, datetime
from typing import Any, Dict

# Trigger import of the fetcher package — this populates the adapter
# REGISTRY (in tools/fetchers/base.py) with all 8 marketplace adapters
# so the runner can find them by source_type.
import tools.fetchers  # noqa: F401

logger = logging.getLogger(__name__)


# ── Availability check ──────────────────────────────────────────────────
def _check_supabase():
    from tools.supabase_client import is_available
    return is_available()


# ── Handlers ────────────────────────────────────────────────────────────

def _handle_save_entry(args: Dict[str, Any], **kw) -> str:
    """Persist one atomic claim to hermes_entries."""
    from tools.supabase_client import get_client

    client = get_client()
    if not client:
        return json.dumps({"error": "Supabase client not available — check env vars"})

    card_id = args.get("card_id", "unresolved")
    claim_type = args.get("claim_type", "")
    value = args.get("value")
    source = args.get("source")
    confidence = args.get("confidence", "speculative")
    date_observed = args.get("date_observed", date.today().isoformat())

    if not claim_type:
        return json.dumps({"error": "claim_type is required (price|sentiment|fundamental|hypothesis)"})

    row = {
        "card_id": card_id,
        "claim_type": claim_type,
        "value": value if isinstance(value, dict) else {"raw": value},
        "source": source if isinstance(source, dict) else {"raw": source},
        "confidence": confidence,
        "date_observed": date_observed,
        "date_processed": date.today().isoformat(),
    }

    try:
        result = client.table("hermes_entries").insert(row).execute()
        entry = result.data[0] if result.data else {}
        return json.dumps({
            "status": "saved",
            "entry_id": entry.get("entry_id", ""),
            "card_id": card_id,
            "claim_type": claim_type,
        })
    except Exception as e:
        logger.error("[supabase_tcg] save_entry failed: %s", e)
        return json.dumps({"error": f"save_entry failed: {str(e)[:300]}"})


def _handle_query_entries(args: Dict[str, Any], **kw) -> str:
    """Search/filter past entries in hermes_entries."""
    from tools.supabase_client import get_client

    client = get_client()
    if not client:
        return json.dumps({"error": "Supabase client not available — check env vars"})

    card_id = args.get("card_id")
    claim_type = args.get("claim_type")
    date_observed_gte = args.get("date_observed_gte")
    date_observed_lte = args.get("date_observed_lte")
    limit = min(args.get("limit", 20), 100)

    try:
        query = client.table("hermes_entries").select("*")

        if card_id:
            # Support prefix matching: "base1-4" matches "base1-4-1stEd-..."
            if card_id.endswith("*"):
                query = query.like("card_id", card_id.replace("*", "%"))
            else:
                query = query.eq("card_id", card_id)

        if claim_type:
            query = query.eq("claim_type", claim_type)

        if date_observed_gte:
            query = query.gte("date_observed", date_observed_gte)

        if date_observed_lte:
            query = query.lte("date_observed", date_observed_lte)

        query = query.order("date_observed", desc=True).limit(limit)
        result = query.execute()

        entries = result.data or []
        return json.dumps({
            "count": len(entries),
            "entries": entries,
        }, default=str)
    except Exception as e:
        logger.error("[supabase_tcg] query_entries failed: %s", e)
        return json.dumps({"error": f"query_entries failed: {str(e)[:300]}"})


def _handle_list_sources(args: Dict[str, Any], **kw) -> str:
    """List watched sources from hermes_sources."""
    from tools.supabase_client import get_client

    client = get_client()
    if not client:
        return json.dumps({"error": "Supabase client not available — check env vars"})

    enabled_only = args.get("enabled_only", False)

    try:
        query = client.table("hermes_sources").select("*")
        if enabled_only:
            query = query.eq("enabled", True)

        query = query.order("name")
        result = query.execute()

        sources = result.data or []
        return json.dumps({
            "count": len(sources),
            "sources": sources,
        }, default=str)
    except Exception as e:
        logger.error("[supabase_tcg] list_sources failed: %s", e)
        return json.dumps({"error": f"list_sources failed: {str(e)[:300]}"})


def _handle_add_source(args: Dict[str, Any], **kw) -> str:
    """Register a new watched source in hermes_sources."""
    from tools.supabase_client import get_client

    client = get_client()
    if not client:
        return json.dumps({"error": "Supabase client not available — check env vars"})

    name = args.get("name", "").strip()
    url = args.get("url", "").strip()
    source_type = args.get("source_type", "web_article")
    credibility_tier = args.get("credibility_tier", "tier3")
    notes = args.get("notes", "")

    if not name or not url:
        return json.dumps({"error": "name and url are required"})

    row = {
        "name": name,
        "url": url,
        "source_type": source_type,
        "credibility_tier": credibility_tier,
        "enabled": True,
        "notes": notes,
    }

    try:
        result = client.table("hermes_sources").insert(row).execute()
        source = result.data[0] if result.data else {}
        return json.dumps({
            "status": "added",
            "id": source.get("id", ""),
            "name": name,
            "url": url,
        })
    except Exception as e:
        logger.error("[supabase_tcg] add_source failed: %s", e)
        return json.dumps({"error": f"add_source failed: {str(e)[:300]}"})


# ── Tool Schemas (OpenAI function-calling format) ───────────────────────

SAVE_ENTRY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "save_entry",
        "description": (
            "Persist one atomic claim about a Pokémon card to the knowledge base. "
            "Each entry captures a single fact: a price observation, a sentiment signal, "
            "a fundamental data point, or a hypothesis. Use the canonical card_id format: "
            "<set_code>-<card_number>-<variant>-<language>-<grade>. "
            "If any field is unknown, set card_id to 'unresolved'. "
            "Always call query_entries first to check for existing data before saving duplicates."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": (
                        "Canonical card ID: <set_code>-<card_number>-<variant>-<language>-<grade>. "
                        "Examples: 'base1-4-1stEd-shadowless-EN-raw', 'sv08-238-alt-EN-psa10'. "
                        "Use 'unresolved' if any component is unknown."
                    ),
                },
                "claim_type": {
                    "type": "string",
                    "enum": ["price", "sentiment", "fundamental", "hypothesis"],
                    "description": "Type of claim being recorded.",
                },
                "value": {
                    "type": "object",
                    "description": (
                        "Claim payload. Shape varies by claim_type: "
                        "price → {amount, currency, platform, condition}; "
                        "sentiment → {direction, strength, summary}; "
                        "fundamental → {fact, source_detail}; "
                        "hypothesis → {thesis, supporting_evidence, timeframe}."
                    ),
                },
                "source": {
                    "type": "object",
                    "description": (
                        "Where this claim came from. "
                        "{url, source_type, author, author_credibility} — "
                        "author_credibility: tier1 (fundamentals), tier2 (prices), tier3 (sentiment)."
                    ),
                    "properties": {
                        "url": {"type": "string"},
                        "source_type": {"type": "string"},
                        "author": {"type": "string"},
                        "author_credibility": {"type": "string"},
                    },
                },
                "confidence": {
                    "type": "string",
                    "enum": ["canonical", "observed", "claimed", "user_hypothesis", "speculative"],
                    "description": "How trustworthy is this claim?",
                },
                "date_observed": {
                    "type": "string",
                    "description": "ISO date when the claim was observed (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": ["card_id", "claim_type", "value", "source", "confidence"],
        },
    },
}

QUERY_ENTRIES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "query_entries",
        "description": (
            "Search the knowledge base for past entries about Pokémon cards. "
            "Use before answering any question that needs historical data. "
            "Supports filtering by card_id (exact or prefix with *), claim_type, and date range."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "card_id": {
                    "type": "string",
                    "description": (
                        "Filter by card_id. Exact match or prefix with * "
                        "(e.g. 'base1-4*' matches all Base Set Charizard variants)."
                    ),
                },
                "claim_type": {
                    "type": "string",
                    "enum": ["price", "sentiment", "fundamental", "hypothesis"],
                    "description": "Filter by claim type.",
                },
                "date_observed_gte": {
                    "type": "string",
                    "description": "Only entries observed on or after this date (YYYY-MM-DD).",
                },
                "date_observed_lte": {
                    "type": "string",
                    "description": "Only entries observed on or before this date (YYYY-MM-DD).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max entries to return (default 20, max 100).",
                },
            },
            "required": [],
        },
    },
}

LIST_SOURCES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_sources",
        "description": (
            "List watched sources registered for batched ingestion. "
            "Shows name, URL, source type, credibility tier, and enabled status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "enabled_only": {
                    "type": "boolean",
                    "description": "If true, only return enabled sources. Default false.",
                },
            },
            "required": [],
        },
    },
}

ADD_SOURCE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "add_source",
        "description": (
            "Register a new watched source for the ingestion pipeline. "
            "Sources are checked periodically and new claims are extracted and saved as entries."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Human-readable name for this source.",
                },
                "url": {
                    "type": "string",
                    "description": "URL to watch (RSS feed, subreddit, YouTube channel, web page, etc.).",
                },
                "source_type": {
                    "type": "string",
                    "enum": ["reddit", "rss", "twitter", "youtube_channel", "web_article"],
                    "description": "Type of source for the fetcher to use.",
                },
                "credibility_tier": {
                    "type": "string",
                    "enum": ["tier1", "tier2", "tier3"],
                    "description": (
                        "tier1 = fundamentals (Bulbapedia, PSA pop reports), "
                        "tier2 = prices (TCGplayer, eBay sold), "
                        "tier3 = sentiment (Reddit, YouTube, Twitter)."
                    ),
                },
                "notes": {
                    "type": "string",
                    "description": "Optional notes about this source.",
                },
            },
            "required": ["name", "url", "source_type", "credibility_tier"],
        },
    },
}


# ── Registry ────────────────────────────────────────────────────────────
from tools.registry import registry

registry.register(
    name="save_entry",
    toolset="supabase_tcg",
    schema=SAVE_ENTRY_SCHEMA,
    handler=_handle_save_entry,
    check_fn=_check_supabase,
    emoji="💾",
    description="Save a knowledge entry about a Pokémon card",
)

registry.register(
    name="query_entries",
    toolset="supabase_tcg",
    schema=QUERY_ENTRIES_SCHEMA,
    handler=_handle_query_entries,
    check_fn=_check_supabase,
    emoji="🔍",
    description="Search the card knowledge base",
)

registry.register(
    name="list_sources",
    toolset="supabase_tcg",
    schema=LIST_SOURCES_SCHEMA,
    handler=_handle_list_sources,
    check_fn=_check_supabase,
    emoji="📡",
    description="List watched sources for ingestion",
)

registry.register(
    name="add_source",
    toolset="supabase_tcg",
    schema=ADD_SOURCE_SCHEMA,
    handler=_handle_add_source,
    check_fn=_check_supabase,
    emoji="➕",
    description="Add a new watched source",
)


# ── Marketplace ingestion tools ─────────────────────────────────────────
# Schemas + handlers live in tools/fetchers/tools_api.py, but registration
# happens here for the same reason as above — Hermes's tool loader sees
# this file but not the subdirectory.

from tools.fetchers.tools_api import (  # noqa: E402
    RUN_INGESTION_SCHEMA,
    SEED_MARKETPLACE_SOURCES_SCHEMA,
    _handle_run_ingestion,
    _handle_seed_marketplace_sources,
)

registry.register(
    name="run_ingestion",
    toolset="supabase_tcg",
    schema=RUN_INGESTION_SCHEMA,
    handler=_handle_run_ingestion,
    check_fn=_check_supabase,
    emoji="📥",
    description="Run a marketplace ingestion cycle",
)

registry.register(
    name="seed_marketplace_sources",
    toolset="supabase_tcg",
    schema=SEED_MARKETPLACE_SOURCES_SCHEMA,
    handler=_handle_seed_marketplace_sources,
    check_fn=_check_supabase,
    emoji="🌱",
    description="Seed canonical marketplace sources",
)

# Sanity log — visible in deployment logs immediately after gateway startup.
try:
    from tools.fetchers.base import REGISTRY as _ADAPTER_REGISTRY
    print(
        f"[supabase_tcg] marketplace tools registered — "
        f"{len(_ADAPTER_REGISTRY)} adapters: {sorted(_ADAPTER_REGISTRY.keys())}",
        flush=True,
    )
except Exception as _e:
    print(f"[supabase_tcg] marketplace registration WARNING: {_e}", flush=True)
