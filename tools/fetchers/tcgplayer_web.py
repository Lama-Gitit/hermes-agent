"""
TCGplayer adapter — stub. TCGplayer's v1 API was deprecated and the new
marketplace program requires an application. Until credentials land, this
adapter is registered but inert, so the runner logs a clean `skipped` row.

When you get approved, implement `_fetch()` below to hit
    https://api.tcgplayer.com/catalog/products
    https://api.tcgplayer.com/pricing/product/{productId}
with TCGPLAYER_BEARER_TOKEN from env.
"""

from __future__ import annotations

from typing import Any, Dict

from tools.fetchers.base import FetcherAdapter, FetchResult, register


@register
class TCGplayerWeb(FetcherAdapter):
    source_type = "tcgplayer_web"
    default_credibility = "tier2"
    required_env = ["TCGPLAYER_BEARER_TOKEN"]

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        return FetchResult(source_id=source_row["id"]).mark_done(
            "skipped",
            "TCGplayer adapter stub — add credentials and implement fetch",
        )
