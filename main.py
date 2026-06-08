import os
import threading
from database import init_db
from bot import load_clones, start_queue_workers, setup_handlers
from web import app
from shared import logger
import telebot

def run_telegram_bot():
    """Starts the main Telegram Bot loop."""
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8286423335:AAH1f5I4NM7B5nmJtEL7i-hCt5Umms7Aj_8"
    main_bot = telebot.TeleBot(TOKEN)
    setup_handlers(main_bot)
    
    logger.info("Starting Main Telegram Bot infinity polling...")
    try:
        main_bot.infinity_polling(timeout=15, long_polling_timeout=10)
    except Exception as e:
        logger.critical(f"Telegram Bot crashed: {e}")

if __name__ == "__main__":
    logger.info("Initializing Gemini-DownOG Production Server...")
    
    # 1. Initialize SQLite schema & WAL Mode
    init_db()
    
    # 2. Start queue worker threads for processing downloads
    start_queue_workers(num_workers=3)
    
    # 3. Load and start all cloned bots
    load_clones()
    
    # 4. Start main Telegram bot polling in a daemon thread
    bot_thread = threading.Thread(target=run_telegram_bot, name="MainBotThread", daemon=True)
    bot_thread.start()
    
    # 5. Start the Flask Web Dashboard Server
    PORT = int(os.getenv("PORT", 8080))
    logger.info(f"Starting Web Dashboard on http://localhost:{PORT}")
    try:
        app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        logger.info("Server shutting down by user request.")
