# Telegram AI Alarm Bot

텔레그램으로 자유롭게 말하면 AI가 시간과 내용을 인식해 알람을 등록하는 봇입니다.

## 기능

- 자유 문장으로 알람 등록 (한국어/영어)
- 등록된 시간에 텔레그램 알람 발송
- `/list` — 알람 목록
- `/cancel [번호]` — 알람 취소
- SQLite DB 저장 (재시작 후에도 유지, Render 재배포 시에는 초기화됨)

## 로컬 실행

1. `.env.example`을 복사해 `.env` 파일 생성 후 값 입력

```bash
copy .env.example .env
```

2. 패키지 설치 및 실행

```bash
pip install -r requirements.txt
python main.py
```

3. 브라우저에서 `http://localhost:10000` 접속 → `Bot is running!` 확인

## Render 무료 배포

### 1. GitHub에 push

```bash
git init
git add .
git commit -m "telegram alarm bot"
git branch -M main
git remote add origin https://github.com/본인아이디/telegram-alarm-bot.git
git push -u origin main
```

### 2. Render 설정

1. [render.com](https://render.com) 가입 (GitHub 연동)
2. **New +** → **Web Service** → 저장소 선택
3. 설정:

| 항목 | 값 |
|------|-----|
| Build Command | `pip install -r requirements.txt` |
| Start Command | `python main.py` |
| Instance Type | **Free** |

4. **Environment Variables** 추가:

| Key | Value |
|-----|-------|
| `TELEGRAM_TOKEN` | @BotFather에서 발급 |
| `OPENAI_API_KEY` | OpenAI API 키 |
| `ALLOWED_CHAT_ID` | 본인 chat_id |

### 3. 24시간 유지 (cron-job.org)

Render 무료는 15분 무활동 시 sleep 됩니다.

1. [cron-job.org](https://cron-job.org) 가입
2. **Create cron job**
3. URL: `https://your-app.onrender.com`
4. Schedule: **Every 10 minutes**

## chat_id 확인

1. 봇에게 `/start` 전송
2. 브라우저에서 접속:

```
https://api.telegram.org/bot<토큰>/getUpdates
```

3. `"chat":{"id":123456789}` 숫자를 `ALLOWED_CHAT_ID`에 입력

## 보안

- 토큰/API 키는 `.env` 또는 Render 환경변수에만 저장
- 코드에 직접 입력하지 마세요
- 유출된 토큰은 @BotFather `/revoke`로 재발급
