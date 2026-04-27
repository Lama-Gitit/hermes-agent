"""
Expose the ingestion runner as Hermes tools.

Two tools:
  - run_ingestion           : kick a fetch cycle (one source or all enabled)
  - seed_marketplace_sources : idempotently create the canonical vault +
                               marketplace rows in hermes_sources if missing

Plus one standalone CLI entrypoint (`python -m tools.fetchers.tools_api all`)
for cron jobs / scheduled tasks.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


# ── Handlers ────────────────────────────────────────────────────────────────

def _handle_run_ingestion(args: Dict[str, Any], **kw) -> str:
    from tools.fetchers import runner

    source_id = args.get("source_id")
    source_types = args.get("source_types")
    enabled_only = bool(args.get("enabled_only", True))

    if source_id is not None:
        out = runner.run_one(int(source_id))
    else:
        out = runner.run_all(
            enabled_only=enabled_only,
            only_source_types=source_types if isinstance(source_types, list) else None,
        )
    return json.dumps(out, default=str)


# Canonical seed rows for the marketplace sources we built adapters for.
# Each row is safe to insert once; the handler below skips inserts that
# would collide on name + url.
_SEEDS: List[Dict[str, Any]] = [
    {
        "name": "Courtyard — OpenSea aggregate stats",
        "url": "https://opensea.io/collection/courtyard-nft",
        "source_type": "courtyard_opensea",
        "credibility_tier": "tier2",
        "notes": "Aggregate floor / volume / window stats. Keyless.",
    },
    {
        "name": "Courtyard — Alchemy NFT metadata",
        "url": "https://polygon-mainnet.g.alchemy.com/nft/v3/",
        "source_type": "courtyard_alchemy",
        "credibility_tier": "tier1",
        "notes": json.dumps({"page_size": 100, "max_pages": 2}),
    },
    {
        "name": "Phygitals — Magic Eden",
        "url": "https://magiceden.io/marketplace/phygitals",
        "source_type": "phygitals_magiceden",
        "credibility_tier": "tier2",
        "notes": json.dumps({"collections": ["phygitals"]}),
    },
    {
        "name": "Beezie — web",
        "url": "https://beezie.com/marketplace",
        "source_type": "beezie_web",
        "credibility_tier": "tier3",
        "notes": "Stub — needs frontend endpoint capture",
    },
    {
        "name": "eBay — graded Pokémon (active)",
        "url": "https://www.ebay.com/sch/i.html?_nkw=pokemon+psa+10",
        "source_type": "ebay_browse",
        "credibility_tier": "tier2",
        "notes": json.dumps({"q": "pokemon charizard psa 10", "limit": 25}),
    },
    {
        "name": "TCGplayer",
        "url": "https://www.tcgplayer.com",
        "source_type": "tcgplayer_web",
        "credibility_tier": "tier2",
        "notes": "Stub — needs partner credentials",
    },
    {
        "name": "Card Ladder",
        "url": "https://www.cardladder.com",
        "source_type": "cardladder_web",
        "credibility_tier": "tier2",
        "notes": "Stub — needs partner credentials",
    },
    {
        "name": "Pokemon Price Tracker",
        "url": "https://www.pokemonpricetracker.com",
        "source_type": "pokemonpricetracker",
        "credibility_tier": "tier2",
        "notes": json.dumps({"card_ids": []}),
    },
]


def _handle_seed_marketplace_sources(args: Dict[str, Any], **kw) -> str:
    from tools.supabase_client import get_client

    client = get_client()
    if not client:
        return json.dumps({"error": "Supabase client not available"})

    try:
        existing = (
            client.table("hermes_sources")
            .select("id,name,url,source_type")
            .execute()
        ).data or []
    except Exception as e:
        return json.dumps({"error": f"could not read hermes_sources: {e}"})

    existing_key = {(r.get("name"), r.get("url")) for r in existing}
    to_insert = [r for r in _SEEDS if (r["name"], r["url"]) not in existing_key]

    inserted = []
    for row in to_insert:
        row = dict(row)
        row["enabled"] = True
        try:
            res = client.table("hermes_sources").insert(row).execute()
            if res.data:
                inserted.append(res.data[0])
        except Exception as e:
            logger.warning("[seed] insert %s failed: %s", row["name"], e)

    return json.dumps({
        "already_present": len(existing_key & {(r["name"], r["url"]) for r in _SEEDS}),
        "newly_inserted": len(inserted),
        "rows": [
            {"id": r.get("id"), "name": r.get("name"), "source_type": r.get("source_type")}
            for r in inserted
        ],
    })


# ── Tool schemas (OpenAI function-calling format) ───────────────────────────

RUN_INGESTION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_ingestion",
        "description": (
            "Run one ingestion cycle. Without arguments, runs every enabled "
            "source in hermes_sources. Pass `source_id` to target a single "
            "row, or `source_types=['courtyard_opensea', ...]` to run only "
            "adapters of those types. Returns per-source summaries "
            "(items_found, entries_inserted, deduped_out, status)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source_id": {
                    "type": "integer",
                    "description": "Run ingestion for just this hermes_sources.id.",
                },
                "source_types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Restrict to these source_type values.",
                },
                "enabled_only": {
                    "type": "boolean",
                    "description": "When no source_id is given, only run enabled rows. Default true.",
                },
            },
            "required": [],
        },
    },
}

SEED_MARKETPLACE_SOURCES_SCHEMA = {
    "type": "function",
    "function": {
        "name": "seed_marketplace_sources",
        "description": (
            "Idempotently insert the canonical marketplace + vault platform "
            "rows into hermes_sources. Safe to call repeatedly — only rows "
            "whose (name, url) pair is missing get inserted. Returns the "
            "number of rows added."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}


# ── Registry wiring ─────────────────────────────────────────────────────────
#
# Registration of `run_ingestion` and `seed_marketplace_sources` lives in
# `tools/marketplace_ingestion.py` — that's the file Hermes Agent's AST-based
# discovery (`_is_registry_register_call`) actually picks up. This module just
# defines the schemas, handlers, and CLI entrypoint that the registration
# imports from.


# ── CLI entrypoint (for cron / scheduled tasks) ─────────────────────────────

def _main(argv: List[str]) -> int:
    """
    python -m tools.fetchers.tools_api all
    python -m tools.fetchers.tools_api one <source_id>
    python -m tools.fetchers.tools_api seed
    python -m tools.fetchers.tools_api types courtyard_opensea phygitals_magiceden
    """
    # Ensure adapters are imported so they register.
    import tools.fetchers  # noqa: F401
    from tools.fetchers import runner

    if len(argv) < 2 or argv[1] == "all":
        out = runner.run_all(enabled_only=True)
    elif argv[1] == "one" and len(argv) >= 3:
        out = runner.run_one(int(argv[2]))
    elif argv[1] == "types" and len(argv) >= 3:
        out = runner.run_all(enabled_only=True, only_source_types=list(argv[2:]))
    elif argv[1] == "seed":
        out = json.loads(_handle_seed_marketplace_sources({}))
    else:
        print(
            "usage: tools_api [all | one <source_id> | types <t> [<t> ...] | seed]",
            file=sys.stderr,
        )
        return 2

    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
