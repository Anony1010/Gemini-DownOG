#!/usr/bin/env python3
"""
Gemini-DownOG — Fully self-contained Telegram Media Downloader Bot.
Deploy anywhere with: pip install -r requirements.txt && python main.py
"""
import os
import sys
import threading

# ─── Bot Token (override with TELEGRAM_BOT_TOKEN env var) ───
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8832436942:AAFFytpOaEEsYSO3MvGMadXI1mOtoxg2yv8"

# Ensure no .env needed
os.environ.setdefault("TELEGRAM_BOT_TOKEN", BOT_TOKEN)

from database import init_db
from bot import setup_handlers, load_clones
from web import app
from shared import logger
import telebot


def main():
    logger.info("=" * 50)
    logger.info("  Gemini-DownOG starting up...")
    logger.info("=" * 50)

    # 1. Initialize database
    init_db()
    logger.info("✅ Database initialized")

    # 2. Create bot and register handlers
    bot = telebot.TeleBot(BOT_TOKEN)
    setup_handlers(bot)
    logger.info("✅ Bot handlers registered")

    # 3. Load clone bots
    load_clones()
    logger.info("✅ Clone bots loaded")

    # 4. Start polling in background with auto-reconnect
    import requests.exceptions

    def poll():
        while True:
            try:
                logger.info("🚀 Telegram bot polling started...")
                bot.infinity_polling(timeout=15, long_polling_timeout=10, skip_pending=True)
                break
            except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, 
                    requests.exceptions.Timeout, Exception) as e:
                logger.warning(f"💥 Polling crashed ({type(e).__name__}), restarting in 5s...")
                import time
                time.sleep(5)
                continue

    t = threading.Thread(target=poll, daemon=True, name="BotPoller")
    t.start()

    # 5. Start web dashboard
    PORT = int(os.getenv("PORT", "8080"))
    logger.info(f"🌐 Web dashboard: http://0.0.0.0:{PORT}")
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        sys.exit(0)


if __name__ == "__main__":
    main()
