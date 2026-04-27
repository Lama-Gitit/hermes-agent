"""
Top-level discovery shim for the marketplace fetcher framework.

Hermes Agent's `tools.registry.discover_builtin_tools()` does an AST parse
of every `tools/*.py` file and ONLY imports modules that contain a literal
`registry.register(...)` call at module level. Subdirectories like
`tools/fetchers/` are not scanned, and shim files that only re-export via
`import` are filtered out by the AST gate.

So we have to do two things in this single top-level file:
  1. Trigger import of the fetcher package (so all 8 adapters self-register
     into `tools.fetchers.base.REGISTRY`).
  2. Call `registry.register(...)` for `run_ingestion` and
     `seed_marketplace_sources` AT MODULE LEVEL, so the AST gate sees them
     and Hermes actually imports this file at startup.

Do not delete. Do not rename. Do not move into a subdirectory.
"""

# ── Step 1: import the fetcher package -----------------------------------
# This pulls in base.py, runner.py, and every adapter, so the
# REGISTRY of FetcherAdapter classes gets populated. We rely on
# tools/fetchers/__init__.py to import each adapter module.
import tools.fetchers  # noqa: F401

# Pull in the schemas and handlers that tools_api.py defines. We re-register
# them here (instead of letting tools_api.py do it) so that this module is
# the one that satisfies the AST discovery gate.
from tools.fetchers.tools_api import (  # noqa: E402
    RUN_INGESTION_SCHEMA,
    SEED_MARKETPLACE_SOURCES_SCHEMA,
    _handle_run_ingestion,
    _handle_seed_marketplace_sources,
)

# ── Step 2: register the Hermes-facing tools at MODULE LEVEL --------------
# These two `registry.register(...)` expressions are what make Hermes
# Agent's AST-based discovery (`_is_registry_register_call`) actually
# import this file at gateway startup.
from tools.registry import registry  # noqa: E402


def _check_supabase():
    """Gate the marketplace tools on Supabase being available."""
    try:
        from tools.supabase_client import is_available
        return is_available()
    except Exception:
        return False


registry.register(
    name="run_ingestion",
    toolset="supabase_tcg",
    schema=RUN_INGESTION_SCHEMA,
    handler=_handle_run_ingestion,
    check_fn=_check_supabase,
    emoji="📥",
    description="Run a marketplace ingestion cycle",
)

registry.register(
    name="seed_marketplace_sources",
    toolset="supabase_tcg",
    schema=SEED_MARKETPLACE_SOURCES_SCHEMA,
    handler=_handle_seed_marketplace_sources,
    check_fn=_check_supabase,
    emoji="🌱",
    description="Seed canonical marketplace sources",
)

# Sanity log so we can see in container startup what loaded.
try:
    from tools.fetchers.base import REGISTRY as _ADAPTER_REGISTRY
    print(
        f"[marketplace_ingestion] loaded — {len(_ADAPTER_REGISTRY)} adapters: "
        f"{sorted(_ADAPTER_REGISTRY.keys())}; "
        f"registered run_ingestion + seed_marketplace_sources",
        flush=True,
    )
except Exception as _e:
    print(f"[marketplace_ingestion] load WARNING: {_e}", flush=True)
