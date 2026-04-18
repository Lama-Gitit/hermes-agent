import os
import sys

env_content = ""
for k in ["ANTHROPIC_API_KEY","HERMES_PROVIDER","HERMES_MODEL","TELEGRAM_BOT_TOKEN","TELEGRAM_ALLOWED_USERS"]:
    if k in os.environ:
        env_content += f"{k}={os.environ[k]}\n"

# Write to ~/.hermes/.env (where Hermes looks first)
hermes_dir = os.path.expanduser("~/.hermes")
os.makedirs(hermes_dir, exist_ok=True)
with open(os.path.join(hermes_dir, ".env"), "w") as f:
    f.write(env_content)

# Also write to /app/.env (project root fallback)
with open("/app/.env", "w") as f:
    f.write(env_content)

print(f"DEBUG: home={os.path.expanduser('~')}", flush=True)
print(f"DEBUG: wrote to {hermes_dir}/.env and /app/.env", flush=True)
print(f"DEBUG: TELEGRAM_BOT_TOKEN present: {'TELEGRAM_BOT_TOKEN' in os.environ}", flush=True)

os.execvp(sys.executable, [sys.executable, "gateway/run.py"])