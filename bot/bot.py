"""
main.py — Telegram Bot with MongoDB integration and Flask keep-alive server.

Run:
    python main.py

Environment variables:
    BOT_TOKEN   — Telegram Bot API token
    ADMIN_IDS   — Comma-separated Telegram user IDs with admin access
    MONGO_URI   — MongoDB connection string
    PORT        — Port for the keep-alive web server (default: 8080)
"""

import asyncio
import logging
import os
import re
import threading

from flask import Flask

# Load .env for local development (no-op if python-dotenv is absent or .env missing)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database as db_module

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
PORT: int = int(os.getenv("PORT", "8080"))

_raw_admin_ids = os.getenv("ADMIN_IDS", "")
ADMIN_IDS: set[int] = set()
for part in _raw_admin_ids.split(","):
    part = part.strip()
    if part.isdigit():
        ADMIN_IDS.add(int(part))

if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is not set. The bot will not start.")

# ---------------------------------------------------------------------------
# Regex patterns for form parsing
# ---------------------------------------------------------------------------

RE_USERNAME = re.compile(
    r"^username\s*-\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
RE_PHONE = re.compile(
    r"^phone\s*number\s*-\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
RE_WHATSAPP = re.compile(
    r"^whatsapp\s*number\s*-\s*(.+)$", re.IGNORECASE | re.MULTILINE
)
RE_ID = re.compile(
    r"^id\s*-\s*(.*)$", re.IGNORECASE | re.MULTILINE
)


def parse_submission(text: str) -> dict | None:
    """
    Extract submission fields from a message.
    Returns a dict or None if the message does not match the required form.
    """
    if not text:
        return None

    m_username = RE_USERNAME.search(text)
    m_phone = RE_PHONE.search(text)
    m_whatsapp = RE_WHATSAPP.search(text)

    # All three required fields must be present and non-empty
    if not (m_username and m_phone and m_whatsapp):
        return None

    username = m_username.group(1).strip()
    phone = m_phone.group(1).strip()
    whatsapp = m_whatsapp.group(1).strip()

    if not username or not phone or not whatsapp:
        return None

    # Optional ID field — silently ignore if present but empty
    id_number: str | None = None
    m_id = RE_ID.search(text)
    if m_id:
        id_value = m_id.group(1).strip()
        if not id_value:
            # ID tag exists but value is blank — ignore silently
            pass
        else:
            id_number = id_value

    return {
        "username": username.lower(),
        "phone_number": phone,
        "whatsapp_number": whatsapp,
        "id_number": id_number,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def user_display(user) -> str:
    """Return a display name (plain text) for logging and storage."""
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def user_html_mention(user) -> str:
    """
    Return an HTML mention that Telegram renders as a clickable tag.
    Works for users with or without a public username.
    """
    name = user.full_name or user.username or str(user.id)
    return f'<a href="tg://user?id={user.id}">{name}</a>'


def format_duplicate_reply(
    template: str,
    *,
    user_mention: str,
    original_user: str,
    matched_field: str,
) -> str:
    """Substitute placeholders in the duplicate warning template."""
    return (
        template
        .replace("{user_mention}", user_mention)
        .replace("{original_user}", original_user)
        .replace("{matched_field}", matched_field)
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — Welcome message (customisable via /setmsg welcome)."""
    user = update.effective_user
    name = user.full_name or user.username or "there"

    db = db_module.get_db()
    if db is None:
        template = db_module.DEFAULT_START_MSG
    else:
        template = db_module.get_start_msg(db)

    await update.message.reply_text(
        template.replace("{name}", name),
        parse_mode="HTML",
    )


async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text messages and photo captions in groups/supergroups."""
    message = update.message
    if not message:
        return

    # Accept both plain text and photo captions
    text = message.text or message.caption or ""
    text = text.strip()

    parsed = parse_submission(text)
    if parsed is None:
        return  # Silently ignore messages that don't match the form

    db = db_module.get_db()
    if db is None:
        logger.error("Database unavailable; skipping submission from %s", message.from_user.id)
        return

    # Duplicate check
    result = db_module.check_duplicate(
        db,
        username=parsed["username"],
        phone_number=parsed["phone_number"],
        whatsapp_number=parsed["whatsapp_number"],
        id_number=parsed["id_number"],
    )

    sender = message.from_user
    sender_mention = user_html_mention(sender)   # clickable HTML tag in replies
    sender_display = user_display(sender)         # plain text for logs/storage

    if result["found"]:
        original_doc = result["doc"]
        original_user = original_doc.get("telegram_username") or str(original_doc.get("telegram_id", "unknown"))
        matched_field = result["field"].replace("_", " ").title()

        template = db_module.get_duplicate_msg(db)
        reply_text = format_duplicate_reply(
            template,
            user_mention=sender_mention,
            original_user=original_user,
            matched_field=matched_field,
        )

        await message.reply_text(reply_text, parse_mode=ParseMode.HTML)
        logger.info(
            "Duplicate detected for %s (field: %s, original submitter: %s)",
            sender_mention,
            matched_field,
            original_user,
        )
    else:
        saved = db_module.save_submission(
            db,
            telegram_id=sender.id,
            telegram_username=user_display(sender),
            username=parsed["username"],
            phone_number=parsed["phone_number"],
            whatsapp_number=parsed["whatsapp_number"],
            id_number=parsed["id_number"],
        )
        if saved:
            logger.info("Saved submission from %s", sender_mention)
        else:
            logger.error("Failed to save submission from %s", sender_mention)


async def cmd_setmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/setmsg — Admin command to set the duplicate or welcome message.

    Usage:
        /setmsg dup <message>      — set the duplicate warning message
        /setmsg welcome <message>  — set the /start welcome message
    """
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorised to use this command.")
        return

    HELP = (
        "Usage:\n"
        "  /setmsg dup &lt;message&gt;     — duplicate warning\n"
        "  /setmsg welcome &lt;message&gt; — /start welcome message\n\n"
        "<b>Duplicate placeholders:</b>\n"
        "  <code>{user_mention}</code> — user who submitted the duplicate\n"
        "  <code>{original_user}</code> — original submitter\n"
        "  <code>{matched_field}</code> — duplicate field name\n\n"
        "<b>Welcome placeholder:</b>\n"
        "  <code>{name}</code> — user's display name\n\n"
        "HTML formatting and Telegram Premium Animated Emoji tags are supported."
    )

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    msg_type = context.args[0].lower()
    new_message = " ".join(context.args[1:])

    if msg_type not in ("dup", "welcome"):
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable. Please try again later.")
        return

    if msg_type == "dup":
        success = db_module.set_duplicate_msg(db, new_message)
        label = "Duplicate warning message"
    else:
        success = db_module.set_start_msg(db, new_message)
        label = "Welcome message"

    if success:
        await update.message.reply_text(
            f"✅ <b>{label}</b> updated!\n\nPreview:\n{new_message}",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("❌ Failed to update the message. Check the logs.")


async def cmd_getmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/getmsg — Admin command to view both custom messages."""
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ You are not authorised to use this command.")
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable. Please try again later.")
        return

    dup_msg = db_module.get_duplicate_msg(db)
    start_msg = db_module.get_start_msg(db)

    await update.message.reply_text(
        f"📋 <b>Duplicate warning message:</b>\n{dup_msg}\n\n"
        f"👋 <b>Welcome message (/start):</b>\n{start_msg}",
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# Flask keep-alive server
# ---------------------------------------------------------------------------

flask_app = Flask(__name__)


@flask_app.route("/")
def health():
    return "Bot is alive!", 200


def run_flask():
    """Run the Flask server in a background thread."""
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def run_bot() -> None:
    """Build and run the bot using the async context manager pattern."""
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # /start — works everywhere
    application.add_handler(CommandHandler("start", cmd_start))

    # Admin commands (private chat only)
    application.add_handler(
        CommandHandler("setmsg", cmd_setmsg, filters=filters.ChatType.PRIVATE)
    )
    application.add_handler(
        CommandHandler("getmsg", cmd_getmsg, filters=filters.ChatType.PRIVATE)
    )

    # Group message listener (text + photo captions)
    application.add_handler(
        MessageHandler(
            (filters.TEXT | filters.PHOTO) & filters.ChatType.GROUPS,
            handle_group_message,
        )
    )

    logger.info("Bot is polling for updates…")
    async with application:
        await application.start()
        await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        # Block forever until a signal arrives (SIGINT / SIGTERM)
        await asyncio.Event().wait()
        await application.updater.stop()
        await application.stop()


def main() -> None:
    # Start keep-alive web server in a daemon thread so Render can bind the port
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Keep-alive server started on port %d", PORT)

    if not BOT_TOKEN:
        logger.error(
            "BOT_TOKEN is missing. Set it via the environment variable and restart."
        )
        # Keep Flask alive so Render/UptimeRobot can still reach /
        flask_thread.join()
        return

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
