"""
Card Ladder adapter — stub.

Card Ladder indexes sold comps across marketplaces. They don't publish a
free API; for production wire this up via their data partner program or
scrape the publicly viewable card pages. Kept as a stub to preserve the
framework shape.
"""

from __future__ import annotations

from typing import Any, Dict

from tools.fetchers.base import FetcherAdapter, FetchResult, register


@register
class CardLadderWeb(FetcherAdapter):
    source_type = "cardladder_web"
    default_credibility = "tier2"

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        return FetchResult(source_id=source_row["id"]).mark_done(
            "skipped",
            "Card Ladder adapter stub — add credentials and implement fetch",
        )
