import os
import re
import time
import json
import uuid
import logging
import threading
import subprocess
from typing import Optional

logger = logging.getLogger("tg_downloader.downloader")

DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50MB Telegram limit

# ─── URL Patterns ───
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

# ─── Platform-specific User Agents ───
USER_AGENTS = {
    "default": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "TikTok": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Instagram": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.6099.144 Mobile Safari/537.36",
}

RETRY_COUNT = 3
RETRY_DELAY = 3


def sanitize_filename(filename: str, max_len: int = 60) -> str:
    """Remove special chars, limit length."""
    sane = re.sub(r'[\\/*?:"<>|]', "_", filename).strip(" .")
    sane = "".join(c for c in sane if c.isprintable() or c == " ")
    sane = re.sub(r"\s+", "_", sane)
    sane = re.sub(r"__+", "_", sane)
    return sane[:max_len] if sane else "downloaded_media"


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


def schedule_cleanup():
    """Background cleanup thread."""
    def _worker():
        while True:
            time.sleep(1800)
            try:
                cleanup_temp()
            except Exception:
                pass
    t = threading.Thread(target=_worker, daemon=True, name="CleanupWorker")
    t.start()


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
    """Extract a JPG thumbnail frame at ~1s."""
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


def _get_user_agent(platform: str = "") -> str:
    """Get platform-specific User-Agent."""
    for key in (platform, "default"):
        if key in USER_AGENTS:
            return USER_AGENTS[key]
    return USER_AGENTS["default"]


def _run_ytdlp_download(url: str, opts: dict) -> Optional[str]:
    """Run yt-dlp download with retry logic. Returns file path or None."""
    import yt_dlp

    last_error = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            # Find the downloaded file using the output template pattern
            outtmpl = opts.get("outtmpl", "")
            base = os.path.basename(outtmpl.replace("%(ext)s", ""))
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(base) and os.path.getsize(os.path.join(DOWNLOAD_DIR, f)) > 2048:
                    return os.path.join(DOWNLOAD_DIR, f)
            return None
        except yt_dlp.utils.DownloadError as e:
            last_error = e
            err_str = str(e).lower()
            if "unsupported url" in err_str:
                break
            if attempt < RETRY_COUNT:
                wait = RETRY_DELAY * attempt
                logger.warning(f"Download attempt {attempt} failed, retrying in {wait}s: {e}")
                time.sleep(wait)
        except Exception as e:
            last_error = e
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)

    if last_error:
        raise last_error
    return None


def _extract_info(url: str, platform: str = "") -> Optional[dict]:
    """Extract media info without downloading. Returns info dict or None."""
    import yt_dlp
    try:
        ua = _get_user_agent(platform)
        opts = {
            "quiet": True, "no_warnings": True, "simulate": True,
            "extract_flat": False, "skip_download": True,
            "useragent": ua, "verbose": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


# ═══════════════════════════════════════════════
#  YouTube Download
# ═══════════════════════════════════════════════

def download_youtube(url: str, fmt: str = "mp4", unique_id: str = "") -> Optional[str]:
    """
    Download YouTube video as MP4 or MP3 using yt-dlp.
    Uses format selection from media-downloader-bot:
      - mp4: bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best
      - mp3: bestaudio/best
    Returns file path or None.
    """
    import yt_dlp

    # 1. Get media info for title
    info = _extract_info(url, "YouTube")
    title = "YouTube"
    if info:
        title = info.get("title", "YouTube") or "YouTube"
    sanitized = sanitize_filename(title)

    # Use unique_id prefix for tracking + title
    base_name = f"{unique_id}_{sanitized}" if unique_id else sanitized

    ua = _get_user_agent("YouTube")

    if fmt == "mp3":
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(DOWNLOAD_DIR, f"{base_name}.%(ext)s"),
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
            "max_filesize": MAX_FILE_SIZE_BYTES,
            "quiet": True, "no_warnings": True, "nocheckcertificate": True,
            "useragent": ua,
            "retries": RETRY_COUNT,
            "fragment_retries": RETRY_COUNT,
            "retry_sleep_functions": {
                "http": lambda n: RETRY_DELAY,
                "fragment": lambda n: RETRY_DELAY,
            },
        }
    else:
        opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best",
            "outtmpl": os.path.join(DOWNLOAD_DIR, f"{base_name}.%(ext)s"),
            "merge_output_format": "mp4",
            "max_filesize": MAX_FILE_SIZE_BYTES,
            "quiet": True, "no_warnings": True, "nocheckcertificate": True,
            "useragent": ua,
            "retries": RETRY_COUNT,
            "fragment_retries": RETRY_COUNT,
            "retry_sleep_functions": {
                "http": lambda n: RETRY_DELAY,
                "fragment": lambda n: RETRY_DELAY,
            },
        }

    try:
        fp = _run_ytdlp_download(url, opts)
        if fp and os.path.getsize(fp) > 0:
            return fp
        return None
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"YouTube download error: {e}")
        raise


# ═══════════════════════════════════════════════
#  Social Media Download (Instagram, TikTok, etc.)
# ═══════════════════════════════════════════════

def download_social_media(url: str, platform: str, unique_id: str = "") -> Optional[str]:
    """
    Download from Instagram, TikTok, Facebook, Twitter etc using yt-dlp.
    Uses improved retry logic and format selection.
    Returns file path or None.
    """
    import yt_dlp

    # 1. Get media info for title
    info = _extract_info(url, platform)
    title = platform
    if info:
        title = info.get("title", platform) or platform
    sanitized = sanitize_filename(title)

    base_name = f"{unique_id}_{sanitized}" if unique_id else sanitized
    ua = _get_user_agent(platform)

    opts = {
        "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best",
        "outtmpl": os.path.join(DOWNLOAD_DIR, f"{base_name}.%(ext)s"),
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILE_SIZE_BYTES,
        "quiet": True, "no_warnings": True, "nocheckcertificate": True,
        "useragent": ua,
        "socket_timeout": 30,
        "retries": RETRY_COUNT,
        "fragment_retries": RETRY_COUNT,
        "extractor_retries": RETRY_COUNT,
        "retry_sleep_functions": {
            "http": lambda n: RETRY_DELAY,
            "fragment": lambda n: RETRY_DELAY,
        },
        "ignoreerrors": False,
        "extract_flat": False,
    }

    try:
        fp = _run_ytdlp_download(url, opts)
        if fp and os.path.getsize(fp) > 2048:
            return fp
        return None
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"{platform} download error: {e}")
        raise
