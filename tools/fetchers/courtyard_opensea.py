"""
Courtyard → OpenSea aggregate stats adapter.

Why this exists:
  Courtyard is 100 % on Polygon (ERC-721 at
  0x251be3a17af4892035c37ebf5890f4a4d889dcad) but their own marketplace data
  lives on a private api.courtyard.io behind Privy auth — not reachable
  without a key. OpenSea's v2 `collections/{slug}/stats` endpoint, however,
  is keyless and gives us aggregate market_cap, floor_price, volume and
  sales counts for 1d / 7d / 30d windows. That's enough for Hermes to track
  Courtyard's macro market, even if per-listing fidelity needs a key later.

What it writes:
  - one `fundamental` entry per interval (one_day, seven_day, thirty_day)
    with volume, sales, average_price, volume_change
  - one `price` entry with the current collection floor
  - dedup_key = sha(source_id, interval, date) — new rows land daily

Stability:
  Only dependency is a public OpenSea endpoint. No auth. No scraping.
  Degrades to status='error' cleanly if the endpoint shape changes.
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
            return result.mark_done("error", f"OpenSea stats fetch failed: {e}")

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
