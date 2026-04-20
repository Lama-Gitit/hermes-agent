import os
import sys

# ── Write .env files from environment ─────────────────────────────────
env_content = ""
for k in [
    "ANTHROPIC_API_KEY",
    "HERMES_PROVIDER",
    "HERMES_MODEL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_WEBHOOK_PORT",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
]:
    if k in os.environ:
        env_content += f"{k}={os.environ[k]}\n"

os.makedirs("/root/.hermes", exist_ok=True)
with open("/root/.hermes/.env", "w") as f:
    f.write(env_content)
with open("/app/.env", "w") as f:
    f.write(env_content)

# ── SOUL.md ───────────────────────────────────────────────────────────
with open("/root/.hermes/SOUL.md", "w") as f:
    f.write("""# TCG Hermes — Pokemon Card Trading Agent
## Who You Are
You are a Pokemon card trading intelligence agent. Your primary job is to monitor card markets, identify underpriced cards, track price trends, and help your owner (Laurens) grow a trading portfolio.
## Core Knowledge
- Focus on PSA/CGC/BGS graded Pokemon cards
- Priority sets: Base Set, Neo Genesis, modern chase cards (Moonbreon, Charizard VMAX, Prismatic Evolutions)
- Price sources: PokeTrace (poketrace.com), PokemonPriceTracker, TCGplayer, eBay sold listings
- Trading platforms: Courtyard (courtyard.io), Phygitals (phygitals.com), OpenSea (for Courtyard NFTs)
- Starting budget: 500 EUR
- Transaction costs on phygital platforms are 10-30% round trip -- only flag opportunities with more than 25% edge
## How To Behave
- Be concise and data-driven, not chatty
- When checking prices, always compare across multiple sources
- Flag anomalies: cards listed significantly below recent comps
- Track set release dates -- prices drop 30-60% in the weeks after release
- Buy seasonal dips (January-February), sell into holiday demand (November-December)
- Never recommend a trade without showing the data behind it
- When unsure about a price, say so -- do not guess
## Daily Routine (when cron jobs are set up)
- Morning: scan for underpriced listings on Courtyard
- Midday: check price movements on tracked cards
- Evening: summarize portfolio changes and any opportunities found
""")

# ── Create message-persistence hook ──────────────────────────────────
# Container resets on every deploy, so we write hook files at startup.
# The hook uses asyncpg (direct Postgres) with the POSTGRES_* vars that
# NodeOps injects via the Supabase integration — no Supabase SDK needed.
hook_dir = "/root/.hermes/hooks/supabase-messages"
os.makedirs(hook_dir, exist_ok=True)

with open(os.path.join(hook_dir, "HOOK.yaml"), "w") as f:
    f.write("""name: supabase-messages
description: Persist user messages and bot responses to hermes_messages via direct Postgres
events:
  - agent:start
  - agent:end
""")

# Bake Postgres credentials into handler.py at write time so the hook
# does not depend on os.environ (which the gateway may overwrite).
_pg_host = os.environ.get("POSTGRES_HOST", "")
_pg_port = os.environ.get("POSTGRES_PORT", "6543")
_pg_db   = os.environ.get("POSTGRES_DB", "")
_pg_user = os.environ.get("POSTGRES_USER", "")
_pg_pass = os.environ.get("POSTGRES_PASSWORD", "")

_pg_found = [k for k in ["POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
             if os.environ.get(k)]
_pg_missing = [k for k in ["POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD"]
               if not os.environ.get(k)]
print(f"[write_env] Postgres vars found: {_pg_found}", flush=True)
if _pg_missing:
    print(f"[write_env] Postgres vars MISSING: {_pg_missing}", flush=True)

with open(os.path.join(hook_dir, "handler.py"), "w") as f:
    f.write(f'''"""
Supabase message persistence hook — direct Postgres via asyncpg.

Credentials are baked in at container startup by write_env.py.
"""
import asyncpg
from datetime import datetime, timezone

_PG_HOST = "{_pg_host}"
_PG_PORT = "{_pg_port}"
_PG_DB   = "{_pg_db}"
_PG_USER = "{_pg_user}"
_PG_PASS = "{_pg_pass}"

_pool = None

async def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    if not _PG_HOST or not _PG_DB:
        print("[supabase-messages] Postgres credentials not baked in", flush=True)
        return None
    try:
        _pool = await asyncpg.create_pool(
            host=_PG_HOST, port=int(_PG_PORT), database=_PG_DB,
            user=_PG_USER, password=_PG_PASS,
            min_size=1, max_size=2,
            ssl="require",
        )
        print(f"[supabase-messages] Connected to Postgres at {{_PG_HOST}}", flush=True)
        return _pool
    except Exception as e:
        print(f"[supabase-messages] Postgres connection failed: {{e}}", flush=True)
        return None


async def _save_message(chat_id, role, content):
    pool = await _get_pool()
    if not pool or not content:
        return
    try:
        chat_id_int = int(chat_id) if chat_id else 0
    except (ValueError, TypeError):
        chat_id_int = 0
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO hermes_messages (chat_id, role, content, created_at) VALUES ($1, $2, $3, $4)",
                chat_id_int, role, content, datetime.now(timezone.utc),
            )
        print(f"[supabase-messages] Saved {{role}} message (chat {{chat_id_int}})", flush=True)
    except Exception as e:
        print(f"[supabase-messages] Insert failed: {{e}}", flush=True)


async def handle(event_type, context):
    """Hook entrypoint — called by HookRegistry.emit()."""
    chat_id = context.get("user_id") or context.get("session_id") or "0"

    if event_type == "agent:start":
        message = context.get("message", "")
        if message:
            await _save_message(chat_id, "user", message)

    elif event_type == "agent:end":
        response = context.get("response", "")
        if response:
            await _save_message(chat_id, "assistant", response)
''')

print("[write_env] Created supabase-messages hook", flush=True)

os.execvp(sys.executable, [sys.executable, "gateway/run.py"])
