"""
Gemini-DownOG Download Engine
Professional yt-dlp integration for all platforms.
Single authoritative module for all media downloading.
"""
import os
import re
import time
import json
import uuid
import logging
import threading
import subprocess
from typing import Optional, Callable
from urllib.parse import urlparse

logger = logging.getLogger("tg_downloader.dl")

# ─── Paths ───
DOWNLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # Telegram limit

# ─── Config ───
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0
PROGRESS_INTERVAL = 3.0  # seconds between progress updates

# ─── URL Regex Patterns ───
YOUTUBE_REGEX = re.compile(
    r'(?:https?://)?(?:www\.|m\.|music\.)?'
    r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/shorts/|'
    r'youtube\.com/embed/|youtube\.com/v/|youtube\.com/live/|'
    r'youtube\.com/playlist\?list=|music\.youtube\.com/watch\?v=)'
    r'([\w-]{11})'
)
INSTAGRAM_REGEX = re.compile(
    r'(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv|stories|share)/([\w-]+)'
)
TIKTOK_REGEX = re.compile(
    r'(?:https?://)?(?:www\.)?(?:vm\.|vt\.)?tiktok\.com/'
    r'(@[\w.-]+/video/\d+|[\w-]+|t/[\w-]+)'
)

# ─── Platform definitions: (name, regex, domain_strings, ytdl_extractor) ───
PLATFORMS = [
    ("YouTube",    YOUTUBE_REGEX,    ["youtube.com","youtu.be"],        "Youtube"),
    ("Instagram",  INSTAGRAM_REGEX,  ["instagram.com"],                  "Instagram"),
    ("TikTok",     TIKTOK_REGEX,     ["tiktok.com","vm.tiktok.com"],    "TikTok"),
    ("Facebook",   None,             ["facebook.com","fb.com","fb.watch"], "Facebook"),
    ("Twitter",    None,             ["twitter.com","x.com","t.co"],    "Twitter"),
    ("Pinterest",  None,             ["pinterest.com","pin.it"],        "Pinterest"),
    ("Reddit",     None,             ["reddit.com","redd.it"],          "Reddit"),
    ("Vimeo",      None,             ["vimeo.com"],                     "Vimeo"),
]

# ─── Quality presets for yt-dlp format strings ───
QUALITY_PRESETS = {
    "best":   "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best",
    "1080p":  "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
    "720p":   "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
    "480p":   "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
    "audio":  "bestaudio/best",
}

# ─── User-Agents per platform ───
USER_AGENTS = {
    "default":   "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "TikTok":    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120.0.6099.144 Mobile Safari/537.36",
    "Instagram": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/120.0.6099.144 Mobile Safari/537.36",
}


# ═══════════════════════════════════════════════
#  Utility Functions
# ═══════════════════════════════════════════════

def detect_platform(url: str) -> Optional[str]:
    """Detect media platform from URL. Returns platform name or None."""
    # Extract domain from URL for proper matching
    parsed = urlparse(url)
    domain = parsed.netloc.lower() or parsed.path.lower()
    # Clean up common prefixes
    domain = domain.removeprefix("www.").removeprefix("m.").removeprefix("music.")
    domain = domain.removeprefix("vm.").removeprefix("vt.")

    for name, pattern, domains, _ in PLATFORMS:
        if pattern and pattern.search(url):
            return name
        for d in domains:
            # Remove www/m prefixes from domain for comparison
            clean_d = d.removeprefix("www.").removeprefix("m.").removeprefix("music.")
            clean_d = clean_d.removeprefix("vm.").removeprefix("vt.")
            if clean_d == domain or domain.endswith("." + clean_d):
                return name
            # Also check full URL for short domains like t.co
            if "." not in clean_d and f"/{d}/" in url:
                return name
    return None


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Sanitize string for use as filename. Handles Unicode."""
    # Remove path separators and special chars
    sane = re.sub(r'[\\/*?:"<>|]', "_", name).strip(" .")
    # Keep only printable chars and spaces
    sane = "".join(c for c in sane if c.isprintable() or c in " -.")
    # Collapse whitespace
    sane = re.sub(r"\s+", " ", sane).strip()
    # Replace spaces with underscores for safety
    sane = sane.replace(" ", "_")
    sane = re.sub(r"_+", "_", sane)
    return sane[:max_len] if sane else "media"


def file_size_mb(path: str) -> float:
    try:
        return os.path.getsize(path) / (1048576)
    except Exception:
        return 0.0


def cleanup_temp(prefix: str = ""):
    """Remove old files from download directory."""
    if not os.path.exists(DOWNLOAD_DIR):
        return
    now = time.time()
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        try:
            if os.path.isfile(path):
                if prefix and f.startswith(prefix):
                    os.unlink(path)
                elif now - os.path.getmtime(path) > 3600:
                    os.unlink(path)
        except Exception:
            pass


def schedule_cleanup():
    """Background thread for periodic temp cleanup."""
    def worker():
        while True:
            time.sleep(1800)
            try:
                cleanup_temp()
            except Exception:
                pass
    t = threading.Thread(target=worker, daemon=True, name="DlCleanup")
    t.start()


def probe_video(file_path: str) -> dict:
    """Use ffprobe to get {width, height, duration}."""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", "v:0",
             "-show_entries", "stream=width,height:format=duration",
             "-of", "json", file_path],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return {}
        data = json.loads(r.stdout or "{}")
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
        logger.debug(f"ffprobe: {e}")
        return {}


def generate_thumbnail(video_path: str) -> Optional[str]:
    """Extract a JPG thumbnail frame at ~1 second."""
    thumb = video_path.rsplit(".", 1)[0] + "_thumb.jpg"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-ss", "1",
             "-i", video_path, "-frames:v", "1",
             "-vf", "scale='min(320,iw)':-2", "-q:v", "5", thumb],
            capture_output=True, timeout=15,
        )
        if os.path.exists(thumb) and os.path.getsize(thumb) > 0:
            return thumb
    except Exception:
        pass
    return None


def get_ua(platform: str = "") -> str:
    """Get platform-specific User-Agent."""
    return USER_AGENTS.get(platform, USER_AGENTS["default"])


# ═══════════════════════════════════════════════
#  yt-dlp Download Core
# ═══════════════════════════════════════════════

def _make_opts(
    platform: str,
    quality: str = "best",
    is_audio: bool = False,
    outtmpl: str = "",
    progress_cb: Optional[Callable] = None,
) -> dict:
    """Build yt-dlp options dict."""
    fmt = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["best"])
    if is_audio:
        fmt = QUALITY_PRESETS["audio"]

    opts = {
        "format": fmt,
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "useragent": get_ua(platform),
        "retries": MAX_RETRIES,
        "fragment_retries": MAX_RETRIES,
        "extractor_retries": MAX_RETRIES,
        "retry_sleep_functions": {
            "http": lambda n: RETRY_BASE_DELAY * n,
            "fragment": lambda n: RETRY_BASE_DELAY * n,
        },
        "socket_timeout": 30,
        "ignoreerrors": False,
    }

    if is_audio:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
        # Embed metadata (thumbnail, title, artist)
        opts["postprocessors"].append({
            "key": "FFmpegMetadata",
            "add_metadata": True,
        })
        opts["writethumbnail"] = True
        opts["embedthumbnail"] = True
    else:
        opts["merge_output_format"] = "mp4"

    # Platform-specific tweaks
    if platform == "TikTok":
        # Prefer watermark-free when available
        opts["format"] = (
            "bestvideo[ext=mp4][protocol!*=m3u8]+bestaudio[ext=m4a]/"
            "best[ext=mp4][protocol!*=m3u8]/best"
        )
        opts["extract_flat"] = False
    elif platform == "Instagram":
        opts["extract_flat"] = False

    if progress_cb:
        last = [0.0]
        def _hook(d):
            if d["status"] == "downloading" and d.get("total_bytes"):
                now = time.time()
                if now - last[0] >= PROGRESS_INTERVAL:
                    last[0] = now
                    pct = (d.get("downloaded_bytes", 0) / d["total_bytes"]) * 100
                    speed = d.get("speed", 0) or 0
                    speed_str = f"{speed/1048576:.1f}MB/s" if speed else "..."
                    try:
                        progress_cb(pct, speed_str)
                    except Exception:
                        pass
        opts["progress_hooks"] = [_hook]

    return opts


def _extract_info(url: str, platform: str = "") -> Optional[dict]:
    """Get media metadata without downloading."""
    import yt_dlp
    try:
        opts = {
            "quiet": True, "no_warnings": True, "simulate": True,
            "skip_download": True, "useragent": get_ua(platform),
            "extract_flat": False, "verbose": False,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception as e:
        logger.debug(f"Extract info failed: {e}")
        return None


def _download_file(url: str, opts: dict) -> Optional[str]:
    """Execute yt-dlp download with retry. Returns file path."""
    import yt_dlp

    # Predict output path
    outtmpl = opts.get("outtmpl", "")
    base = os.path.basename(outtmpl.replace("%(ext)s", ""))

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            # Find file matching our pattern
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(base) and os.path.getsize(os.path.join(DOWNLOAD_DIR, f)) > 2048:
                    return os.path.join(DOWNLOAD_DIR, f)
            # Also try without extension suffix
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(base.rstrip(".")) and os.path.getsize(os.path.join(DOWNLOAD_DIR, f)) > 2048:
                    return os.path.join(DOWNLOAD_DIR, f)
            return None

        except yt_dlp.utils.DownloadError as e:
            last_err = e
            err = str(e).lower()
            if "unsupported" in err or "private" in err or "copyright" in err:
                raise  # Don't retry these
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(f"Retry {attempt}/{MAX_RETRIES} in {delay:.0f}s: {e}")
                time.sleep(delay)
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY * attempt)

    if last_err:
        raise last_err
    return None


# ═══════════════════════════════════════════════
#  Public Download API
# ═══════════════════════════════════════════════

def download_media(
    url: str,
    platform: str = "",
    quality: str = "best",
    is_audio: bool = False,
    unique_id: str = "",
    progress_cb: Optional[Callable] = None,
) -> Optional[str]:
    """
    Universal download function.
    
    Args:
        url: Media URL
        platform: Platform name (auto-detected if empty)
        quality: 'best', '1080p', '720p', '480p', 'audio'
        is_audio: Force audio-only (MP3)
        unique_id: Prefix for temp file tracking
        progress_cb: Callback(pct, speed_str) for progress updates
        
    Returns:
        Path to downloaded file, or None on failure
    """
    import yt_dlp
    
    if not platform:
        platform = detect_platform(url) or "Web"

    # 1. Get media info for title
    info = _extract_info(url, platform)
    title = platform
    if info:
        title = info.get("title") or info.get("id") or platform
    safe_title = sanitize_filename(str(title))

    prefix = unique_id or uuid.uuid4().hex[:8]
    ext = "mp3" if is_audio else "mp4"
    base_name = f"{prefix}_{safe_title}"
    outtmpl = os.path.join(DOWNLOAD_DIR, f"{base_name}.%(ext)s")

    # 2. Build options
    opts = _make_opts(
        platform=platform,
        quality=quality,
        is_audio=is_audio,
        outtmpl=outtmpl,
        progress_cb=progress_cb,
    )

    # 3. Handle audio metadata for YouTube
    if is_audio and info and platform == "YouTube":
        # yt-dlp handles thumbnail embedding via FFmpegMetadata
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {
                "key": "FFmpegMetadata",
                "add_metadata": True,
            },
        ]
        opts["writethumbnail"] = True
        opts["embedthumbnail"] = True
        # Add thumbnail as cover art
        opts["postprocessors"].append({
            "key": "EmbedThumbnail",
        })

    # Handle Instagram carousel / multiple items
    if platform == "Instagram" and info:
        entries = info.get("entries")
        if entries and len(entries) > 1:
            # Remove merge_output_format for carousel (each item separate)
            opts.pop("merge_output_format", None)
            opts["format"] = "best[ext=mp4]/best"

    try:
        fp = _download_file(url, opts)
        if fp and os.path.isfile(fp):
            size = os.path.getsize(fp)
            if is_audio or size <= MAX_FILE_SIZE_BYTES:
                return fp
            # Too large
            os.unlink(fp)
            logger.warning(f"File too large ({size/1048576:.1f}MB): {fp}")
        return None
    except yt_dlp.utils.DownloadError as e:
        logger.error(f"Download failed [{platform}]: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected download error [{platform}]: {e}")
        raise


def download_youtube(
    url: str,
    fmt: str = "mp4",
    unique_id: str = "",
    progress_cb: Optional[Callable] = None,
) -> Optional[str]:
    """
    Download YouTube video (MP4) or audio (MP3).
    Kept for backwards compatibility with bot.py.
    """
    is_audio = fmt == "mp3"
    quality = "audio" if is_audio else "best"
    return download_media(
        url=url,
        platform="YouTube",
        quality=quality,
        is_audio=is_audio,
        unique_id=unique_id,
        progress_cb=progress_cb,
    )


def download_social_media(
    url: str,
    platform: str,
    unique_id: str = "",
    progress_cb: Optional[Callable] = None,
) -> Optional[str]:
    """
    Download from social media platforms.
    Kept for backwards compatibility with bot.py.
    """
    return download_media(
        url=url,
        platform=platform,
        quality="best",
        is_audio=False,
        unique_id=unique_id,
        progress_cb=progress_cb,
    )


def get_available_qualities(url: str) -> list:
    """Get list of available video qualities for a URL."""
    info = _extract_info(url)
    if not info:
        return ["best"]

    heights = set()
    formats = info.get("formats", [])
    for f in formats:
        h = f.get("height")
        if h and isinstance(h, int) and h > 0:
            heights.add(h)

    qualities = []
    if heights:
        for target in [2160, 1440, 1080, 720, 480, 360]:
            if target in heights:
                label = f"{target}p"
                if target == 2160:
                    label = "4K"
                elif target == 1440:
                    label = "2K"
                qualities.append(label)
    if not qualities:
        qualities.append("best")

    return qualities
