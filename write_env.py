import os
import sys

# DEBUG: Print all available env keys to see what NodeOps is providing
print(f"[write_env] Available environment keys: {list(os.environ.keys())}", flush=True)

# ── Write .env files from environment ─────────────────────────────────
env_content = ""
for k in [
    "ANTHROPIC_API_KEY",
    "HERMES_INFERENCE_PROVIDER",
    "HERMES_PROVIDER",
    "HERMES_MODEL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_WEBHOOK_PORT",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DATABASE_URL",
    "DB_HOST",
    "DB_PORT",
    "DB_USER",
    "DB_PASS",
    "DB_NAME",
    "DB_DATABASE",
    "VENICE_API_KEY",
    "VENICE_BASE_URL",
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
# Uses asyncpg with POSTGRES_* vars (reliably injected by NodeOps)
# instead of the Supabase SDK (SUPABASE_URL is NOT reliably injected).
# Credentials are baked in because the gateway overwrites os.environ.
hook_dir = "/root/.hermes/hooks/supabase-messages"
os.makedirs(hook_dir, exist_ok=True)

with open(os.path.join(hook_dir, "HOOK.yaml"), "w") as f:
    f.write("""name: supabase-messages
description: Persist user messages and bot responses to hermes_messages
events:
  - gateway:startup
  - agent:start
  - agent:end
""")

# Detection for NodeOps / generic DB vars
_pg_host = os.environ.get("POSTGRES_HOST") or os.environ.get("DB_HOST") or ""
_pg_port = os.environ.get("POSTGRES_PORT") or os.environ.get("DB_PORT") or "5432"
_pg_user = os.environ.get("POSTGRES_USER") or os.environ.get("DB_USER") or ""
_pg_pass = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DB_PASS") or ""
_pg_db = os.environ.get("POSTGRES_DB") or os.environ.get("DB_NAME") or os.environ.get("DB_DATABASE") or ""

# Fallback to parsing DATABASE_URL if host is still empty
if not _pg_host and os.environ.get("DATABASE_URL"):
    try:
        from urllib.parse import urlparse
        url = urlparse(os.environ.get("DATABASE_URL"))
        _pg_host = url.hostname or ""
        _pg_port = str(url.port or "5432")
        _pg_user = url.username or ""
        _pg_pass = url.password or ""
        _pg_db = url.path.lstrip("/") or ""
        print(f"[write_env] Detected DB from DATABASE_URL: host={_pg_host}", flush=True)
    except Exception as e:
        print(f"[write_env] Failed to parse DATABASE_URL: {e}", flush=True)

if _pg_host:
    print(f"[write_env] Postgres: host={_pg_host}, port={_pg_port}, db={_pg_db}, user={_pg_user}", flush=True)
else:
    print("[write_env] WARNING: No database host found (checked POSTGRES_HOST, DB_HOST, DATABASE_URL). Message persistence will not work.", flush=True)

with open(os.path.join(hook_dir, "handler.py"), "w") as f:
    f.write(f'''"""
Supabase message persistence hook — uses asyncpg (direct Postgres).
Credentials baked in by write_env.py at container startup.
"""
import asyncio
from datetime import datetime, timezone

_PG_HOST = "{_pg_host}"
_PG_PORT = "{_pg_port}"
_PG_USER = "{_pg_user}"
_PG_PASS = "{_pg_pass}"
_PG_DB   = "{_pg_db}"
_pool = None


async def _get_pool():
    global _pool
    if _pool is not None:
        return _pool
    if not _PG_HOST or not _PG_USER:
        print("[supabase-messages] No Postgres credentials baked in", flush=True)
        return None
    try:
        import asyncpg
        _pool = await asyncpg.create_pool(
            host=_PG_HOST, port=int(_PG_PORT),
            user=_PG_USER, password=_PG_PASS,
            database=_PG_DB, min_size=1, max_size=2,
        )
        print(f"[supabase-messages] Connected to Postgres at {{_PG_HOST}}:{{_PG_PORT}}/{{_PG_DB}}", flush=True)
        return _pool
    except Exception as e:
        print(f"[supabase-messages] Failed to connect: {{e}}", flush=True)
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
        await pool.execute(
            "INSERT INTO hermes_messages (chat_id, role, content, created_at) VALUES ($1, $2, $3, $4)",
            chat_id_int, role, content, datetime.now(timezone.utc),
        )
        print(f"[supabase-messages] Saved {{role}} message (chat {{chat_id_int}})", flush=True)
    except Exception as e:
        print(f"[supabase-messages] Insert failed: {{e}}", flush=True)


async def handle(event_type, context):
    print(f"[supabase-messages] Hook fired: {{event_type}}", flush=True)

    if event_type == "gateway:startup":
        print("[supabase-messages] Running startup self-test...", flush=True)
        pool = await _get_pool()
        if pool:
            try:
                await pool.execute(
                    "INSERT INTO hermes_messages (chat_id, role, content, created_at) VALUES ($1, $2, $3, $4)",
                    0, "assistant", "startup-test: asyncpg connected successfully",
                    datetime.now(timezone.utc),
                )
                print("[supabase-messages] STARTUP TEST OK — row inserted", flush=True)
            except Exception as e:
                print(f"[supabase-messages] STARTUP TEST FAILED — insert error: {{e}}", flush=True)
        else:
            print("[supabase-messages] STARTUP TEST FAILED — no pool", flush=True)
        return

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
