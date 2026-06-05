"""Fetch latest Telegram chat_id and update .env ALLOWED_CHAT_ID."""
import re
import sys
from pathlib import Path

import requests

env_path = Path(__file__).parent / ".env"
text = env_path.read_text(encoding="utf-8")
match = re.search(r"^TELEGRAM_TOKEN=(.+)$", text, re.M)
if not match:
    print("ERROR: TELEGRAM_TOKEN not found in .env")
    sys.exit(1)

token = match.group(1).strip()
resp = requests.get(
    f"https://api.telegram.org/bot{token}/getUpdates",
    timeout=15,
)
resp.raise_for_status()
data = resp.json()
if not data.get("ok"):
    print("ERROR: Telegram API returned not ok")
    sys.exit(1)

chat_ids = []
for update in data.get("result", []):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        continue
    cid = msg.get("chat", {}).get("id")
    if cid is not None:
        chat_ids.append(int(cid))

if not chat_ids:
    print(
        "ERROR: No messages found. Send /start to your bot in Telegram, then run again."
    )
    sys.exit(2)

chat_id = chat_ids[-1]
new_text = re.sub(
    r"^ALLOWED_CHAT_ID=.*$",
    f"ALLOWED_CHAT_ID={chat_id}",
    text,
    flags=re.M,
)
env_path.write_text(new_text, encoding="utf-8")
print(f"Updated ALLOWED_CHAT_ID={chat_id}")
