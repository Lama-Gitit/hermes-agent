"""
eBay Browse API adapter — graded-card sold-listings / active listings.

Requires an eBay developer account (free). Put the OAuth app token in
EBAY_OAUTH_TOKEN. The `notes` column of hermes_sources should hold a JSON
blob with per-row query config, e.g.:

    {
      "q": "pokemon charizard psa 10",
      "category_ids": "183454",
      "filter": "conditions:{USED},price:[50..]",
      "limit": 25
    }

eBay's public endpoint for active listings is:
    https://api.ebay.com/buy/browse/v1/item_summary/search

For sold listings (price history), the closest free path is the Marketplace
Insights API → item_sales/search. That one requires approval. Until then
this adapter handles active listings, which is enough for floor/arbitrage
signal.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any, Dict
from urllib.parse import urlencode

from tools.fetchers.base import FetcherAdapter, FetchEntry, FetchResult, register

logger = logging.getLogger(__name__)

_BROWSE_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


@register
class EbayBrowse(FetcherAdapter):
    source_type = "ebay_browse"
    default_credibility = "tier2"
    required_env = ["EBAY_OAUTH_TOKEN"]

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        source_id = source_row["id"]
        result = FetchResult(source_id=source_id)
        today = date.today().isoformat()

        token = os.environ.get("EBAY_OAUTH_TOKEN")
        if not token:
            return result.mark_done(
                "skipped",
                "EBAY_OAUTH_TOKEN not set — skipping eBay fetch",
            )

        try:
            cfg = json.loads(source_row.get("notes") or "{}")
        except Exception:
            cfg = {}

        params = {
            "q": cfg.get("q", "pokemon psa 10"),
            "limit": str(cfg.get("limit", 25)),
        }
        for k in ("category_ids", "filter", "sort"):
            if cfg.get(k):
                params[k] = str(cfg[k])

        url = f"{_BROWSE_URL}?{urlencode(params)}"
        try:
            data = self.http_json(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=25,
            )
        except Exception as e:
            return result.mark_done("error", f"eBay Browse fetch failed: {e}")

        items = data.get("itemSummaries") or []
        result.items_found = len(items)

        for it in items:
            price = (it.get("price") or {}).get("value")
            currency = (it.get("price") or {}).get("currency", "USD")
            item_id = it.get("itemId")
            if not item_id or price is None:
                continue
            result.entries.append(
                FetchEntry(
                    card_id="unresolved",
                    claim_type="price",
                    confidence="observed",
                    date_observed=today,
                    value={
                        "metric": "active_listing",
                        "platform": "ebay",
                        "title": it.get("title"),
                        "amount": float(price),
                        "currency": currency,
                        "condition": it.get("condition"),
                        "seller": (it.get("seller") or {}).get("username"),
                        "item_id": item_id,
                        "item_url": it.get("itemWebUrl"),
                        "image_url": (it.get("image") or {}).get("imageUrl"),
                        "observed_at": self.now_iso(),
                    },
                    source={
                        "url": it.get("itemWebUrl"),
                        "source_type": self.source_type,
                        "author": "ebay_browse_v1",
                        "author_credibility": self.default_credibility,
                        "query": params["q"],
                    },
                    dedup_key=self.stable_hash(source_id, item_id, float(price)),
                )
            )

        if not result.entries:
            return result.mark_done("partial", "no items returned from eBay Browse")
        return result.mark_done("success")
