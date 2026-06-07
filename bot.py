import os
import re
import telebot
import yt_dlp
import threading
from dotenv import load_dotenv
from database import (
    init_db, db_register_user, db_is_banned, db_ban_user,
    db_log_download, db_get_user, db_get_user_by_username,
    db_get_all_users, db_get_recent_users, db_count_users,
    db_count_banned_users, db_get_user_downloads, get_setting,
    set_setting, delete_setting, db_add_clone, db_get_all_clones,
    db_delete_clone
)

# Load environment variables
load_dotenv()

# Active clone bot instances {token: bot_instance}
active_clones = {}

# Active admin states {user_id: {'state': state_name}}
user_states = {}

DOWNLOAD_DIR = "downloads"
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# Regular expressions for matching links
YOUTUBE_REGEX = r"(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/live/|m\.youtube\.com/watch\?v=)([\w-]+)"
INSTAGRAM_REGEX = r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv|stories|share)/([\w-]+)"
TIKTOK_REGEX = r"(?:https?://)?(?:www\.)?(?:vm\.|vt\.)?tiktok\.com/([\w-]+|t/[\w-]+|@[\w.-]+/video/\d+|@[\w.-]+/video/v/\d+)"

def clean_file(filepath):
    """Safely delete a file if it exists."""
    try:
        if filepath and os.path.exists(filepath):
            os.remove(filepath)
            print(f"Cleaned up file: {filepath}")
    except Exception as e:
        print(f"Error cleaning up file {filepath}: {e}")

def is_admin(user_id):
    """Check if user is admin. Auto-assigns first user if no admin exists."""
    env_admin = os.getenv("ADMIN_ID")
    if env_admin and str(user_id) == str(env_admin):
        return True
    db_admin = get_setting("admin_id")
    if db_admin and str(user_id) == str(db_admin):
        return True
    if not db_admin and not env_admin:
        set_setting("admin_id", str(user_id))
        return True
    return False

# UI Rendering Functions
def send_admin_menu(bot_inst, chat_id, message_id=None):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=2)
    
    markup.add(
        types.InlineKeyboardButton("📢 Toplu Mesaj", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("🤖 Bot Klonla", callback_data="admin_clones")
    )
    markup.add(
        types.InlineKeyboardButton("✍️ Yükləmə Mətni", callback_data="admin_caption"),
        types.InlineKeyboardButton("📝 Start Mətni", callback_data="admin_start_text")
    )
    markup.add(
        types.InlineKeyboardButton("🖼️ Start Media", callback_data="admin_start_media"),
        types.InlineKeyboardButton("👥 İstifadəçilər", callback_data="admin_users")
    )
    
    text = "⚓ **ADMIN PANEL** ⚓\n\nZəhmət olmasa etmək istədiyiniz əməliyyatı seçin:"
    
    if message_id:
        try:
            bot_inst.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception:
            bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def send_clones_menu(bot_inst, chat_id, message_id=None):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    clones = db_get_all_clones()
    for c in clones:
        markup.add(types.InlineKeyboardButton(f"❌ Sil: @{c['username']}", callback_data=f"admin_clone_del_{c['token']}"))
        
    markup.add(types.InlineKeyboardButton("➕ Yeni Bot Klonla", callback_data="admin_clone_add"))
    markup.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu"))
    
    text = f"🤖 **Bot Klonları ({len(clones)})**\n\nHal-hazırda aktiv olan klon botlar aşağıda qeyd olunub:"
    
    if message_id:
        try:
            bot_inst.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception:
            bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def send_caption_menu(bot_inst, chat_id, message_id=None):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    markup.add(types.InlineKeyboardButton("✍️ Yeni Mətn Təyin Et", callback_data="admin_caption_set"))
    markup.add(types.InlineKeyboardButton("🔄 Default Vəziyyətinə Qaytar", callback_data="admin_caption_reset"))
    markup.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu"))
    
    current = get_setting("caption_branding") or "⚓ BY ORUJOV ⚓"
    text = f"✍️ **Yükləmə Mətni (Branding)**\n\nHal-hazırki mətn:\n`{current}`"
    
    if message_id:
        try:
            bot_inst.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception:
            bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def send_start_text_menu(bot_inst, chat_id, message_id=None):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    markup.add(types.InlineKeyboardButton("✍️ Yeni /start Mətni Təyin Et", callback_data="admin_start_text_set"))
    markup.add(types.InlineKeyboardButton("🔄 Default Vəziyyətinə Qaytar", callback_data="admin_start_text_reset"))
    markup.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu"))
    
    current = get_setting("start_message") or (
        "👋 **Salam! Mən media yükləyici botam.**\n\n"
        "Linkləri mənə göndərin və mən onları yükləyib sizə göndərim:\n"
        "🎵 **YouTube** linki göndərdikdə -> **MP3** (səs)\n"
        "🎬 **Instagram** və ya **TikTok** linki göndərdikdə -> **MP4** (video)\n\n"
        "Sadəcə linki kopyalayıb bura yapışdırın!"
    )
    text = f"📝 **/start Komandası Mətni**\n\nHal-hazırki mətn:\n\n{current}"
    
    if message_id:
        try:
            bot_inst.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception:
            bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def send_start_media_menu(bot_inst, chat_id, message_id=None):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    markup.add(types.InlineKeyboardButton("🖼️ Şəkil/Video Təyin Et", callback_data="admin_start_media_set"))
    markup.add(types.InlineKeyboardButton("❌ Media Faylını Sil", callback_data="admin_start_media_reset"))
    markup.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu"))
    
    media_id = get_setting("start_media_id")
    media_type = get_setting("start_media_type")
    
    status = f"Aktivdir ({media_type})" if media_id else "Aktiv deyil (Yalnız mətn)"
    text = f"🖼️ **/start Üçün Media Faylı**\n\nStatus: **{status}**"
    
    if message_id:
        try:
            bot_inst.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception:
            bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def send_users_menu(bot_inst, chat_id, message_id=None):
    from telebot import types
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    markup.add(
        types.InlineKeyboardButton("📋 İstifadəçi Siyahısı", callback_data="admin_users_list"),
        types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_menu")
    )
    
    total = db_count_users()
    banned = db_count_banned_users()
    text = f"👥 **İstifadəçi Statistikası**\n\n👥 Ümumi istifadəçilər: **{total}**\n🚫 Bloklananlar: **{banned}**"
    
    if message_id:
        try:
            bot_inst.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        except Exception:
            bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
    else:
        bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def show_users_list(bot_inst, chat_id):
    from telebot import types
    import html
    users = db_get_recent_users(limit=15)
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    text = "📋 <b>Son 15 Aktiv İstifadəçi:</b>\n\n"
    for idx, u in enumerate(users):
        first_name_esc = html.escape(u['first_name'] or "İstifadəçi")
        username_str = f" (@{html.escape(u['username'])})" if u['username'] else ""
        banned_str = " 🚫" if u['is_banned'] else ""
        text += f"{idx+1}. ID: <code>{u['user_id']}</code> - {first_name_esc}{username_str}{banned_str}\n"
        markup.add(types.InlineKeyboardButton(f"Bax: {first_name_esc}", callback_data=f"admin_user_view_{u['user_id']}"))
        
    markup.add(types.InlineKeyboardButton("⬅️ Geri", callback_data="admin_users"))
    bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')

def show_user_details(bot_inst, chat_id, target_id):
    from telebot import types
    import html
    u = db_get_user(target_id)
    if not u:
        bot_inst.send_message(chat_id, "❌ İstifadəçi tapılmadı.")
        return
        
    history = db_get_user_downloads(target_id, limit=5)
    
    history_text = ""
    if history:
        for idx, h in enumerate(history):
            history_text += f"• [{h['platform']}] {html.escape(h['url'][:30])}... ({h['downloaded_at'][:16]})\n"
    else:
        history_text = "Yükləmə keçmişi yoxdur.\n"
        
    first_name_esc = html.escape(u['first_name'] or "İstifadəçi")
    username_str = f"@{html.escape(u['username'])}" if u['username'] else "Yoxdur"
    status_str = "Bloklanıb 🚫" if u['is_banned'] else "Aktiv ✅"
    
    text = (
        f"👤 <b>İstifadəçi Məlumatları</b>\n\n"
        f"🆔 ID: <code>{u['user_id']}</code>\n"
        f"📝 Adı: {first_name_esc}\n"
        f"🔗 Username: {username_str}\n"
        f"📊 Status: <b>{status_str}</b>\n"
        f"📅 Qoşulma tarixi: {u['joined_at'][:16]}\n\n"
        f"🎵 <b>Son 5 Yükləmə:</b>\n{history_text}"
    )
    
    markup = types.InlineKeyboardMarkup(row_width=1)
    if u['is_banned']:
        markup.add(types.InlineKeyboardButton("🔓 Bloku Aç", callback_data=f"admin_user_unban_{target_id}"))
    else:
        markup.add(types.InlineKeyboardButton("🚫 Blokla (Ban)", callback_data=f"admin_user_ban_{target_id}"))
        
    markup.add(types.InlineKeyboardButton("⬅️ İstifadəçilərə Qayıt", callback_data="admin_users"))
    bot_inst.send_message(chat_id, text, reply_markup=markup, parse_mode='HTML')

def handle_admin_state_input(bot_inst, message, state):
    user_id = message.from_user.id
    chat_id = message.chat.id
    text = message.text.strip() if message.text else ""
    
    if text == "/cancel":
        user_states.pop(user_id, None)
        bot_inst.send_message(chat_id, "❌ Əməliyyat ləğv edildi.")
        send_admin_menu(bot_inst, chat_id)
        return
        
    if state == 'waiting_for_broadcast':
        user_states.pop(user_id, None)
        bot_inst.send_message(chat_id, "⌛ Toplu mesaj göndərilir, zəhmət olmasa gözləyin...")
        
        users = db_get_all_users()
        sent_count = 0
        fail_count = 0
        
        for u in users:
            dest_id = u['user_id']
            if dest_id == user_id:
                continue
            try:
                bot_inst.copy_message(chat_id=dest_id, from_chat_id=chat_id, message_id=message.message_id)
                sent_count += 1
            except Exception as e:
                print(f"Failed to send broadcast to {dest_id}: {e}")
                fail_count += 1
                
        bot_inst.send_message(chat_id, f"📢 **Toplu mesaj tamamlandı.**\n\n✅ Göndərildi: {sent_count}\n❌ Uğursuz: {fail_count}")
        send_admin_menu(bot_inst, chat_id)
        
    elif state == 'waiting_for_clone_token':
        if not re.match(r"^\d+:[a-zA-Z0-9_-]+$", text):
            bot_inst.send_message(chat_id, "❌ Yanlış token formatı. Yenidən göndərin və ya ləğv etmək üçün /cancel yazın.")
            return
            
        user_states.pop(user_id, None)
        bot_inst.send_message(chat_id, "⌛ Bot yoxlanılır və işə salınır...")
        
        success, res = start_bot_instance(text)
        if success:
            db_add_clone(text, res)
            bot_inst.send_message(chat_id, f"✅ Bot uğurla klonlandı! Username: @{res}")
        else:
            bot_inst.send_message(chat_id, f"❌ Bot işə salına bilmədi. Xəta: {res}")
            
        send_admin_menu(bot_inst, chat_id)
        
    elif state == 'waiting_for_caption':
        user_states.pop(user_id, None)
        set_setting("caption_branding", text)
        bot_inst.send_message(chat_id, f"✅ Yeni yükləmə mətni təyin edildi:\n`{text}`")
        send_admin_menu(bot_inst, chat_id)
        
    elif state == 'waiting_for_start_text':
        user_states.pop(user_id, None)
        set_setting("start_message", text)
        bot_inst.send_message(chat_id, f"✅ Yeni /start mətni təyin edildi.")
        send_admin_menu(bot_inst, chat_id)
        
    elif state == 'waiting_for_start_media':
        user_states.pop(user_id, None)
        if message.photo:
            file_id = message.photo[-1].file_id
            set_setting("start_media_id", file_id)
            set_setting("start_media_type", "photo")
            bot_inst.send_message(chat_id, "✅ /start şəkli təyin olundu.")
        elif message.video:
            file_id = message.video.file_id
            set_setting("start_media_id", file_id)
            set_setting("start_media_type", "video")
            bot_inst.send_message(chat_id, "✅ /start videosu təyin olundu.")
        else:
            bot_inst.send_message(chat_id, "❌ Zəhmət olmasa yalnız şəkil və ya video göndərin. Ləğv etmək üçün /cancel yazın.")
            user_states[user_id] = {'state': 'waiting_for_start_media'}
            return
            
        send_admin_menu(bot_inst, chat_id)


# Core Downloader Tasks
def download_youtube_mp3(bot_inst, message, wait_msg):
    url = message.text.strip()
    chat_id = message.chat.id
    
    ydl_opts = {
        'format': 'ba[ext=m4a]/ba',  # Prefer m4a for fast audio conversion
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'external_downloader': 'aria2c',
        'external_downloader_args': {
            'aria2c': ['-j', '8', '-x', '8', '-s', '8', '-k', '1M']
        },
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',  # 128k is fast to convert and download
        }],
        'quiet': True,
        'no_warnings': True,
    }
    
    filepath = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Audio')
            filepath = os.path.join(DOWNLOAD_DIR, f"{info['id']}.mp3")
            
            if not os.path.exists(filepath):
                raise FileNotFoundError("Yüklənmiş MP3 faylı tapılmadı.")
            
            file_size = os.path.getsize(filepath)
            if file_size > 50 * 1024 * 1024:
                bot_inst.reply_to(message, "⚠️ Faylın ölçüsü 50 MB limitini aşdı. Telegram bot limiti səbəbindən bunu göndərə bilmirəm.")
                return
            
            branding = get_setting("caption_branding") or "⚓ BY ORUJOV ⚓"
            caption = f"🎵 {title}\n\n{branding}"
            
            with open(filepath, 'rb') as audio:
                bot_inst.send_audio(
                    chat_id, 
                    audio, 
                    title=title, 
                    caption=caption
                )
            
            # Delete wait message
            try:
                bot_inst.delete_message(chat_id, wait_msg.message_id)
            except Exception as e:
                print(f"Error deleting wait message: {e}")
                
    except Exception as e:
        print(f"YouTube download error: {e}")
        bot_inst.reply_to(message, "❌ YouTube linkini yükləyərkən xəta baş verdi. Linkin düzgünlüyünə əmin olun.")
    finally:
        clean_file(filepath)

def download_instagram_tiktok_mp4(bot_inst, message, wait_msg, platform):
    url = message.text.strip()
    chat_id = message.chat.id
    
    ydl_opts = {
        # Download already merged MP4 to avoid CPU-intensive ffmpeg merging on device
        'format': 'best[ext=mp4]/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, '%(id)s.%(ext)s'),
        'external_downloader': 'aria2c',
        'external_downloader_args': {
            'aria2c': ['-j', '8', '-x', '8', '-s', '8', '-k', '1M']
        },
        'quiet': True,
        'no_warnings': True,
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
    }
    
    filepath = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', f'{platform} Videosu')
            
            if 'requested_downloads' in info and len(info['requested_downloads']) > 0:
                filepath = info['requested_downloads'][0]['filepath']
            else:
                ext = info.get('ext', 'mp4')
                filepath = os.path.join(DOWNLOAD_DIR, f"{info['id']}.{ext}")
            
            if not os.path.exists(filepath):
                raise FileNotFoundError("Yüklənmiş MP4 faylı tapılmadı.")
            
            file_size = os.path.getsize(filepath)
            if file_size > 50 * 1024 * 1024:
                bot_inst.reply_to(message, f"⚠️ Faylın ölçüsü 50 MB limitini aşdı. Telegram bot limiti səbəbindən bunu göndərə bilmirəm.")
                return
            
            branding = get_setting("caption_branding") or "⚓ BY ORUJOV ⚓"
            caption = f"🎬 {title}\n\n{branding}"
            
            with open(filepath, 'rb') as video:
                bot_inst.send_video(
                    chat_id, 
                    video, 
                    caption=caption
                )
            
            # Delete wait message
            try:
                bot_inst.delete_message(chat_id, wait_msg.message_id)
            except Exception as e:
                print(f"Error deleting wait message: {e}")
                
    except Exception as e:
        print(f"{platform} download error: {e}")
        bot_inst.reply_to(message, f"❌ {platform} videosunu yükləyərkən xəta baş verdi. Linkin aktiv və açıq (public) olduğuna əmin olun.")
    finally:
        clean_file(filepath)

# Dynamic Bot Handler Registration
def setup_handlers(bot_inst):
    
    def check_user_and_log(message):
        user_id = message.from_user.id
        username = message.from_user.username
        first_name = message.from_user.first_name
        
        # Log user in DB
        db_register_user(user_id, username, first_name)
        
        # Check ban status
        if db_is_banned(user_id):
            bot_inst.reply_to(message, "🚫 Siz botdan istifadə etmək üçün bloklanmısınız.")
            return False
        return True

    @bot_inst.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        if not check_user_and_log(message):
            return
            
        start_text = get_setting("start_message") or (
            "👋 **Salam! Mən media yükləyici botam.**\n\n"
            "Linkləri mənə göndərin və mən onları yükləyib sizə göndərim:\n"
            "🎵 **YouTube** linki göndərdikdə -> **MP3** (səs)\n"
            "🎬 **Instagram** və ya **TikTok** linki göndərdikdə -> **MP4** (video)\n\n"
            "Sadəcə linki kopyalayıb bura yapışdırın!"
        )
        
        media_id = get_setting("start_media_id")
        media_type = get_setting("start_media_type")
        
        if media_id and media_type:
            try:
                if media_type == 'photo':
                    bot_inst.send_photo(message.chat.id, media_id, caption=start_text, parse_mode='Markdown')
                elif media_type == 'video':
                    bot_inst.send_video(message.chat.id, media_id, caption=start_text, parse_mode='Markdown')
            except Exception as e:
                print(f"Error sending welcome media: {e}")
                bot_inst.reply_to(message, start_text, parse_mode='Markdown')
        else:
            bot_inst.reply_to(message, start_text, parse_mode='Markdown')

    @bot_inst.message_handler(commands=['66'])
    def admin_panel_handler(message):
        if not is_admin(message.from_user.id):
            return
        send_admin_menu(bot_inst, message.chat.id)

    @bot_inst.callback_query_handler(func=lambda call: True)
    def callback_inline(call):
        if not is_admin(call.from_user.id):
            bot_inst.answer_callback_query(call.id, "❌ İcazəniz yoxdur.")
            return
            
        chat_id = call.message.chat.id
        user_id = call.from_user.id
        data = call.data
        
        bot_inst.answer_callback_query(call.id)
        
        if data == "admin_menu":
            send_admin_menu(bot_inst, chat_id, message_id=call.message.message_id)
            
        elif data == "admin_broadcast":
            user_states[user_id] = {'state': 'waiting_for_broadcast'}
            bot_inst.send_message(chat_id, "📢 **Toplu mesaj göndərin.**\n\nGöndərdiyiniz hər hansı mesaj digər bütün istifadəçilərə yönləndiriləcək.\n\nİmtina etmək üçün /cancel yazın.")
            
        elif data == "admin_clones":
            send_clones_menu(bot_inst, chat_id, message_id=call.message.message_id)
            
        elif data == "admin_clone_add":
            user_states[user_id] = {'state': 'waiting_for_clone_token'}
            bot_inst.send_message(chat_id, "🔑 **Bot Tokenini göndərin.**\n\nKlonlamaq istədiyiniz botun tokenini yazın.\n\nİmtina etmək üçün /cancel yazın.")
            
        elif data.startswith("admin_clone_del_"):
            token = data.replace("admin_clone_del_", "")
            db_delete_clone(token)
            if token in active_clones:
                try:
                    active_clones[token].stop_polling()
                    del active_clones[token]
                except Exception as e:
                    print(f"Error stopping clone bot: {e}")
            bot_inst.send_message(chat_id, "✅ Klon bot uğurla silindi.")
            send_clones_menu(bot_inst, chat_id)
            
        elif data == "admin_caption":
            send_caption_menu(bot_inst, chat_id, message_id=call.message.message_id)
            
        elif data == "admin_caption_set":
            user_states[user_id] = {'state': 'waiting_for_caption'}
            bot_inst.send_message(chat_id, "✍️ **Yeni yükləmə mətni göndərin.**\n\nDefault olaraq gələn `⚓ BY ORUJOV ⚓` sözünü əvəz edəcək yeni mətni yazın.\n\nİmtina etmək üçün /cancel yazın.")
            
        elif data == "admin_caption_reset":
            delete_setting("caption_branding")
            bot_inst.send_message(chat_id, "✅ Yükləmə mətni default vəziyyətinə (`⚓ BY ORUJOV ⚓`) qaytarıldı.")
            send_caption_menu(bot_inst, chat_id)
            
        elif data == "admin_start_text":
            send_start_text_menu(bot_inst, chat_id, message_id=call.message.message_id)
            
        elif data == "admin_start_text_set":
            user_states[user_id] = {'state': 'waiting_for_start_text'}
            bot_inst.send_message(chat_id, "✍️ **Yeni /start mətni göndərin.**\n\nİmtina etmək üçün /cancel yazın.")
            
        elif data == "admin_start_text_reset":
            delete_setting("start_message")
            bot_inst.send_message(chat_id, "✅ /start mətni default vəziyyətinə qaytarıldı.")
            send_start_text_menu(bot_inst, chat_id)
            
        elif data == "admin_start_media":
            send_start_media_menu(bot_inst, chat_id, message_id=call.message.message_id)
            
        elif data == "admin_start_media_set":
            user_states[user_id] = {'state': 'waiting_for_start_media'}
            bot_inst.send_message(chat_id, "📷 **/start üçün şəkil və ya video göndərin.**\n\nİmtina etmək üçün /cancel yazın.")
            
        elif data == "admin_start_media_reset":
            delete_setting("start_media_id")
            delete_setting("start_media_type")
            bot_inst.send_message(chat_id, "✅ /start media faylı silindi. İndi yalnız mətn göndəriləcək.")
            send_start_media_menu(bot_inst, chat_id)
            
        elif data == "admin_users":
            send_users_menu(bot_inst, chat_id, message_id=call.message.message_id)
            
        elif data == "admin_users_list":
            show_users_list(bot_inst, chat_id)
            

        elif data.startswith("admin_user_view_"):
            target_id = int(data.replace("admin_user_view_", ""))
            show_user_details(bot_inst, chat_id, target_id)
            
        elif data.startswith("admin_user_ban_"):
            target_id = int(data.replace("admin_user_ban_", ""))
            db_ban_user(target_id, 1)
            bot_inst.send_message(chat_id, f"🚫 İstifadəçi ({target_id}) bloklandı.")
            show_user_details(bot_inst, chat_id, target_id)
            
        elif data.startswith("admin_user_unban_"):
            target_id = int(data.replace("admin_user_unban_", ""))
            db_ban_user(target_id, 0)
            bot_inst.send_message(chat_id, f"✅ İstifadəçinin ({target_id}) bloku açıldı.")
            show_user_details(bot_inst, chat_id, target_id)

    @bot_inst.message_handler(func=lambda message: True, content_types=['text', 'photo', 'video', 'document', 'audio', 'voice', 'sticker'])
    def handle_message(message):
        if not check_user_and_log(message):
            return
            
        chat_id = message.chat.id
        user_id = message.from_user.id
        
        # Check if user is in an admin state
        if is_admin(user_id) and user_id in user_states:
            state = user_states[user_id].get('state')
            if state:
                handle_admin_state_input(bot_inst, message, state)
                return
                
        # Non-text messages can be skipped for normal commands
        if not message.text:
            return
            
        text = message.text.strip()
        
        # Match YouTube Link
        if re.search(YOUTUBE_REGEX, text) or "youtube.com" in text or "youtu.be" in text:
            wait_msg = bot_inst.reply_to(message, "🎵 YouTube videosu tapıldı. Səs faylı (MP3) yüklənir, zəhmət olmasa gözləyin...")
            db_log_download(user_id, text, "YouTube")
            download_youtube_mp3(bot_inst, message, wait_msg)
            
        # Match Instagram Link
        elif re.search(INSTAGRAM_REGEX, text) or "instagram.com" in text:
            wait_msg = bot_inst.reply_to(message, "🎬 Instagram videosu tapıldı. Video (MP4) yüklənir, zəhmət olmasa gözləyin...")
            db_log_download(user_id, text, "Instagram")
            download_instagram_tiktok_mp4(bot_inst, message, wait_msg, "Instagram")
            
        # Match TikTok Link
        elif re.search(TIKTOK_REGEX, text) or "tiktok.com" in text:
            wait_msg = bot_inst.reply_to(message, "🎬 TikTok videosu tapıldı. Video (MP4) yüklənir, zəhmət olmasa gözləyin...")
            db_log_download(user_id, text, "TikTok")
            download_instagram_tiktok_mp4(bot_inst, message, wait_msg, "TikTok")

# Clone Bot Instances Runner
def start_bot_instance(token):
    if token in active_clones:
        try:
            return True, active_clones[token].get_me().username
        except Exception:
            pass
    try:
        new_bot = telebot.TeleBot(token)
        me = new_bot.get_me()
        
        setup_handlers(new_bot)
        
        t = threading.Thread(target=new_bot.infinity_polling, kwargs={'timeout': 10, 'long_polling_timeout': 5}, daemon=True)
        t.start()
        
        active_clones[token] = new_bot
        return True, me.username
    except Exception as e:
        return False, str(e)

def load_clones():
    clones = db_get_all_clones()
    print(f"Loading {len(clones)} clone bots...")
    for c in clones:
        success, res = start_bot_instance(c['token'])
        if success:
            print(f"Clone bot @{res} started successfully.")
        else:
            print(f"Failed to start clone bot with token {c['token'][:10]}... Error: {res}")

if __name__ == "__main__":
    # Initialize SQLite database tables
    init_db()
    
    # Load all cloned bots from DB
    load_clones()
    
    # Run the main bot
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or "8286423335:AAH1f5I4NM7B5nmJtEL7i-hCt5Umms7Aj_8"
    main_bot = telebot.TeleBot(TOKEN)
    setup_handlers(main_bot)
    
    print("Main bot is starting...")
    try:
        main_bot.infinity_polling(timeout=10, long_polling_timeout=5)
    except Exception as e:
        print(f"Main bot execution error: {e}")
