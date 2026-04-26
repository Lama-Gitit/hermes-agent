"""
Phygitals → Magic Eden (Solana) aggregate + listings adapter.

Phygitals is Solana-based. Their own collection slug(s) on Magic Eden are
the stable way to read listings + floor. `notes` in hermes_sources should
contain a JSON with the ME collection symbols, e.g.:

    {"collections": ["phygitals", "phygitals_pokemon"]}

If `notes` is empty we try a default symbol of "phygitals".
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, List

from tools.fetchers.base import FetcherAdapter, FetchEntry, FetchResult, register

logger = logging.getLogger(__name__)

_BASE = "https://api-mainnet.magiceden.dev/v2"


@register
class PhygitalsMagicEden(FetcherAdapter):
    source_type = "phygitals_magiceden"
    default_credibility = "tier2"

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        source_id = source_row["id"]
        result = FetchResult(source_id=source_id)
        today = date.today().isoformat()

        # Parse collection symbols from notes
        collections: List[str] = ["phygitals"]
        try:
            cfg = json.loads(source_row.get("notes") or "{}")
            if isinstance(cfg.get("collections"), list):
                collections = [str(x) for x in cfg["collections"]]
        except Exception:
            pass

        for symbol in collections:
            # Collection stats
            try:
                stats = self.http_json(f"{_BASE}/collections/{symbol}/stats", timeout=20)
                result.items_found += 1
                if stats and isinstance(stats, dict):
                    result.entries.append(
                        FetchEntry(
                            card_id=f"collection:phygitals:{symbol}",
                            claim_type="fundamental",
                            confidence="observed",
                            date_observed=today,
                            value={
                                "metric": "me_collection_stats",
                                "symbol": symbol,
                                "floor_price_lamports": stats.get("floorPrice"),
                                "listed_count": stats.get("listedCount"),
                                "avg_price_24h_lamports": stats.get("avgPrice24hr"),
                                "volume_all_lamports": stats.get("volumeAll"),
                                "observed_at": self.now_iso(),
                            },
                            source={
                                "url": f"https://magiceden.io/marketplace/{symbol}",
                                "source_type": self.source_type,
                                "author": "magiceden_v2",
                                "author_credibility": self.default_credibility,
                                "chain": "solana",
                            },
                            dedup_key=self.stable_hash(source_id, "me_stats", symbol, today),
                        )
                    )
            except Exception as e:
                logger.warning("[phygitals_magiceden] stats %s failed: %s", symbol, e)

            # Active listings (top 20 by price asc)
            try:
                listings = self.http_json(
                    f"{_BASE}/collections/{symbol}/listings?offset=0&limit=20",
                    timeout=20,
                )
                if isinstance(listings, list):
                    result.items_found += len(listings)
                    for li in listings:
                        if not isinstance(li, dict):
                            continue
                        mint = li.get("tokenMint") or li.get("mint")
                        price_sol = li.get("price")
                        if mint is None or price_sol is None:
                            continue
                        result.entries.append(
                            FetchEntry(
                                card_id=f"phygitals:{mint}",
                                claim_type="price",
                                confidence="observed",
                                date_observed=today,
                                value={
                                    "metric": "active_listing",
                                    "symbol": symbol,
                                    "mint": mint,
                                    "amount": float(price_sol),
                                    "currency": "SOL",
                                    "seller": li.get("seller"),
                                    "platform": "phygitals_magiceden",
                                    "observed_at": self.now_iso(),
                                },
                                source={
                                    "url": f"https://magiceden.io/item-details/{mint}",
                                    "source_type": self.source_type,
                                    "author": "magiceden_v2",
                                    "author_credibility": self.default_credibility,
                                    "chain": "solana",
                                },
                                dedup_key=self.stable_hash(
                                    source_id, "me_ask", mint, float(price_sol)
                                ),
                            )
                        )
            except Exception as e:
                logger.warning("[phygitals_magiceden] listings %s failed: %s", symbol, e)

        if not result.entries:
            return result.mark_done("partial", "no entries for Phygitals collections")
        return result.mark_done("success")
