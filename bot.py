import json
import logging
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import requests
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ALLOWED_CHAT_ID = os.getenv("ALLOWED_CHAT_ID", "").strip()

KST = pytz.timezone("Asia/Seoul")
PROJECT_DIR = Path(__file__).parent
DB_PATH = PROJECT_DIR / "alarms.db"
ENV_PATH = PROJECT_DIR / ".env"
PLACEHOLDER_CHAT_IDS = {"", "123456789"}

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

ai_client = OpenAI(api_key=OPENAI_API_KEY)


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS alarms (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    INTEGER NOT NULL,
                alarm_time TEXT    NOT NULL,
                task       TEXT    NOT NULL,
                sent       INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_alarm_time ON alarms(alarm_time, sent)"
        )


def save_alarm(chat_id: int, alarm_time: str, task: str) -> int:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO alarms (chat_id, alarm_time, task) VALUES (?, ?, ?)",
            (chat_id, alarm_time, task),
        )
        return cur.lastrowid


def get_pending_alarms(chat_id: int) -> list[tuple]:
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:00")
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT id, alarm_time, task
            FROM alarms
            WHERE chat_id = ? AND sent = 0 AND alarm_time >= ?
            ORDER BY alarm_time
            """,
            (chat_id, now_str),
        ).fetchall()


def cancel_alarm(alarm_id: int, chat_id: int) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "DELETE FROM alarms WHERE id = ? AND chat_id = ? AND sent = 0",
            (alarm_id, chat_id),
        )
        return cur.rowcount > 0


DATE_KEYWORDS = ("내일", "모레", "글피", "다음날", "tomorrow", "day after")


def format_alarm_message(message: str) -> str:
    text = message.strip().strip("⏰").strip()
    return f"⏰{text}⏰"


def infer_today_if_no_date(user_message: str, time_str: str) -> str:
    """날짜를 말하지 않았는데 AI가 내일로 잡은 경우, 오늘로 보정."""
    if any(keyword in user_message for keyword in DATE_KEYWORDS):
        return time_str

    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    now = datetime.now(KST)
    today = now.date()

    if dt.date() != today + timedelta(days=1):
        return time_str

    today_at_time = KST.localize(datetime.combine(today, dt.time()))
    if today_at_time > now:
        return today_at_time.strftime("%Y-%m-%d %H:%M:00")

    return time_str


def normalize_alarm_time(time_str: str) -> str:
    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    return KST.localize(dt).strftime("%Y-%m-%d %H:%M:00")


def fetch_due_alarms(now_str: str) -> list[tuple]:
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute(
            """
            SELECT id, chat_id, task
            FROM alarms
            WHERE alarm_time <= ? AND sent = 0
            ORDER BY alarm_time
            """,
            (now_str,),
        ).fetchall()


def mark_sent(alarm_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE alarms SET sent = 1 WHERE id = ?", (alarm_id,))


def send_message(chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
        if not resp.ok:
            logger.error("Telegram send failed: %s", resp.text)
    except Exception as e:
        logger.error("Telegram send error: %s", e)


def persist_allowed_chat_id(chat_id: int):
    global ALLOWED_CHAT_ID
    ALLOWED_CHAT_ID = str(chat_id)
    if not ENV_PATH.exists():
        return
    text = ENV_PATH.read_text(encoding="utf-8")
    if re.search(r"^ALLOWED_CHAT_ID=", text, re.M):
        text = re.sub(
            r"^ALLOWED_CHAT_ID=.*$",
            f"ALLOWED_CHAT_ID={chat_id}",
            text,
            flags=re.M,
        )
    else:
        text = text.rstrip() + f"\nALLOWED_CHAT_ID={chat_id}\n"
    ENV_PATH.write_text(text, encoding="utf-8")
    logger.info("Saved ALLOWED_CHAT_ID=%s to .env", chat_id)


def is_allowed(chat_id: int) -> bool:
    global ALLOWED_CHAT_ID
    if ALLOWED_CHAT_ID in PLACEHOLDER_CHAT_IDS:
        persist_allowed_chat_id(chat_id)
        send_message(
            chat_id,
            f"✅ 이 계정이 봇 사용자로 등록되었습니다. (chat_id: {chat_id})",
        )
        return True
    if not ALLOWED_CHAT_ID:
        return True
    return str(chat_id) == ALLOWED_CHAT_ID


def parse_alarm_with_ai(user_message: str) -> dict | None:
    now_kst = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

    system_prompt = (
        f"You extract alarm info from user messages (Korean/English/mixed). "
        f"Current KST: {now_kst}. "
        f"Return JSON with exactly two keys:\n"
        f'  "datetime": "YYYY-MM-DD HH:MM:00" (KST, must be in the future)\n'
        f'  "message": short friendly Korean alarm text (no emoji)\n'
        f"Date rules (very important):\n"
        f"- If the user does NOT mention a date (no 오늘/내일/모레/tomorrow), "
        f"assume TODAY when that clock time is still later today.\n"
        f"- Example: at 14:00, user says '5시에 밥먹으라해줘' → datetime is TODAY 17:00.\n"
        f"- Use TOMORROW only if that clock time already passed today, "
        f"or the user explicitly says tomorrow/내일/다음날/모레.\n"
        f"- For relative times (e.g. 30 minutes later), compute from current KST.\n"
        f"Message rules:\n"
        f"- Convert the request into a natural short Korean phrase ending with ~입니다 or ~해요.\n"
        f"- Example: '밥 먹으라' → '식사 시간입니다'\n"
        f"- Example: '약 먹으라' → '약 먹을 시간입니다'\n"
        f"- Do NOT include ⏰ emoji in the message.\n"
        f"If message is NOT an alarm request, return "
        f'{{"datetime": null, "message": null}}.'
    )

    try:
        response = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        return None


def validate_alarm_time(time_str: str) -> tuple[bool, str]:
    try:
        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
        dt = KST.localize(dt)
    except ValueError:
        return False, "시간 형식이 올바르지 않습니다."

    now = datetime.now(KST)
    if dt <= now:
        return False, "과거 시간은 등록할 수 없습니다. 미래 시간으로 다시 말씀해 주세요."

    if (dt - now).days > 365:
        return False, "1년 이후 알람은 등록할 수 없습니다."

    return True, ""


def handle_start(chat_id: int):
    send_message(
        chat_id,
        "🤖 AI 알람봇입니다!\n\n"
        "• 자유롭게 말하면 알람 등록\n"
        '  예) "5시에 밥 먹으라해줘" → ⏰식사 시간입니다⏰\n'
        '  예) "내일 오후 3시에 회의"\n'
        '  예) "30분 뒤에 약 먹기"\n\n'
        "• 날짜를 안 말하면 오늘로 설정\n"
        "• /list — 등록된 알람 보기\n"
        "• /cancel [번호] — 알람 취소",
    )


def handle_list(chat_id: int):
    alarms = get_pending_alarms(chat_id)
    if not alarms:
        send_message(chat_id, "📭 등록된 알람이 없습니다.")
        return

    lines = ["📋 등록된 알람 목록:\n"]
    for alarm_id, alarm_time, task in alarms:
        dt = datetime.strptime(alarm_time, "%Y-%m-%d %H:%M:%S")
        lines.append(
            f"[{alarm_id}] {dt.strftime('%m/%d %H:%M')} — {format_alarm_message(task)}"
        )
    lines.append("\n취소: /cancel [번호]")
    send_message(chat_id, "\n".join(lines))


def handle_cancel(chat_id: int, text: str):
    parts = text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        send_message(chat_id, "사용법: /cancel [번호]\n예) /cancel 3")
        return

    alarm_id = int(parts[1])
    if cancel_alarm(alarm_id, chat_id):
        send_message(chat_id, f"✅ 알람 #{alarm_id} 취소 완료")
    else:
        send_message(chat_id, f"❌ 알람 #{alarm_id}을(를) 찾을 수 없습니다.")


def handle_alarm_request(chat_id: int, user_message: str):
    result = parse_alarm_with_ai(user_message)

    alarm_message = (result or {}).get("message") or (result or {}).get("task")
    if not result or not result.get("datetime") or not alarm_message:
        send_message(
            chat_id,
            "❌ 알람으로 인식하지 못했습니다.\n"
            '예) "5시에 밥 먹으라해줘", "내일 아침 9시 회의"',
        )
        return

    time_str = normalize_alarm_time(result["datetime"])
    time_str = infer_today_if_no_date(user_message, time_str)
    alarm_message = alarm_message.strip()

    ok, err = validate_alarm_time(time_str)
    if not ok:
        send_message(chat_id, f"❌ {err}")
        return

    alarm_id = save_alarm(chat_id, time_str, alarm_message)
    dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
    preview = format_alarm_message(alarm_message)

    send_message(
        chat_id,
        f"✅ 알람 등록 완료! (#{alarm_id})\n"
        f"📅 {dt.strftime('%Y-%m-%d %H:%M')} (한국시간)\n"
        f"🔔 {preview}",
    )
    logger.info("Alarm saved #%s: %s @ %s", alarm_id, alarm_message, time_str)


def process_message(chat_id: int, text: str):
    if not is_allowed(chat_id):
        send_message(
            chat_id,
            "⛔ 이 봇은 허용된 사용자만 사용할 수 있습니다.\n\n"
            f"본인 chat_id: {chat_id}\n"
            ".env(또는 Render)의 ALLOWED_CHAT_ID에 위 숫자를 넣고 봇을 재시작하세요.",
        )
        return

    text = text.strip()

    if text == "/start":
        handle_start(chat_id)
    elif text == "/list":
        handle_list(chat_id)
    elif text.startswith("/cancel"):
        handle_cancel(chat_id, text)
    else:
        handle_alarm_request(chat_id, text)


def alarm_checker_loop():
    logger.info("Alarm checker thread started")
    while True:
        try:
            now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M:00")
            due = fetch_due_alarms(now_str)

            for alarm_id, chat_id, task in due:
                send_message(chat_id, format_alarm_message(task))
                mark_sent(alarm_id)
                logger.info("Alarm fired #%s: %s", alarm_id, task)

        except Exception as e:
            logger.error("Alarm checker error: %s", e)

        time.sleep(1)


def telegram_polling_loop():
    logger.info("Telegram polling started")
    last_update_id = 0
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

    while True:
        try:
            resp = requests.get(
                url,
                params={"offset": last_update_id + 1, "timeout": 30},
                timeout=35,
            )
            if not resp.ok:
                logger.error("getUpdates failed: %s", resp.text)
                time.sleep(5)
                continue

            for update in resp.json().get("result", []):
                last_update_id = update["update_id"]
                msg = update.get("message")
                if not msg:
                    continue

                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text")
                if chat_id and text:
                    process_message(chat_id, text)

        except Exception as e:
            logger.error("Polling error: %s", e)
            time.sleep(5)


def main():
    init_db()
    threading.Thread(target=alarm_checker_loop, daemon=True).start()
    telegram_polling_loop()


if __name__ == "__main__":
    main()
