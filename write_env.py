import os
import sys

os.makedirs("/root/.hermes", exist_ok=True)
with open("/root/.hermes/.env", "w") as f:
    for k in ["ANTHROPIC_API_KEY","HERMES_PROVIDER","HERMES_MODEL","TELEGRAM_BOT_TOKEN","TELEGRAM_ALLOWED_USERS"]:
        if k in os.environ:
            f.write(f"{k}={os.environ[k]}\n")

os.execvp(sys.executable, [sys.executable, "gateway/run.py"])