import os
import re
import time
import uuid
import threading
from typing import Optional

import telebot
from telebot import types
from telebot.types import ReactionTypeEmoji

from database import (
    db_register_user, db_is_banned, db_ban_user,
    db_log_download, db_get_user, db_get_user_by_username,
    db_get_all_users, db_get_recent_users, db_count_users,
    db_count_banned_users, db_get_user_downloads, get_setting,
    set_setting, delete_setting, db_add_clone, db_get_all_clones,
    db_delete_clone
)
from shared import logger, active_clones
from ytdl_downloader import (
    detect_platform, download_youtube, download_social_media,
    probe_video, generate_thumbnail, file_size_mb, cleanup_temp,
    schedule_cleanup, DOWNLOAD_DIR, MAX_FILE_SIZE_BYTES
)

# ─── Admin state tracking ───
user_states = {}
user_last_url = {}

# ─── Helper Functions ───

def get_admin_id():
    """Get admin ID from env or DB. Auto-assigns later on first /66 usage."""
    env_admin = os.getenv("ADMIN_ID")
    if env_admin:
        return int(env_admin)
    db_admin = get_setting("admin_id")
    if db_admin:
        return int(db_admin)
    return None


def is_admin(user_id):
    """Check if user is admin."""
    env_admin = os.getenv("ADMIN_ID")
    if env_admin and str(user_id) == str(env_admin):
        return True
    db_admin = get_setting("admin_id")
    if db_admin and str(user_id) == str(db_admin):
        return True
    # Auto-assign first user who calls /66
    if not get_setting("admin_id") and not env_admin:
        set_setting("admin_id", str(user_id))
        return True
    return False


def safe_reaction(bot, chat_id, msg_id, emoji="👍"):
    """Add reaction to a message. Safe wrapper."""
    try:
        bot.set_message_reaction(chat_id, msg_id, [ReactionTypeEmoji(emoji)])
    except Exception as e:
        logger.debug(f"Reaction failed: {e}")


def safe_delete(bot, chat_id, msg_id):
    """Delete a message safely."""
    try:
        bot.delete_message(chat_id, msg_id)
    except Exception:
        pass


def safe_edit(bot, chat_id, msg_id, text, **kwargs):
    """Edit a message safely."""
    try:
        bot.edit_message_text(text, chat_id, msg_id, **kwargs)
    except Exception:
        pass


def safe_send(bot, chat_id, text, **kwargs):
    """Send a message safely."""
    try:
        return bot.send_message(chat_id, text, **kwargs)
    except Exception as e:
        logger.error(f"send_message failed: {e}")
        return None


def clean_file(path):
    """Delete a file safely."""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except Exception:
        pass


def register_user(message, bot=None):
    """Register or update user in DB."""
    uid = message.from_user.id
    username = message.from_user.username or ""
    fname = message.from_user.first_name or ""
    db_register_user(uid, username, fname)
    if db_is_banned(uid):
        try:
            if bot:
                bot.reply_to(message, "🚫 Siz bloklanmısınız.")
        except Exception:
            pass
        return False
    return True


# ─── Background Download Workers ───

download_queue = []
queue_lock = threading.Lock()
queue_event = threading.Event()
active_downloads = {}
active_dl_lock = threading.Lock()


def queue_worker():
    """Background thread that processes download queue."""
    while True:
        queue_event.wait()
        while True:
            task = None
            with queue_lock:
                if download_queue:
                    task = download_queue.pop(0)
                else:
                    queue_event.clear()
            if not task:
                break
            try:
                task()
            except Exception as e:
                logger.error(f"Queue task error: {e}")


def enqueue_download(func):
    """Add a download task to the queue."""
    with queue_lock:
        download_queue.append(func)
        queue_event.set()


def can_download(user_id):
    """Check if user can start a new download (max 2 concurrent)."""
    with active_dl_lock:
        cnt = active_downloads.get(user_id, 0)
        if cnt >= 2:
            return False
        active_downloads[user_id] = cnt + 1
        return True


def finish_download(user_id):
    """Decrement active download count."""
    with active_dl_lock:
        cnt = active_downloads.get(user_id, 0)
        if cnt > 0:
            active_downloads[user_id] = cnt - 1


# ─── YouTube Download Handler ───

def start_yt_worker(bot, chat_id, user_id, url, fmt, wait_msg):
    """Background worker for YouTube downloads."""
    uid = f"yt_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    fp = None
    try:
        safe_edit(bot, chat_id, wait_msg.message_id,
                  "🎬 YouTube yüklənir..." if fmt == "mp4" else "🎵 YouTube audiosu yüklənir...")

        fp = download_youtube(url, fmt, uid)
        if not fp or not os.path.exists(fp):
            safe_edit(bot, chat_id, wait_msg.message_id, "❌ Fayl tapılmadı.")
            finish_download(user_id)
            return

        if os.path.getsize(fp) > MAX_FILE_SIZE_BYTES:
            safe_edit(bot, chat_id, wait_msg.message_id, "⚠️ Fayl 50MB limitini aşır.")
            clean_file(fp)
            finish_download(user_id)
            return

        brand = get_setting("caption_branding") or "🔹 DOWNLOADED BY GASHAM🔹"
        safe_edit(bot, chat_id, wait_msg.message_id, "📤 Telegram-a yüklənir...")

        if fmt == "mp3":
            with open(fp, "rb") as f:
                bot.send_audio(chat_id, f, caption=f"🎵 {brand}")
        else:
            meta = probe_video(fp)
            thumb = generate_thumbnail(fp)
            try:
                with open(fp, "rb") as f:
                    bot.send_video(chat_id, f, caption=f"🎬 {brand}",
                                   width=meta.get("width"), height=meta.get("height"),
                                   duration=meta.get("duration"), supports_streaming=True)
            except Exception:
                with open(fp, "rb") as f:
                    bot.send_document(chat_id, f, caption=f"🎬 {brand}")
            if thumb:
                clean_file(thumb)

        safe_delete(bot, chat_id, wait_msg.message_id)

    except Exception as e:
        logger.error(f"YouTube worker error: {e}")
        try:
            err = str(e)
            if "filesize" in err.lower() or "max" in err.lower():
                safe_edit(bot, chat_id, wait_msg.message_id, "⚠️ Fayl 50MB limitini aşır.")
            else:
                safe_edit(bot, chat_id, wait_msg.message_id, "❌ Xəta baş verdi. Yenidən cəhd edin.")
        except Exception:
            pass
    finally:
        if fp:
            clean_file(fp)
        cleanup_temp(uid)
        finish_download(user_id)


# ─── Social Media Download Handler ───

def start_social_worker(bot, chat_id, user_id, url, platform, wait_msg):
    """Background worker for Instagram/TikTok/etc downloads."""
    uid = f"soc_{user_id}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    fp = None
    try:
        safe_edit(bot, chat_id, wait_msg.message_id, f"⬇️ {platform} yüklənir...")

        fp = download_social_media(url, platform, uid)
        if not fp or not os.path.exists(fp):
            safe_edit(bot, chat_id, wait_msg.message_id, "❌ Video tapılmadı və ya çox böyükdür.")
            finish_download(user_id)
            return

        sz = file_size_mb(fp)
        safe_edit(bot, chat_id, wait_msg.message_id, "📤 Telegram-a yüklənir...")

        meta = probe_video(fp)
        thumb = generate_thumbnail(fp)
        sent = False
        brand = get_setting("caption_branding") or "🔹 DOWNLOADED BY GASHAM🔹"
        try:
            with open(fp, "rb") as f:
                bot.send_video(chat_id, f, supports_streaming=True,
                               width=meta.get("width"), height=meta.get("height"),
                               duration=meta.get("duration"),
                               caption=f"🔹 {brand}")
            sent = True
        except Exception:
            try:
                with open(fp, "rb") as f:
                    bot.send_document(chat_id, f, caption=f"🔹 {brand}")
                sent = True
            except Exception:
                pass

        if thumb:
            clean_file(thumb)

        if sent:
            safe_delete(bot, chat_id, wait_msg.message_id)
            logger.info(f"{platform} OK ({sz:.1f}MB)")
        else:
            safe_edit(bot, chat_id, wait_msg.message_id, "❌ Video göndərilə bilmədi.")

    except Exception as e:
        logger.error(f"{platform} worker error: {e}")
        try:
            safe_edit(bot, chat_id, wait_msg.message_id, f"❌ {platform} yüklənə bilmədi. Linki yoxlayın.")
        except Exception:
            pass
    finally:
        if fp:
            clean_file(fp)
        cleanup_temp(uid)
        finish_download(user_id)


# ─── Setup All Handlers ───

def setup_handlers(bot):

    # Start worker threads (3 workers)
    for i in range(3):
        t = threading.Thread(target=queue_worker, daemon=True, name=f"Worker-{i+1}")
        t.start()

    # Start cleanup scheduler
    schedule_cleanup()

    # ══════════════════════════════════════════
    # /start command
    # ══════════════════════════════════════════
    @bot.message_handler(commands=['start', 'help'])
    def cmd_start(message):
        if not register_user(message, bot):
            return
        cid = message.chat.id
        text = get_setting("start_message") or (
            "👋 **Salam! Mən media yükləyici botam.**\n\n"
            "Linkləri mənə göndərin və mən onları yükləyib sizə göndərim:\n"
            "🎬 **YouTube** → MP4 (video) / MP3 (audio) seçimi\n"
            "📸 **Instagram** → Avtomatik MP4\n"
            "🎵 **TikTok** → Avtomatik MP4\n"
            "🌐 **Facebook, Twitter, Reddit, Pinterest, Vimeo**\n\n"
            "Sadəcə linki yapışdırın, qalanını mənə buraxın!"
        )
        mid = get_setting("start_media_id")
        mtype = get_setting("start_media_type")
        if mid and mtype:
            try:
                if mtype == "photo":
                    bot.send_photo(cid, mid, caption=text, parse_mode="Markdown")
                elif mtype == "video":
                    bot.send_video(cid, mid, caption=text, parse_mode="Markdown")
                return
            except Exception:
                pass
        bot.send_message(cid, text, parse_mode="Markdown")

    # ══════════════════════════════════════════
    # /66 admin panel
    # ══════════════════════════════════════════
    @bot.message_handler(commands=['66'])
    def cmd_admin(message):
        uid = message.from_user.id
        if not is_admin(uid):
            return
        show_admin_menu(bot, message.chat.id)

    # ══════════════════════════════════════════
    # /gaga user management (hidden, admin only)
    # ══════════════════════════════════════════
    @bot.message_handler(commands=['gaga'])
    def cmd_gaga(message):
        uid = message.from_user.id
        if not is_admin(uid):
            return
        show_user_menu(bot, message.chat.id)

    # ══════════════════════════════════════════
    # Callback handler
    # ══════════════════════════════════════════
    @bot.callback_query_handler(func=lambda c: True)
    def handle_callback(call):
        uid = call.from_user.id
        cid = call.message.chat.id
        d = call.data

        # ── YouTube format selection (any user) ──
        if d in ("yt_mp4", "yt_mp3"):
            bot.answer_callback_query(call.id)
            fmt = "mp4" if d == "yt_mp4" else "mp3"
            url = user_last_url.get(uid)
            if not url:
                bot.send_message(cid, "❌ Link tapılmadı. YouTube linkini yenidən göndərin.")
                return
            if not can_download(uid):
                bot.send_message(cid, "⚠️ Artıq 2 aktiv yükləmə var. Gözləyin.")
                return
            safe_delete(bot, cid, call.message.message_id)
            wm = bot.send_message(cid, "⏳ Sıraya əlavə olundu...")
            db_log_download(uid, url, f"YouTube {fmt.upper()}")
            enqueue_download(lambda: start_yt_worker(bot, cid, uid, url, fmt, wm))
            return

        # ── Admin-only below ──
        if not is_admin(uid):
            bot.answer_callback_query(call.id, "❌ İcazəniz yoxdur.")
            return
        bot.answer_callback_query(call.id)

        if d == "admin_menu":
            show_admin_menu(bot, cid, call.message.message_id)

        elif d == "admin_broadcast":
            user_states[uid] = {"state": "br"}
            bot.send_message(cid,
                "📢 **Toplu mesaj yazın.**\n\nGöndərdiyiniz mesaj bütün istifadəçilərə çatdırılacaq.\nLəğv: /cancel")

        elif d == "admin_clones":
            show_clones_menu(bot, cid, call.message.message_id)

        elif d == "admin_clone_add":
            user_states[uid] = {"state": "clone"}
            bot.send_message(cid,
                "🔑 **Bot tokenini göndərin.**\nLəğv: /cancel")

        elif d.startswith("admin_clone_del_"):
            token = d.replace("admin_clone_del_", "")
            db_delete_clone(token)
            if token in active_clones:
                try:
                    active_clones[token].stop_polling()
                except Exception:
                    pass
                del active_clones[token]
            bot.send_message(cid, "✅ Klon silindi.")
            show_clones_menu(bot, cid)

        elif d == "admin_caption":
            cur = get_setting("caption_branding") or "🔹 DOWNLOADED BY GASHAM🔹"
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("✍️ Dəyiş", callback_data="admin_caption_set"),
                types.InlineKeyboardButton("🔄 Sıfırla", callback_data="admin_caption_reset"),
                types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu"),
            )
            txt = f"✍️ **Yükləmə mətni**\nHazırki: `{cur}`"
            try:
                bot.edit_message_text(txt, cid, call.message.message_id, reply_markup=mk, parse_mode="Markdown")
            except Exception:
                bot.send_message(cid, txt, reply_markup=mk, parse_mode="Markdown")

        elif d == "admin_caption_set":
            user_states[uid] = {"state": "cap"}
            bot.send_message(cid, "✍️ **Yeni mətni göndərin.**\nLəğv: /cancel")

        elif d == "admin_caption_reset":
            delete_setting("caption_branding")
            bot.send_message(cid, "✅ Mətn sıfırlandı.")
            show_admin_menu(bot, cid)

        elif d == "admin_start":
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("✍️ Mətni Dəyiş", callback_data="admin_start_text_set"),
                types.InlineKeyboardButton("🔄 Sıfırla", callback_data="admin_start_text_reset"),
                types.InlineKeyboardButton("🖼️ Media", callback_data="admin_start_media"),
                types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu"),
            )
            try:
                bot.edit_message_text("📝 **Start tənzimləmələri**", cid, call.message.message_id,
                                      reply_markup=mk, parse_mode="Markdown")
            except Exception:
                bot.send_message(cid, "📝 **Start tənzimləmələri**", reply_markup=mk, parse_mode="Markdown")

        elif d == "admin_start_text_set":
            user_states[uid] = {"state": "stxt"}
            bot.send_message(cid, "📝 **Yeni start mətnini göndərin.**\nLəğv: /cancel")

        elif d == "admin_start_text_reset":
            delete_setting("start_message")
            bot.send_message(cid, "✅ Start mətni sıfırlandı.")

        elif d == "admin_start_media":
            mk = types.InlineKeyboardMarkup()
            mid = get_setting("start_media_id")
            if mid:
                mk.add(types.InlineKeyboardButton("📸 Yeni Media", callback_data="admin_start_media_set"),
                       types.InlineKeyboardButton("🗑 Sil", callback_data="admin_start_media_del"))
            else:
                mk.add(types.InlineKeyboardButton("📸 Media Yüklə", callback_data="admin_start_media_set"))
            mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_start"))
            try:
                bot.edit_message_text("🖼️ **Start mediası**", cid, call.message.message_id,
                                      reply_markup=mk, parse_mode="Markdown")
            except Exception:
                bot.send_message(cid, "🖼️ **Start mediası**", reply_markup=mk, parse_mode="Markdown")

        elif d == "admin_start_media_set":
            user_states[uid] = {"state": "smed"}
            bot.send_message(cid, "📸 **Media göndərin** (şəkil və ya video).\nLəğv: /cancel")

        elif d == "admin_start_media_del":
            delete_setting("start_media_id")
            delete_setting("start_media_type")
            bot.send_message(cid, "✅ Start mediası silindi.")

        elif d == "admin_users":
            show_user_menu(bot, cid, call.message.message_id)

        elif d == "admin_users_list":
            users = db_get_recent_users(15)
            if not users:
                bot.send_message(cid, "❌ Heç istifadəçi yoxdur.")
                return
            mk = types.InlineKeyboardMarkup(row_width=1)
            for u in users:
                nm = u.get("first_name", "?")[:15]
                un = f"@{u['username']}" if u.get("username") else ""
                st = "🔇" if u.get("is_banned") else "✅"
                mk.add(types.InlineKeyboardButton(f"{st} {nm} {un}",
                        callback_data=f"admin_user_{u['user_id']}"))
            mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_users"))
            bot.send_message(cid, f"👥 **Son 15 istifadəçi**", reply_markup=mk, parse_mode="Markdown")

        elif d == "admin_users_search":
            user_states[uid] = {"state": "usearch"}
            bot.send_message(cid, "🔍 **İstifadəçi adı və ya ID yazın.**\nLəğv: /cancel")

        elif d == "admin_users_stats":
            bot.send_message(cid,
                f"📊 **Bot Statistikası**\n\n"
                f"👥 Ümumi: {db_count_users()}\n"
                f"🚫 Bloklanmış: {db_count_banned_users()}\n"
                f"🤖 Klonlar: {len(active_clones)}")

        elif d.startswith("admin_user_ban_"):
            tid = int(d.replace("admin_user_ban_", ""))
            db_ban_user(tid, 1)
            bot.send_message(cid, f"🚫 {tid} bloklandı.")
            show_user_detail(bot, cid, tid)

        elif d.startswith("admin_user_unban_"):
            tid = int(d.replace("admin_user_unban_", ""))
            db_ban_user(tid, 0)
            bot.send_message(cid, f"✅ {tid} bloku açıldı.")
            show_user_detail(bot, cid, tid)

        elif d.startswith("admin_user_delhist_"):
            tid = int(d.replace("admin_user_delhist_", ""))
            from database import db_delete_user_downloads
            db_delete_user_downloads(tid)
            bot.send_message(cid, f"🗑 {tid} istifadəçisinin yükləmə tarixçəsi silindi.")
            show_user_detail(bot, cid, tid)

        elif d.startswith("admin_user_"):
            try:
                tid = int(d.replace("admin_user_", ""))
                show_user_detail(bot, cid, tid)
            except ValueError:
                pass

    # ══════════════════════════════════════════
    # All text messages
    # ══════════════════════════════════════════
    @bot.message_handler(func=lambda m: True, content_types=['text', 'photo', 'video'])
    def handle_msg(message):
        if not register_user(message, bot):
            return
        cid = message.chat.id
        uid = message.from_user.id

        # ── Admin state input handling ──
        if is_admin(uid) and uid in user_states:
            st = user_states[uid]["state"]
            txt = message.text.strip() if message.text else ""

            if txt == "/cancel":
                user_states.pop(uid, None)
                bot.send_message(cid, "❌ Ləğv edildi.")
                show_admin_menu(bot, cid)
                return

            if st == "br":
                users = db_get_all_users()
                ok, fail = 0, 0
                pm = bot.send_message(cid, f"📡 Göndərilir... 0/{len(users)}")
                for i, u in enumerate(users, 1):
                    try:
                        bot.copy_message(u["user_id"], cid, message.message_id)
                        ok += 1
                    except Exception:
                        fail += 1
                    if i % 10 == 0:
                        try:
                            bot.edit_message_text(f"📡 {i}/{len(users)} OK:{ok}", cid, pm.message_id)
                        except Exception:
                            pass
                try:
                    bot.edit_message_text(f"✅ Tamam: OK:{ok} Fail:{fail}", cid, pm.message_id)
                except Exception:
                    bot.send_message(cid, f"✅ Tamam: OK:{ok} Fail:{fail}")
                user_states.pop(uid, None)
                return

            elif st == "clone":
                success, res = start_clone(txt)
                if success:
                    db_add_clone(txt, res)
                    bot.send_message(cid, f"✅ Klon @{res} aktiv!")
                else:
                    bot.send_message(cid, f"❌ Xəta: {res}")
                user_states.pop(uid, None)
                return

            elif st == "cap":
                set_setting("caption_branding", txt)
                bot.send_message(cid, f"✅ Mətn dəyişdirildi: `{txt}`", parse_mode="Markdown")
                user_states.pop(uid, None)
                return

            elif st == "stxt":
                set_setting("start_message", txt)
                bot.send_message(cid, "✅ Start mətni dəyişdirildi!")
                user_states.pop(uid, None)
                return

            elif st == "usearch":
                target = db_get_user_by_username(txt.lstrip("@"))
                if target:
                    show_user_detail(bot, cid, target["user_id"])
                else:
                    try:
                        target = db_get_user(int(txt))
                        if target:
                            show_user_detail(bot, cid, target["user_id"])
                        else:
                            bot.send_message(cid, "❌ Tapılmadı.")
                    except ValueError:
                        bot.send_message(cid, "❌ İstifadəçi adı və ya ID yazın.")
                user_states.pop(uid, None)
                return

        # ── Handle media for start media setting ──
        if is_admin(uid) and uid in user_states:
            st = user_states[uid]["state"]
            if st == "smed":
                if message.photo:
                    set_setting("start_media_id", message.photo[-1].file_id)
                    set_setting("start_media_type", "photo")
                    bot.send_message(cid, "✅ Start şəkli yeniləndi!")
                    user_states.pop(uid, None)
                elif message.video:
                    set_setting("start_media_id", message.video.file_id)
                    set_setting("start_media_type", "video")
                    bot.send_message(cid, "✅ Start videosu yeniləndi!")
                    user_states.pop(uid, None)
                return

        # ── Text-only from here ──
        if not message.text:
            return

        text = message.text.strip()
        platform = detect_platform(text)

        if not platform:
            # Unknown platform
            msg = bot.reply_to(message, "❓ Dəstəklənən platformalar:\n"
                              "YouTube, Instagram, TikTok, Facebook, Twitter/X,\n"
                              "Pinterest, Reddit, Vimeo")
            return

        # ── YouTube: show format selection ──
        if platform == "YouTube":
            safe_reaction(bot, cid, message.message_id, "🎬")
            user_last_url[uid] = text
            mk = types.InlineKeyboardMarkup(row_width=2)
            mk.add(
                types.InlineKeyboardButton("🎬 MP4 (Video)", callback_data="yt_mp4"),
                types.InlineKeyboardButton("🎵 MP3 (Audio)", callback_data="yt_mp3"),
            )
            bot.send_message(cid, "**YouTube formatını seçin:**", reply_markup=mk, parse_mode="Markdown")
            return

        # ── Other platforms: direct download ──
        reaction_map = {
            "Instagram": "📸", "TikTok": "🎵", "Facebook": "👍",
            "Twitter": "🐦", "Pinterest": "📌", "Reddit": "👽", "Vimeo": "🎥"
        }
        emoji = reaction_map.get(platform, "📥")
        safe_reaction(bot, cid, message.message_id, emoji)

        if not can_download(uid):
            bot.send_message(cid, "⚠️ Artıq 2 aktiv yükləmə var. Gözləyin.")
            return

        wm = bot.send_message(cid, f"⏳ {platform} yüklənir...")
        db_log_download(uid, text, platform)

        enqueue_download(lambda: start_social_worker(bot, cid, uid, text, platform, wm))


# ─── Admin UI Functions ───

def show_admin_menu(bot, cid, msg_id=None):
    mk = types.InlineKeyboardMarkup(row_width=2)
    mk.add(
        types.InlineKeyboardButton("📢 Toplu Mesaj", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("🤖 Bot Klonla", callback_data="admin_clones"),
        types.InlineKeyboardButton("✍️ Yükləmə Mətni", callback_data="admin_caption"),
        types.InlineKeyboardButton("📝 Start", callback_data="admin_start"),
    )
    txt = "⚓ **ADMIN PANEL** ⚓"
    if msg_id:
        try:
            bot.edit_message_text(txt, cid, msg_id, reply_markup=mk, parse_mode="Markdown")
            return
        except Exception:
            pass
    bot.send_message(cid, txt, reply_markup=mk, parse_mode="Markdown")


def show_clones_menu(bot, cid, msg_id=None):
    clones = db_get_all_clones()
    mk = types.InlineKeyboardMarkup(row_width=1)
    for c in clones:
        mk.add(types.InlineKeyboardButton(f"❌ @{c['username']}", callback_data=f"admin_clone_del_{c['token']}"))
    mk.add(types.InlineKeyboardButton("➕ Yeni Klon", callback_data="admin_clone_add"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu"))
    txt = f"🤖 **Klon Botlar ({len(clones)})**"
    if msg_id:
        try:
            bot.edit_message_text(txt, cid, msg_id, reply_markup=mk, parse_mode="Markdown")
            return
        except Exception:
            pass
    bot.send_message(cid, txt, reply_markup=mk, parse_mode="Markdown")


def show_user_detail(bot, cid, target_id):
    user = db_get_user(target_id)
    if not user:
        bot.send_message(cid, "❌ İstifadəçi tapılmadı.")
        return
    txt = (
        f"**İstifadəçi:**\n"
        f"🆔 `{user['user_id']}`\n"
        f"👤 {user.get('first_name', '?')}\n"
        f"📛 @{user.get('username', 'yox')}\n"
        f"🚫 Blok: {'Bəli' if user.get('is_banned') else 'Xeyr'}\n"
        f"📅 {user.get('joined_at', '?')}"
    )
    dl = db_get_user_downloads(target_id, 3)
    if dl:
        txt += "\n\n**Son yükləmələr:**\n"
        for d in dl:
            txt += f"• [{d['platform']}] {d['url'][:30]}...\n"
    mk = types.InlineKeyboardMarkup(row_width=2)
    if user.get("is_banned"):
        mk.add(types.InlineKeyboardButton("✅ Bloku Aç", callback_data=f"admin_user_unban_{target_id}"))
    else:
        mk.add(types.InlineKeyboardButton("🚫 Blokla", callback_data=f"admin_user_ban_{target_id}"))
    mk.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_users_list"))
    bot.send_message(cid, txt, reply_markup=mk, parse_mode="Markdown")


def show_user_menu(bot, cid, msg_id=None):
    """User list for /gaga command - users with download history."""
    from database import db_get_user_downloads_all
    users = db_get_recent_users(20)
    if not users:
        bot.send_message(cid, "❌ Hələ heç bir istifadəçi yoxdur.")
        return
    mk = types.InlineKeyboardMarkup(row_width=1)
    for u in users:
        nm = u.get("first_name", "?")[:18]
        un = f"@{u['username']}" if u.get("username") else ""
        st = "🔇" if u.get("is_banned") else "✅"
        dl = db_get_user_downloads(u["user_id"], 1)
        has_dl = "📥" if dl else ""
        mk.add(types.InlineKeyboardButton(f"{st} {nm} {un} {has_dl}",
                callback_data=f"admin_user_{u['user_id']}"))
    cnt = db_count_users()
    bnd = db_count_banned_users()
    txt = f"👥 **İstifadəçilər ({cnt})** | Blok: {bnd}\n\nİstifadəçi seçin:"
    if msg_id:
        try:
            bot.edit_message_text(txt, cid, msg_id, reply_markup=mk, parse_mode="Markdown")
            return
        except Exception:
            pass
    bot.send_message(cid, txt, reply_markup=mk, parse_mode="Markdown")


# ─── Clone Bot Management ───

def start_clone(token):
    if token in active_clones:
        try:
            return True, active_clones[token].get_me().username
        except Exception:
            pass
    try:
        nb = telebot.TeleBot(token)
        me = nb.get_me()
        setup_handlers(nb)
        t = threading.Thread(target=nb.infinity_polling, kwargs={"timeout": 10, "long_polling_timeout": 5}, daemon=True)
        t.start()
        active_clones[token] = nb
        return True, me.username
    except Exception as e:
        return False, str(e)


def load_clones():
    clones = db_get_all_clones()
    for c in clones:
        success, res = start_clone(c["token"])
        if success:
            logger.info(f"Clone @{res} started")
        else:
            logger.error(f"Clone failed: {res}")
