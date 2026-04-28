"""
Courtyard → OpenSea aggregate stats adapter. **DEPRECATED 2026-04-28.**

OpenSea's v2 stats endpoint now requires X-API-KEY auth (returns 401 for
anonymous requests). Rather than adding yet another vendor + API key + rate
limit surface, the architectural decision is to read the chain directly
via Alchemy (we already have ALCHEMY_POLYGON_API_KEY). The replacement
lives in tools/fetchers/courtyard_alchemy.py — that adapter currently
fetches NFT metadata; the next change is to add Alchemy NFT API calls for
floor prices (`getFloorPrice`) and sales history (`getNFTSales`), which
covers the same data this OpenSea adapter was producing.

Until courtyard_alchemy is enhanced, this adapter is left in place but
the corresponding hermes_sources row should be marked `enabled = false`
so the cron stops erroring on it nightly. To re-enable later if Alchemy
proves insufficient, set OPENSEA_API_KEY as an env var.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict

from tools.fetchers.base import FetcherAdapter, FetchEntry, FetchResult, register

logger = logging.getLogger(__name__)

_COLLECTION_SLUG = "courtyard-nft"
_CHAIN = "polygon"
_CONTRACT = "0x251be3a17af4892035c37ebf5890f4a4d889dcad"

_STATS_URL = f"https://api.opensea.io/api/v2/collections/{_COLLECTION_SLUG}/stats"


@register
class CourtyardOpenSeaStats(FetcherAdapter):
    source_type = "courtyard_opensea"
    default_credibility = "tier2"   # prices-tier source

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        source_id = source_row["id"]
        result = FetchResult(source_id=source_id)
        today = date.today().isoformat()

        try:
            data = self.http_json(_STATS_URL, timeout=20, retries=2)
        except Exception as e:
            return result.mark_done(
                "error",
                f"OpenSea stats fetch failed: {e}. "
                "This adapter is deprecated — the corresponding hermes_sources "
                "row should be disabled. See tools/fetchers/courtyard_alchemy.py "
                "for the chain-direct replacement.",
            )

        total = data.get("total") or {}
        intervals = data.get("intervals") or []
        result.items_found = 1 + len(intervals)

        common_source = {
            "url": f"https://opensea.io/collection/{_COLLECTION_SLUG}",
            "source_type": self.source_type,
            "author": "opensea_v2_api",
            "author_credibility": self.default_credibility,
            "chain": _CHAIN,
            "contract": _CONTRACT,
        }

        # 1) Floor price snapshot → claim_type=price
        floor = total.get("floor_price")
        symbol = total.get("floor_price_symbol") or "USDC"
        if floor is not None:
            result.entries.append(
                FetchEntry(
                    card_id="collection:courtyard",
                    claim_type="price",
                    confidence="observed",
                    date_observed=today,
                    value={
                        "metric": "collection_floor",
                        "amount": float(floor),
                        "currency": symbol,
                        "platform": "courtyard",
                        "num_owners": total.get("num_owners"),
                        "market_cap_native": total.get("market_cap"),
                        "total_sales": total.get("sales"),
                        "total_volume_native": total.get("volume"),
                        "observed_at": self.now_iso(),
                    },
                    source=common_source,
                    dedup_key=self.stable_hash(
                        source_id, "floor", today
                    ),
                )
            )

        # 2) Interval windows → claim_type=fundamental
        for iv in intervals:
            interval_name = iv.get("interval") or "unknown"
            result.entries.append(
                FetchEntry(
                    card_id="collection:courtyard",
                    claim_type="fundamental",
                    confidence="observed",
                    date_observed=today,
                    value={
                        "metric": "market_window",
                        "window": interval_name,
                        "volume_native": iv.get("volume"),
                        "volume_change_pct": iv.get("volume_change"),
                        "volume_diff_native": iv.get("volume_diff"),
                        "sales": iv.get("sales"),
                        "sales_diff": iv.get("sales_diff"),
                        "average_price_native": iv.get("average_price"),
                        "currency": symbol,
                        "observed_at": self.now_iso(),
                    },
                    source=common_source,
                    dedup_key=self.stable_hash(
                        source_id, "interval", interval_name, today
                    ),
                )
            )

        if not result.entries:
            return result.mark_done("partial", "OpenSea returned no usable fields")

        return result.mark_done("success")
