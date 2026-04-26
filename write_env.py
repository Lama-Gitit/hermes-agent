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
    # Marketplace / ingestion adapter keys — all optional. Adapters that
    # require a missing key degrade to status='skipped' without crashing.
    "ALCHEMY_POLYGON_API_KEY",
    "EBAY_OAUTH_TOKEN",
    "TCGPLAYER_BEARER_TOKEN",
    "POKEMONPRICETRACKER_API_KEY",
    "COURTYARD_API_KEY",
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
_pg_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRESQL_URL") or ""
_pg_host = os.environ.get("POSTGRES_HOST") or os.environ.get("DB_HOST") or ""
_pg_port = os.environ.get("POSTGRES_PORT") or os.environ.get("DB_PORT") or "5432"
_pg_user = os.environ.get("POSTGRES_USER") or os.environ.get("DB_USER") or ""
_pg_pass = os.environ.get("POSTGRES_PASSWORD") or os.environ.get("DB_PASS") or ""
_pg_db = os.environ.get("POSTGRES_DB") or os.environ.get("DB_NAME") or os.environ.get("DB_DATABASE") or ""

_sb_url = os.environ.get("SUPABASE_URL", "")
_sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Fallback to parsing URL if host is still empty
if not _pg_host and os.environ.get("DATABASE_URL"):
    try:
        from urllib.parse import urlparse
        url = urlparse(os.environ.get("DATABASE_URL"))
        _pg_host = url.hostname or ""
        _pg_port = str(url.port or "5432")
        _pg_user = url.username or ""
        _pg_pass = url.password or ""
        _pg_db = url.path.lstrip("/") or ""
    except Exception:
        pass

if _pg_host:
    print(f"[write_env] Postgres: host={_pg_host}, port={_pg_port}, db={_pg_db}, user={_pg_user}", flush=True)
elif _sb_url:
    print(f"[write_env] Supabase: url={_sb_url}", flush=True)
else:
    print("[write_env] WARNING: No database found (checked POSTGRES, DB_HOST, DATABASE_URL, SUPABASE_URL). Message persistence will not work.", flush=True)

with open(os.path.join(hook_dir, "handler.py"), "w") as f:
    f.write(f'''"""
Supabase message persistence hook — uses direct Postgres or Supabase SDK.
Credentials baked in by write_env.py at container startup.
"""
import asyncio
from datetime import datetime, timezone

_PG_HOST = "{_pg_host}"
_PG_PORT = "{_pg_port}"
_PG_USER = "{_pg_user}"
_PG_PASS = "{_pg_pass}"
_PG_DB   = "{_pg_db}"

_SB_URL  = "{_sb_url}"
_SB_KEY  = "{_sb_key}"

_pool = None
_client = None


async def _get_client():
    global _pool, _client
    if _pool is not None:
        return _pool, "postgres"
    if _client is not None:
        return _client, "supabase"

    # Try Postgres first
    if _PG_HOST and _PG_USER:
        try:
            import asyncpg
            _pool = await asyncpg.create_pool(
                host=_PG_HOST, port=int(_PG_PORT),
                user=_PG_USER, password=_PG_PASS,
                database=_PG_DB, min_size=1, max_size=2,
            )
            print(f"[supabase-messages] Connected via direct Postgres", flush=True)
            return _pool, "postgres"
        except Exception as e:
            print(f"[supabase-messages] Postgres connection failed: {{e}}", flush=True)

    # Fallback to Supabase SDK
    if _SB_URL and _SB_KEY:
        try:
            from supabase import create_client
            _client = create_client(_SB_URL, _SB_KEY)
            print(f"[supabase-messages] Connected via Supabase API", flush=True)
            return _client, "supabase"
        except Exception as e:
            print(f"[supabase-messages] Supabase API connection failed: {{e}}", flush=True)

    return None, None


async def _save_message(chat_id, role, content):
    client, type = await _get_client()
    if not client or not content:
        return
    try:
        chat_id_int = int(chat_id) if chat_id else 0
    except (ValueError, TypeError):
        chat_id_int = 0

    data = {{
        "chat_id": chat_id_int,
        "role": role,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }}

    try:
        if type == "postgres":
            await client.execute(
                "INSERT INTO hermes_messages (chat_id, role, content, created_at) VALUES ($1, $2, $3, $4)",
                chat_id_int, role, content, datetime.now(timezone.utc),
            )
        else:
            # Supabase SDK is synchronous
            client.table("hermes_messages").insert(data).execute()
        print(f"[supabase-messages] Saved {{role}} message (chat {{chat_id_int}})", flush=True)
    except Exception as e:
        print(f"[supabase-messages] Insert failed ({{type}}): {{e}}", flush=True)


# ── Defensive context extraction ────────────────────────────────────
# Hermes versions vary in the shape of the context payload passed to hooks.
# Older versions used flat keys (context["message"], context["response"], context["user_id"]).
# Some newer paths nest data under context["event"] as an object with attrs.
# We try both shapes so the handler keeps working across versions.
def _pick(ctx, *keys):
    """Try a list of keys against ctx (dict) and ctx['event'] (dict or obj)."""
    if isinstance(ctx, dict):
        for k in keys:
            v = ctx.get(k)
            if v not in (None, ""):
                return v
        ev = ctx.get("event")
        if ev is not None:
            for k in keys:
                if isinstance(ev, dict):
                    v = ev.get(k)
                else:
                    v = getattr(ev, k, None)
                if v not in (None, ""):
                    return v
    return None


async def handle(event_type, context):
    print(f"[supabase-messages] Hook fired: {{event_type}}", flush=True)

    if event_type == "gateway:startup":
        print("[supabase-messages] Running startup self-test...", flush=True)
        client, type = await _get_client()
        if client:
            try:
                await _save_message(0, "assistant", "startup-test: connected successfully via " + type)
                print("[supabase-messages] STARTUP TEST OK", flush=True)
            except Exception as e:
                print(f"[supabase-messages] STARTUP TEST FAILED: {{e}}", flush=True)
        else:
            print("[supabase-messages] STARTUP TEST FAILED — no connection", flush=True)
        return

    # Diagnostic: log the actual context shape so we can adapt if Hermes changes again.
    try:
        ctx_keys = list(context.keys()) if hasattr(context, "keys") else f"<{{type(context).__name__}}>"
    except Exception as e:
        ctx_keys = f"<err: {{e}}>"
    print(f"[supabase-messages] {{event_type}} context keys: {{ctx_keys}}", flush=True)

    chat_id = _pick(context, "chat_id", "user_id", "session_id") or 0

    if event_type == "agent:start":
        text = _pick(context, "message", "text", "content", "input")
        if text:
            await _save_message(chat_id, "user", text)
        else:
            print(f"[supabase-messages] agent:start — no user text in context (chat_id={{chat_id}})", flush=True)
    elif event_type == "agent:end":
        text = _pick(context, "response", "output", "text", "content", "message")
        if text:
            await _save_message(chat_id, "assistant", text)
        else:
            print(f"[supabase-messages] agent:end — no assistant text in context (chat_id={{chat_id}})", flush=True)
''')

print("[write_env] Created supabase-messages hook", flush=True)
os.execvp(sys.executable, [sys.executable, "gateway/run.py"])
