"""
Auto-discovery shim for the marketplace fetcher framework.

Hermes Agent auto-imports every top-level file under `tools/` at startup,
but does NOT recurse into subdirectories. Our fetcher framework lives in
`tools/fetchers/`, so without this shim the @register decorators and
registry.register() calls inside that package never run, and no agent
tool ever shows up.

Importing the package here is enough — `tools.fetchers.__init__` pulls
in every adapter module (which self-register via @register) and then
imports `tools.fetchers.tools_api` (which registers the two Hermes
agent tools `run_ingestion` and `seed_marketplace_sources`).

Do not delete. Do not move into a subdirectory.
"""

# noqa: F401 — these imports are intentional side-effects.
import tools.fetchers  # noqa: F401
from tools.fetchers import tools_api  # noqa: F401

# Tiny sanity log so the tool count is visible in container startup output.
try:
    from tools.fetchers.base import REGISTRY as _ADAPTER_REGISTRY
    print(
        f"[marketplace_ingestion] loaded — {len(_ADAPTER_REGISTRY)} adapters: "
        f"{sorted(_ADAPTER_REGISTRY.keys())}",
        flush=True,
    )
except Exception as _e:
    print(f"[marketplace_ingestion] load WARNING: {_e}", flush=True)
