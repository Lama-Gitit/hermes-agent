import os
import sys
import shutil

env_content = ""
for k in ["ANTHROPIC_API_KEY","HERMES_PROVIDER","HERMES_MODEL","TELEGRAM_BOT_TOKEN","TELEGRAM_ALLOWED_USERS"]:
    if k in os.environ:
        env_content += f"{k}={os.environ[k]}\n"

os.makedirs("/root/.hermes", exist_ok=True)
with open("/root/.hermes/.env", "w") as f:
    f.write(env_content)
with open("/app/.env", "w") as f:
    f.write(env_content)

soul_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "SOUL.md")
if os.path.exists(soul_src):
    shutil.copy2(soul_src, "/root/.hermes/SOUL.md")
else:
    print(f"SOUL.md not found at {soul_src}", flush=True)

os.execvp(sys.executable, [sys.executable, "gateway/run.py"])