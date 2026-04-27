"""
Deprecated. Kept as a placeholder so old imports don't break.

The marketplace ingestion tools (`run_ingestion`, `seed_marketplace_sources`)
are now registered directly inside `tools/supabase_tcg.py`, because Hermes
Agent's tool loader reliably picks up that file but does NOT pick up this
one (mechanism unclear — possibly a hardcoded import list somewhere).

Schemas + handlers still live in `tools/fetchers/tools_api.py`.
Adapter classes still live in `tools/fetchers/*.py`.

If you ever figure out how to make Hermes auto-load this file, you can
move the registrations back here for cleaner separation. Until then, leave
this file empty so it does not cause double-registration.
"""
