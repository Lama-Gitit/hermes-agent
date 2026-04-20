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
# Credentials are baked into handler.py because the gateway overwrites
# os.environ during startup.
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

_sb_url = os.environ.get("SUPABASE_URL", "")
_sb_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
print(f"[write_env] SUPABASE_URL={'set' if _sb_url else 'MISSING'}, SERVICE_ROLE_KEY={'set' if _sb_key else 'MISSING'}", flush=True)

with open(os.path.join(hook_dir, "handler.py"), "w") as f:
    f.write(f'''"""
Supabase message persistence hook.
Credentials baked in by write_env.py at container startup.
"""
from datetime import datetime, timezone

_SUPABASE_URL = "{_sb_url}"
_SUPABASE_KEY = "{_sb_key}"
_client = None

def _get_client():
    global _client
    if _client is not None:
        return _client
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        print("[supabase-messages] Credentials not baked in — check write_env.py", flush=True)
        return None
    try:
        from supabase import create_client
        _client = create_client(_SUPABASE_URL, _SUPABASE_KEY)
        print(f"[supabase-messages] Connected to {{_SUPABASE_URL}}", flush=True)
        return _client
    except Exception as e:
        print(f"[supabase-messages] Failed to create client: {{e}}", flush=True)
        return None


def _save_message(chat_id, role, content):
    client = _get_client()
    if not client or not content:
        return
    try:
        chat_id_int = int(chat_id) if chat_id else 0
    except (ValueError, TypeError):
        chat_id_int = 0
    try:
        client.table("hermes_messages").insert({{
            "chat_id": chat_id_int,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }}).execute()
        print(f"[supabase-messages] Saved {{role}} message (chat {{chat_id_int}})", flush=True)
    except Exception as e:
        print(f"[supabase-messages] Insert failed: {{e}}", flush=True)


async def handle(event_type, context):
    print(f"[supabase-messages] Hook fired: {{event_type}}", flush=True)

    if event_type == "gateway:startup":
        # ── Startup self-test: connect + insert a test row ──
        print("[supabase-messages] Running startup self-test...", flush=True)
        client = _get_client()
        if client:
            try:
                client.table("hermes_messages").insert({{
                    "chat_id": 0,
                    "role": "assistant",
                    "content": "startup-test: hook connected successfully",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }}).execute()
                print("[supabase-messages] STARTUP TEST OK — row inserted into hermes_messages", flush=True)
            except Exception as e:
                print(f"[supabase-messages] STARTUP TEST FAILED — insert error: {{e}}", flush=True)
        else:
            print("[supabase-messages] STARTUP TEST FAILED — no client", flush=True)
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
