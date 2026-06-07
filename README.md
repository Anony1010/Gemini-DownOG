# 🎬 Gemini-DownOG (Telegram Media Downloader Bot)

Bu bot YouTube-dan **MP3** (səs), Instagram və TikTok-dan isə **MP4** (video) yükləyir. Botda həmçinin xüsusi **Admin Panel** (`/66`) yer alır.

## ⚡ Xüsusiyyətlər
- **YouTube MP3**: parallel çoxaxınlı yükləmə (`aria2c` ilə) və sürətli audio konvertasiyası (128 kbps).
- **Instagram & TikTok MP4**: ən yaxşı keyfiyyətdə serverdən birbaşa birləşdirilmiş `.mp4` yüklənməsi (cihazda FFMPEG birləşdirilməsi ləğv edilib).
- **Gözləmə Mesajının Avtomatik Silinməsi**: Media yükləndikdən sonra yüklənmə bildirişi silinir.
- **SQLite3 Bazası**: İstifadəçilərin siyahısı, yükləmə keçmişi, klon botlar və admin tənzimləmələri bazada saxlanılır.
- **⚓ Admin Panel (/66)**:
  - **Toplu Mesaj**: Bütün istifadəçilərə mesaj göndərmə.
  - **Bot Klonlama**: Yeni bot tokeni əlavə edərək botu çoxaltma.
  - **Brendinq**: Yüklənən medianın altındakı imza mətni dəyişdirilməsi.
  - **Start Tənzimləmələri**: /start xoş gəldiniz mətninin və start şəkli/videosunun canlı tənzimlənməsi.
  - **İstifadəçi Siyahısı**: İstifadəçi detallarına baxış, ban/unban əməliyyatları.

## 🛠️ Quraşdırma (Termux / Linux)

1. Sistem asılılıqlarını quraşdırın:
   ```bash
   pkg update -y && pkg install ffmpeg aria2 -y
   ```
2. Python asılılıqlarını quraşdırın:
   ```bash
   pip install -r requirements.txt
   ```
3. `.env` faylını yaradın və doldurun:
   ```env
   TELEGRAM_BOT_TOKEN=YOUR_BOT_TOKEN
   ```
4. Botu işə salın:
   ```bash
   python bot.py
   ```
