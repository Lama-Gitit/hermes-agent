# Hermes marketplace fetcher framework

Adds ingestion of TCG vault marketplaces and price aggregators into
`hermes_entries` and `hermes_ingestion_jobs`.

## Architecture

```
hermes_sources (one row per watched endpoint)
       │
       ▼
runner.run_all()    ──►  REGISTRY[source_type].fetch(source_row)
       │                           │
       │                           ▼
       │                    List[FetchEntry]
       │                           │
       ├── dedup against last 30d  ◄ dedup_key lookup on value->>'dedup_key'
       ├── bulk insert survivors ───► hermes_entries
       ├── log summary row ────────► hermes_ingestion_jobs
       └── bump last_checked_at ───► hermes_sources
```

## Adapters (all self-register via `@register`)

| source_type            | data                                                  | auth                         | status   |
|------------------------|-------------------------------------------------------|------------------------------|----------|
| courtyard_opensea      | aggregate floor / volume / 1d-7d-30d windows          | keyless                      | live ✅  |
| courtyard_alchemy      | per-token on-chain catalog + hydrated slab metadata   | Alchemy demo / free key      | live ✅  |
| phygitals_magiceden    | Solana floor + active listings                         | keyless                      | live ✅  |
| beezie_web             | Beezie marketplace listings                            | needs frontend capture        | stub     |
| ebay_browse            | eBay active listings (graded cards)                    | `EBAY_OAUTH_TOKEN`           | pending key |
| tcgplayer_web          | TCGplayer prices                                       | `TCGPLAYER_BEARER_TOKEN`     | pending key |
| cardladder_web         | Card Ladder comps                                      | partner access               | stub     |
| pokemonpricetracker    | aggregated Pokémon card prices                         | `POKEMONPRICETRACKER_API_KEY`| pending key |

Adapters that don't have credentials degrade to `status='skipped'` in
`hermes_ingestion_jobs` instead of crashing — so the runner stays green.

## Seeding

First-time setup, from the Hermes CLI or via the Telegram bot, call:

```
seed_marketplace_sources()
```

This idempotently creates one `hermes_sources` row per adapter with a
sensible default `notes` JSON.

## Running manually

From Telegram / Hermes chat:

```
run_ingestion()                                  # all enabled sources
run_ingestion(source_id=3)                        # one row
run_ingestion(source_types=["courtyard_opensea"]) # by type
```

From a shell on the NodeOps container (cron-friendly):

```
python -m tools.fetchers.tools_api all
python -m tools.fetchers.tools_api one 3
python -m tools.fetchers.tools_api types courtyard_opensea phygitals_magiceden
python -m tools.fetchers.tools_api seed
```

## Writing a new adapter

1. Create `tools/fetchers/<your_source>.py`.
2. Subclass `FetcherAdapter`, set `source_type` to a unique string, implement `fetch(source_row)` returning a `FetchResult` with a list of `FetchEntry`s.
3. Decorate the class with `@register`.
4. Add the module to the import list in `tools/fetchers/__init__.py`.
5. Add a seed row in `tools_api.py` `_SEEDS` if you want `seed_marketplace_sources` to know about it.
6. Add any required env vars to `write_env.py`.

## Claim shapes

All entries land in `hermes_entries` using the existing schema. Recommended
`claim_type` and `value.metric` conventions:

| claim_type    | value.metric           | fields                                            |
|---------------|------------------------|---------------------------------------------------|
| `price`       | `collection_floor`     | amount, currency, platform                        |
| `price`       | `active_listing`       | amount, currency, platform, seller, item_id       |
| `price`       | `aggregator_price`     | platform, prices                                  |
| `fundamental` | `market_window`        | window, volume, sales, average_price, volume_change |
| `fundamental` | `vaulted_token`        | token_id, attributes, image_url                   |
| `fundamental` | `me_collection_stats`  | symbol, floor_price_lamports, listed_count        |

`dedup_key` is stashed inside the `value` JSONB so the runner can skip
duplicates without a schema migration.
