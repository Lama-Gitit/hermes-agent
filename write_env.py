import os
import sys

env_content = ""
_expected_keys = [
    "ANTHROPIC_API_KEY",
    "HERMES_PROVIDER",
    "HERMES_MODEL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_ALLOWED_USERS",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_WEBHOOK_PORT",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
]
for k in _expected_keys:
    if k in os.environ:
        env_content += f"{k}={os.environ[k]}\n"

# Debug: show which keys were found vs missing
_found = [k for k in _expected_keys if k in os.environ]
_missing = [k for k in _expected_keys if k not in os.environ]
print(f"[write_env] Env vars found: {_found}", flush=True)
print(f"[write_env] Env vars MISSING: {_missing}", flush=True)

os.makedirs("/root/.hermes", exist_ok=True)
with open("/root/.hermes/.env", "w") as f:
    f.write(env_content)
with open("/app/.env", "w") as f:
    f.write(env_content)

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
# The hook fires on agent:start + agent:end to log messages to Supabase.
hook_dir = "/root/.hermes/hooks/supabase-messages"
os.makedirs(hook_dir, exist_ok=True)

with open(os.path.join(hook_dir, "HOOK.yaml"), "w") as f:
    f.write("""name: supabase-messages
description: Persist user messages and bot responses to hermes_messages in Supabase
events:
  - gateway:startup
  - agent:start
  - agent:end
""")

with open(os.path.join(hook_dir, "handler.py"), "w") as f:
    f.write('''"""
Supabase message persistence hook.

Fires on agent:start to capture the user message, and agent:end to capture
the bot response. Both are saved to hermes_messages with chat_id, role,
and content.
"""
import os
import sys
from datetime import datetime, timezone

_client = None

def _get_client():
    global _client
    if _client is not None:
        return _client
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not key:
        print("[supabase-messages] SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not set", flush=True)
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        print(f"[supabase-messages] Client connected to {url}", flush=True)
        return _client
    except Exception as e:
        print(f"[supabase-messages] Failed to create client: {e}", flush=True)
        return None


def _save_message(chat_id, role, content):
    """Insert a single message row into hermes_messages."""
    client = _get_client()
    if not client:
        print(f"[supabase-messages] No client — skipping {role} message", flush=True)
        return
    if not content:
        return
    try:
        # chat_id from Telegram is a numeric string; convert safely
        try:
            chat_id_int = int(chat_id)
        except (ValueError, TypeError):
            chat_id_int = 0
        client.table("hermes_messages").insert({
            "chat_id": chat_id_int,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        print(f"[supabase-messages] Saved {role} message (chat {chat_id_int})", flush=True)
    except Exception as e:
        # Never block the main pipeline
        print(f"[supabase-messages] Insert failed: {e}", flush=True)


async def handle(event_type, context):
    """Hook entrypoint — called by HookRegistry.emit()."""
    print(f"[supabase-messages] Hook fired: {event_type}", flush=True)

    if event_type == "gateway:startup":
        print("[supabase-messages] Gateway startup confirmed — hook dispatch is working", flush=True)
        return

    chat_id = context.get("user_id") or context.get("session_id") or "0"

    if event_type == "agent:start":
        message = context.get("message", "")
        if message:
            _save_message(chat_id, "user", message)

    elif event_type == "agent:end":
        response = context.get("response", "")
        if response:
            _save_message(chat_id, "assistant", response)
''')

print("[write_env] Created supabase-messages hook", flush=True)

os.execvp(sys.executable, [sys.executable, "gateway/run.py"])
