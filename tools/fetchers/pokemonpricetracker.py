"""
PokemonPriceTracker adapter — uses their real-time price API.

pokemonpricetracker.com publishes a REST API that returns live prices for
individual cards. Free tier requires an API key via POKEMONPRICETRACKER_API_KEY.
The `notes` column should carry:

    {"card_ids": ["swsh12pt5-160", "sv2-125"]}

One FetchEntry per card per run is emitted with `claim_type="price"`.

Docs: https://docs.pokemonpricetracker.com
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Any, Dict, List

from tools.fetchers.base import FetcherAdapter, FetchEntry, FetchResult, register

logger = logging.getLogger(__name__)

_BASE = "https://www.pokemonpricetracker.com/api/v1"


@register
class PokemonPriceTracker(FetcherAdapter):
    source_type = "pokemonpricetracker"
    default_credibility = "tier2"
    required_env = ["POKEMONPRICETRACKER_API_KEY"]

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        source_id = source_row["id"]
        result = FetchResult(source_id=source_id)
        today = date.today().isoformat()

        key = os.environ.get("POKEMONPRICETRACKER_API_KEY")
        if not key:
            return result.mark_done(
                "skipped",
                "POKEMONPRICETRACKER_API_KEY not set — skipping",
            )

        try:
            cfg = json.loads(source_row.get("notes") or "{}")
        except Exception:
            cfg = {}
        card_ids: List[str] = cfg.get("card_ids") or []
        if not card_ids:
            return result.mark_done("skipped", "no card_ids configured in notes")

        headers = {"Authorization": f"Bearer {key}"}

        for cid in card_ids:
            try:
                data = self.http_json(
                    f"{_BASE}/prices?id={cid}",
                    headers=headers,
                    timeout=20,
                )
            except Exception as e:
                logger.warning("[pokemonpricetracker] %s failed: %s", cid, e)
                continue

            result.items_found += 1
            # Shape varies; try to extract market, low, mid
            prices = data.get("data") or data
            if not isinstance(prices, dict):
                continue

            result.entries.append(
                FetchEntry(
                    card_id=cid,  # using their canonical format e.g. swsh12pt5-160
                    claim_type="price",
                    confidence="observed",
                    date_observed=today,
                    value={
                        "metric": "aggregator_price",
                        "platform": "pokemonpricetracker",
                        "prices": prices,
                        "observed_at": self.now_iso(),
                    },
                    source={
                        "url": f"https://www.pokemonpricetracker.com/cards/{cid}",
                        "source_type": self.source_type,
                        "author": "pokemonpricetracker_v1",
                        "author_credibility": self.default_credibility,
                    },
                    dedup_key=self.stable_hash(source_id, cid, today),
                )
            )

        if not result.entries:
            return result.mark_done("partial", "no prices returned")
        return result.mark_done("success")
