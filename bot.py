"""
YouTube → MP3 Telegram Bot  (Webhook edition)
----------------------------------------------
• Uses webhook mode  → works on free hosts (Render, Railway, Koyeb, Fly.io …)
• No ffmpeg needed   → downloads native m4a/webm audio, sends as audio file
• Falls back to polling if WEBHOOK_URL is not set (handy for local dev)

Required env vars:
    BOT_TOKEN       your Telegram bot token (from @BotFather)
    WEBHOOK_URL     public HTTPS URL of this service, e.g. https://mybot.onrender.com
                    (leave unset to use polling locally)

Optional env vars:
    PORT            port to listen on (default 8000)
    MAX_DURATION    max video duration in minutes (default 60)
    MAX_FILE_MB     max output file size in MB (default 50)
"""

import os
import re
import logging
import tempfile
import asyncio
from pathlib import Path

from fastapi import FastAPI, Request, Response
from telegram import Update, constants
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import yt_dlp
import uvicorn

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config from env ────────────────────────────────────────────────────────────
def _load_env():
    """Load .env file if present (no python-dotenv dependency needed)."""
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

BOT_TOKEN       = os.environ.get("BOT_TOKEN", "")
WEBHOOK_URL     = os.environ.get("WEBHOOK_URL", "").rstrip("/")
PORT            = int(os.environ.get("PORT", 8000))
MAX_DURATION    = int(os.environ.get("MAX_DURATION", 60))   # minutes
MAX_FILE_MB     = int(os.environ.get("MAX_FILE_MB", 50))    # MB

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN is not set.\n"
        "Create a .env file with BOT_TOKEN=your_token or export it as an env var."
    )

# ── YouTube helpers ────────────────────────────────────────────────────────────
YOUTUBE_REGEX = re.compile(
    r"(https?://)?(www\.)?"
    r"(youtube\.com/(watch\?v=|shorts/|embed/)|youtu\.be/)"
    r"[\w\-]{11}",
    re.IGNORECASE,
)

def extract_youtube_url(text: str) -> str | None:
    match = YOUTUBE_REGEX.search(text)
    return match.group(0) if match else None

def human_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


COOKIES_FILE = Path(__file__).parent / "cookies.txt"   # optional Netscape cookies file


def _build_ydl_opts(dest_dir: str) -> dict:
    """
    Build yt-dlp options.
    Bypasses YouTube bot-detection via:
      1. cookies.txt  (if present next to bot.py)
      2. android client  (works without cookies on most videos)
    """
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "outtmpl": os.path.join(dest_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Use Android client — bypasses sign-in prompt on most videos
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
    }
    if COOKIES_FILE.exists():
        opts["cookiefile"] = str(COOKIES_FILE)
        logger.info("Using cookies file: %s", COOKIES_FILE)
    return opts


async def download_audio(url: str, dest_dir: str) -> dict:
    """
    Download the best audio track WITHOUT requiring ffmpeg.
    YouTube provides native m4a (AAC) and webm (Opus) streams — no conversion needed.
    Returns: {path, title, duration, filesize, ext}
    Raises ValueError for user-facing errors.
    """
    ydl_opts = _build_ydl_opts(dest_dir)

    loop = asyncio.get_event_loop()

    def _run():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            return ydl.extract_info(url, download=True)

    info = await loop.run_in_executor(None, _run)

    duration = info.get("duration") or 0
    if duration > MAX_DURATION * 60:
        raise ValueError(
            f"❌ Video is too long ({duration // 60} min). "
            f"Max allowed: {MAX_DURATION} min."
        )

    title = info.get("title", "audio")

    # Find the downloaded file (any audio extension)
    audio_files = [
        f for f in Path(dest_dir).iterdir()
        if f.suffix.lower() in (".m4a", ".webm", ".opus", ".ogg", ".mp3", ".aac")
    ]
    if not audio_files:
        raise ValueError("❌ Download failed — no audio file found.")

    audio_path = str(audio_files[0])
    ext = Path(audio_path).suffix.lstrip(".")
    filesize = os.path.getsize(audio_path)

    if filesize > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(
            f"❌ File too large ({human_size(filesize)}). "
            f"Telegram limit is {MAX_FILE_MB} MB."
        )

    return {
        "path": audio_path,
        "title": title,
        "duration": duration,
        "filesize": filesize,
        "ext": ext,
    }


# ── Telegram handlers ──────────────────────────────────────────────────────────
ADMIN_ID = int(os.environ.get("ADMIN_ID", 0))   # your Telegram user ID (optional)


def is_admin(update: Update) -> bool:
    if not ADMIN_ID:
        return True
    return update.effective_user.id == ADMIN_ID


async def cmd_setcookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: reply to a cookies.txt file to install it."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Not authorized.")
        return

    doc = update.message.document
    if not doc or not doc.file_name.endswith(".txt"):
        await update.message.reply_text(
            "📎 Send a `cookies.txt` file and use /setcookies as the *caption*, "
            "or reply to the file with /setcookies.",
            parse_mode=constants.ParseMode.MARKDOWN,
        )
        return

    file = await doc.get_file()
    await file.download_to_drive(str(COOKIES_FILE))
    await update.message.reply_text(
        f"✅ cookies.txt saved ({human_size(COOKIES_FILE.stat().st_size)}). "
        "YouTube requests will now use these cookies."
    )
    logger.info("cookies.txt updated by admin %s", update.effective_user.id)


async def cmd_delcookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin command: delete stored cookies."""
    if not is_admin(update):
        await update.message.reply_text("⛔ Not authorized.")
        return
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
        await update.message.reply_text("🗑 cookies.txt deleted.")
    else:
        await update.message.reply_text("ℹ️ No cookies.txt found.")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *YouTube → Audio Bot*\n\n"
        "Send me any YouTube link and I'll reply with the audio file.\n\n"
        "Supported:\n"
        "• `https://youtube.com/watch?v=...`\n"
        "• `https://youtu.be/...`\n"
        "• `https://youtube.com/shorts/...`\n\n"
        f"⚠️ Max length: {MAX_DURATION} min  |  Max size: {MAX_FILE_MB} MB\n\n"
        "ℹ️ Files are sent as .m4a (AAC) — plays in Telegram and all devices.",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    url = extract_youtube_url(text)

    if not url:
        await update.message.reply_text(
            "🤔 No YouTube link found.\nPlease send a valid YouTube URL."
        )
        return

    status = await update.message.reply_text("⏳ Downloading audio…")

    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            result = await download_audio(url, tmpdir)
        except ValueError as exc:
            await status.edit_text(str(exc))
            return
        except Exception as exc:
            logger.exception("Download error for %s", url)
            await status.edit_text(f"❌ Download failed.\n{exc}")
            return

        await status.edit_text("📤 Uploading…")

        try:
            with open(result["path"], "rb") as f:
                await update.message.reply_audio(
                    audio=f,
                    title=result["title"],
                    duration=result["duration"] or None,
                    caption=(
                        f"🎵 *{result['title']}*\n"
                        f"📦 {human_size(result['filesize'])}  •  .{result['ext']}"
                    ),
                    parse_mode=constants.ParseMode.MARKDOWN,
                )
            await status.delete()
        except Exception as exc:
            logger.exception("Upload error for %s", url)
            await status.edit_text(f"❌ Upload failed.\n{exc}")


# ── App setup ──────────────────────────────────────────────────────────────────
def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setcookies", cmd_setcookies))
    app.add_handler(CommandHandler("delcookies", cmd_delcookies))
    # Handle document (cookies.txt) sent with /setcookies caption
    app.add_handler(MessageHandler(filters.Document.TXT, cmd_setcookies))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


# ── Webhook mode (production) ──────────────────────────────────────────────────
def run_webhook():
    """Run with webhook — required for free hosts that need an HTTP server."""
    logger.info("Starting in WEBHOOK mode → %s", WEBHOOK_URL)

    ptb_app = build_application()
    fastapi_app = FastAPI()

    # Health-check endpoint (keeps Render/Railway alive)
    @fastapi_app.get("/")
    @fastapi_app.get("/health")
    async def health():
        return {"status": "ok"}

    # Telegram sends updates here
    @fastapi_app.post(f"/webhook/{BOT_TOKEN}")
    async def telegram_webhook(request: Request):
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        await ptb_app.process_update(update)
        return Response(status_code=200)

    # Register webhook with Telegram on startup
    @fastapi_app.on_event("startup")
    async def on_startup():
        await ptb_app.initialize()
        webhook_endpoint = f"{WEBHOOK_URL}/webhook/{BOT_TOKEN}"
        await ptb_app.bot.set_webhook(webhook_endpoint)
        logger.info("Webhook registered: %s", webhook_endpoint)

    @fastapi_app.on_event("shutdown")
    async def on_shutdown():
        await ptb_app.bot.delete_webhook()
        await ptb_app.shutdown()
        logger.info("Webhook removed.")

    uvicorn.run(fastapi_app, host="0.0.0.0", port=PORT)


# ── Polling mode (local development) ──────────────────────────────────────────
def run_polling():
    logger.info("Starting in POLLING mode (local dev)")
    ptb_app = build_application()
    ptb_app.run_polling(allowed_updates=Update.ALL_TYPES)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if WEBHOOK_URL:
        run_webhook()
    else:
        run_polling()
