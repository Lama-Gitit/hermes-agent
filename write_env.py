import os
import sys
import time

# ── DEBUG: what env vars does this process actually see at boot? ──────
print(f"[write_env] PID={os.getpid()} sees {len(os.environ)} env vars", flush=True)
print(f"[write_env] ALL KEYS: {sorted(os.environ.keys())}", flush=True)
for k in ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "ANTHROPIC_API_KEY", "TELEGRAM_BOT_TOKEN", "HERMES_MODEL"]:
    v = os.environ.get(k)
    if v is None:
        print(f"[write_env] {k} = <MISSING>", flush=True)
    else:
        # show length + first/last 4 chars, never the full secret
        shown = f"{v[:4]}...{v[-4:]}" if len(v) > 10 else "<short>"
        print(f"[write_env] {k} len={len(v)} preview={shown}", flush=True)

# ── Wait for env vars if needed (NodeOps sometimes injects them late) ─
_REQUIRED = ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"]
for attempt in range(4):  # check immediately, then retry 3 times
    if all(k in os.environ for k in _REQUIRED):
        break
    if attempt == 0:
        print("[write_env] Supabase env vars not found yet, waiting for NodeOps injection...", flush=True)
    time.sleep(3)
    # Re-read /proc/self/environ in case vars were injected after process start
    try:
        with open("/proc/self/environ", "rb") as f:
            for entry in f.read().split(b"\0"):
                if b"=" in entry:
                    k, v = entry.decode("utf-8", errors="replace").split("=", 1)
                    if k not in os.environ:
                        os.environ[k] = v
    except Exception:
        pass

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
# The handler reads credentials from os.environ (or /root/.hermes/.env as
# a fallback) at call time, so it works even if a worker process re-imports
# the module later with a different os.environ view.
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
    f.write('''"""
Supabase message persistence hook.
Reads credentials from os.environ first, falls back to /root/.hermes/.env.
No credentials are baked into this file.
"""
import os
from datetime import datetime, timezone

_client = None


def _read_dotenv(path):
    """Parse a simple KEY=VALUE .env file, ignoring blanks and comments."""
    result = {}
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def _resolve_creds():
    """Try os.environ, then /root/.hermes/.env. Return (url, key, source)."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return url, key, "env"
    env_vars = _read_dotenv("/root/.hermes/.env")
    url = url or env_vars.get("SUPABASE_URL")
    key = key or env_vars.get("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return url, key, "dotenv"
    return None, None, "none"


def _get_client():
    global _client
    if _client is not None:
        return _client
    url, key, source = _resolve_creds()
    if not url or not key:
        print(f"[supabase-messages] No credentials found (PID={os.getpid()}, source={source})", flush=True)
        return None
    try:
        from supabase import create_client
        _client = create_client(url, key)
        print(f"[supabase-messages] Connected (PID={os.getpid()}, source={source})", flush=True)
        return _client
    except Exception as e:
        print(f"[supabase-messages] Failed to create client: {e}", flush=True)
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
        client.table("hermes_messages").insert({
            "chat_id": chat_id_int,
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        print(f"[supabase-messages] Saved {role} message (chat {chat_id_int})", flush=True)
    except Exception as e:
        print(f"[supabase-messages] Insert failed: {e}", flush=True)


async def handle(event_type, context):
    print(f"[supabase-messages] Hook fired: {event_type} (PID={os.getpid()})", flush=True)

    if event_type == "gateway:startup":
        # ── Startup self-test: connect + insert a test row ──
        print("[supabase-messages] Running startup self-test...", flush=True)
        client = _get_client()
        if client:
            try:
                client.table("hermes_messages").insert({
                    "chat_id": 0,
                    "role": "assistant",
                    "content": "startup-test: hook connected successfully",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }).execute()
                print("[supabase-messages] STARTUP TEST OK — row inserted into hermes_messages", flush=True)
            except Exception as e:
                print(f"[supabase-messages] STARTUP TEST FAILED — insert error: {e}", flush=True)
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
