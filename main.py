import os
import threading

from flask import Flask

from bot import main as run_bot

app = Flask(__name__)


@app.route("/")
def health():
    return "Bot is running!", 200


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, use_reloader=False, threaded=True)
