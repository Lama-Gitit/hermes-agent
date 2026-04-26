"""
Hermes marketplace fetcher framework.

One adapter per source_type. Each adapter:
  1. Pulls fresh data from its upstream (on-chain API, site endpoint, or scrape).
  2. Normalises rows into `hermes_entries` (claim_type = price | fundamental).
  3. Returns a FetchResult so the runner can log to `hermes_ingestion_jobs`.

The base class is in `base.py`. Adapters register themselves in `REGISTRY`.
"""

from tools.fetchers.base import (
    FetcherAdapter,
    FetchResult,
    FetchEntry,
    REGISTRY,
    register,
)

# Importing adapter modules triggers their @register decorators.
# Keep this list sorted; add new adapters as they come online.
from tools.fetchers import (
    courtyard_opensea,      # source_type = "courtyard_opensea"
    courtyard_alchemy,      # source_type = "courtyard_alchemy"
    phygitals_magiceden,    # source_type = "phygitals_magiceden"
    beezie_web,             # source_type = "beezie_web"
    ebay_browse,            # source_type = "ebay_browse"
    tcgplayer_web,          # source_type = "tcgplayer_web"
    cardladder_web,         # source_type = "cardladder_web"
    pokemonpricetracker,    # source_type = "pokemonpricetracker"
)  # noqa: F401

# Importing tools_api triggers the Hermes-facing tool registrations
# (run_ingestion, seed_marketplace_sources) with the registry.
from tools.fetchers import tools_api  # noqa: F401

__all__ = [
    "FetcherAdapter",
    "FetchResult",
    "FetchEntry",
    "REGISTRY",
    "register",
]
