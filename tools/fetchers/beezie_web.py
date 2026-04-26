"""
Beezie → frontend JSON adapter (stub, scrape-ready).

Beezie hasn't published a public API and its docs don't expose a blockchain
address. The production path here is to:
  1. Sniff beezie.com/marketplace with Playwright or a headless browser.
  2. Identify the SPA's internal JSON endpoints.
  3. Replay them from this adapter with an httpx client.

For now this adapter is wired but returns `status='skipped'` with guidance.
To enable it, fill in `_fetch_listings()` with the real endpoint + auth
scheme once you've inspected the site, and remove the early return below.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from tools.fetchers.base import FetcherAdapter, FetchResult, register

logger = logging.getLogger(__name__)


@register
class BeezieWeb(FetcherAdapter):
    source_type = "beezie_web"
    default_credibility = "tier2"

    def fetch(self, source_row: Dict[str, Any]) -> FetchResult:
        result = FetchResult(source_id=source_row["id"])
        # TODO: implement once the frontend endpoints are captured.
        # Typical steps:
        #   1. Open https://beezie.com/marketplace in DevTools.
        #   2. Watch the network tab for XHR calls (likely under some
        #      api.beezie.com or /_next/data/{buildId}/ path).
        #   3. Replay the request here with self.http_json.
        #   4. Normalize each listing into a FetchEntry with claim_type="price".
        return result.mark_done(
            "skipped",
            "beezie_web adapter not implemented — see file for integration steps",
        )
