import os
import re
import time
import subprocess
import json
import logging
import threading
from typing import Optional

logger = logging.getLogger("tg_downloader.downloader")

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB Telegram limit

# URL patterns for each platform
YOUTUBE_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|'
    r'youtube\.com/embed/|youtube\.com/v/|youtube\.com/live/|m\.youtube\.com/watch\?v=)([\w-]+)'
)
INSTAGRAM_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv|stories|share)/([\w-]+)'
)
TIKTOK_PATTERN = re.compile(
    r'(?:https?://)?(?:www\.)?(?:vm\.|vt\.)?tiktok\.com/([\w-]+|t/[\w-]+|@[\w.-]+/video/\d+)'
)

PLATFORM_RULES = [
    ("YouTube", YOUTUBE_PATTERN, ["youtube.com", "youtu.be"]),
    ("Instagram", INSTAGRAM_PATTERN, ["instagram.com"]),
    ("TikTok", TIKTOK_PATTERN, ["tiktok.com", "vm.tiktok.com"]),
    ("Facebook", None, ["facebook.com", "fb.com", "fb.watch"]),
    ("Twitter", None, ["twitter.com", "x.com", "t.co"]),
    ("Pinterest", None, ["pinterest.com", "pin.it"]),
    ("Reddit", None, ["reddit.com", "redd.it"]),
    ("Vimeo", None, ["vimeo.com"]),
]


def detect_platform(url: str) -> Optional[str]:
    """Detect platform from URL."""
    for name, pattern, domains in PLATFORM_RULES:
        if pattern and pattern.search(url):
            return name
        for domain in domains:
            if domain in url:
                return name
    return None


def cleanup_temp(prefix: str = ""):
    """Remove temporary download fragments."""
    if not os.path.exists(DOWNLOAD_DIR):
        return
    now = time.time()
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        try:
            if os.path.isfile(path):
                if prefix and f.startswith(prefix):
                    os.unlink(path)
                elif now - os.path.getmtime(path) > 1800:  # 30 min
                    os.unlink(path)
        except Exception:
            pass


def probe_video(file_path: str) -> dict:
    """Return {width, height, duration} via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format") or {}
        out = {}
        if isinstance(stream.get("width"), int) and stream["width"] > 0:
            out["width"] = stream["width"]
        if isinstance(stream.get("height"), int) and stream["height"] > 0:
            out["height"] = stream["height"]
        if fmt.get("duration"):
            try:
                out["duration"] = int(float(fmt["duration"]))
            except (TypeError, ValueError):
                pass
        return out
    except Exception as e:
        logger.debug(f"ffprobe failed: {e}")
        return {}


def generate_thumbnail(video_path: str) -> Optional[str]:
    """Extract a JPG thumbnail frame."""
    thumb_path = video_path.rsplit(".", 1)[0] + "_thumb.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1",
             "-i", video_path, "-frames:v", "1",
             "-vf", "scale='min(320,iw)':-2", "-q:v", "5", thumb_path],
            capture_output=True, timeout=15,
        )
        if os.path.exists(thumb_path) and os.path.getsize(thumb_path) > 0:
            return thumb_path
    except Exception:
        pass
    return None


def file_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1024 * 1024)
    except Exception:
        return 0.0


def download_youtube(url: str, fmt: str = "mp4", unique_id: str = "") -> Optional[str]:
    """
    Download YouTube video as MP4 or MP3.
    fmt: 'mp4' for video or 'mp3' for audio.
    Returns file path or None.
    """
    import yt_dlp

    # First get the video title
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'nocheckcertificate': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'video') or 'video'
            # Sanitize title for filename
            title = re.sub(r'[\\/*?:"<>|]', '', title)[:80].strip()
    except Exception:
        title = f"video_{unique_id}" if unique_id else "video"

    if not title:
        title = f"video_{unique_id}" if unique_id else "video"

    # Use title as filename
    filename = f"{title}.%(ext)s" if fmt == "mp4" else f"{title}.mp3"
    outtmpl = os.path.join(DOWNLOAD_DIR, filename)

    if fmt == "mp3":
        opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{title}.%(ext)s"),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'max_filesize': MAX_FILE_SIZE_BYTES,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
        }
    else:
        opts = {
            'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{title}.%(ext)s"),
            'merge_output_format': 'mp4',
            'max_filesize': MAX_FILE_SIZE_BYTES,
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
        }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        # Find the downloaded file
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(title) or f.startswith(f"{title}."):
                fp = os.path.join(DOWNLOAD_DIR, f)
                if os.path.getsize(fp) > 0:
                    return fp
        return None
    except Exception as e:
        logger.error(f"YouTube download error: {e}")
        raise


def download_social_media(url: str, platform: str, unique_id: str = "") -> Optional[str]:
    """
    Download from Instagram, TikTok, Facebook, Twitter etc using yt-dlp.
    Returns file path or None.
    """
    import yt_dlp

    # First get the media title
    try:
        with yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True, 'nocheckcertificate': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', platform) or platform
            title = re.sub(r'[\\/*?:"<>|]', '', title)[:80].strip()
    except Exception:
        title = f"{platform}_{unique_id}" if unique_id else platform

    if not title:
        title = f"{platform}_{unique_id}" if unique_id else platform

    opts = {
        'format': 'bestvideo+bestaudio/best',
        'outtmpl': os.path.join(DOWNLOAD_DIR, f"{title}.%(ext)s"),
        'merge_output_format': 'mp4',
        'max_filesize': MAX_FILE_SIZE_BYTES,
        'quiet': True,
        'no_warnings': True,
        'nocheckcertificate': True,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 3,
        'extractor_retries': 3,
        'ignoreerrors': False,
    }

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(title):
                fp = os.path.join(DOWNLOAD_DIR, f)
                if os.path.getsize(fp) > 2048:
                    return fp
        return None
    except Exception as e:
        logger.error(f"{platform} download error: {e}")
        raise


def schedule_cleanup():
    """Background cleanup thread."""
    def _worker():
        while True:
            time.sleep(1800)  # 30 min
            try:
                cleanup_temp()
            except Exception:
                pass

    t = threading.Thread(target=_worker, daemon=True, name="CleanupWorker")
    t.start()
