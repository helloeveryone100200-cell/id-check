"""
bot.py — Telegram Bot with MongoDB integration and duplicate ID-check.

Run:
    python bot.py

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

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from telegram import Update, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Conversation state
SETEMOJI_WAIT = 0

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

RE_USERNAME = re.compile(r".*username\s*[-:]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
RE_PHONE    = re.compile(r".*(?:client|phone)\s*number\s*[-:]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
RE_WHATSAPP = re.compile(r".*whatsapp\s*number\s*[-:]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
RE_ID       = re.compile(r".*\bid\b\s*[-:]\s*(.+)$", re.IGNORECASE | re.MULTILINE)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_submission(text: str) -> dict | None:
    if not text:
        return None
    m_phone = RE_PHONE.search(text)
    if not m_phone:
        return None
    phone = m_phone.group(1).strip()
    if not phone:
        return None
    m_username = RE_USERNAME.search(text)
    username = m_username.group(1).strip() if m_username else ""
    m_whatsapp = RE_WHATSAPP.search(text)
    whatsapp = m_whatsapp.group(1).strip() if m_whatsapp else None
    id_number: str | None = None
    m_id = RE_ID.search(text)
    if m_id:
        id_value = m_id.group(1).strip()
        if id_value:
            id_number = id_value
    return {
        "username": username.lower(),
        "phone_number": phone,
        "whatsapp_number": whatsapp,
        "id_number": id_number,
    }


def user_display(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.full_name or str(user.id)


def user_html_mention(user) -> str:
    name = user.full_name or str(user.id)
    return f'<a href="tg://user?id={user.id}">{name}</a>'


FIELD_NAMES = {
    "phone_number":    "Phone number",
    "whatsapp_number": "WhatsApp number",
    "id_number":       "ID",
    "username":        "Username",
}


def _emoji_tag(cfg: dict) -> str:
    """Return a <tg-emoji> tag if an emoji_id is set, otherwise return the plain fallback."""
    emoji_id = cfg.get("emoji_id")
    fallback = cfg.get("fallback", "•")
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback


def format_duplicate_reply(
    template: str,
    user_mention: str,
    original_user: str,
    matches: list,
    field_emojis: dict | None = None,
) -> str:
    if field_emojis is None:
        field_emojis = {}

    lines = []
    for m in matches:
        field = m["field"]
        name = FIELD_NAMES.get(field, field)
        cfg = field_emojis.get(field, {"fallback": "•", "emoji_id": None})
        emoji = _emoji_tag(cfg)
        lines.append(f"  {emoji} {name}: <code>{m['value']}</code>")

    matched_fields = "\n".join(lines)
    first_cfg = field_emojis.get(matches[0]["field"], {}) if matches else {}
    first_field = f"{_emoji_tag(first_cfg)} {FIELD_NAMES.get(matches[0]['field'], '')}".strip() if matches else ""

    return (
        template
        .replace("{user_mention}", user_mention)
        .replace("{original_user}", original_user)
        .replace("{matched_fields}", matched_fields)
        .replace("{matched_field}", first_field)
    )


# ---------------------------------------------------------------------------
# /start
# ---------------------------------------------------------------------------

OWNER_PANEL_CALLBACK = "owner_panel"

OWNER_PANEL_TEXT = (
    "⚙️ <b>Owner Panel — Commands</b>\n\n"
    "━━━━━━━━━━━━━━━━━━\n"
    "📝 <b>Messages</b>\n"
    "  /setmsg dup <code>&lt;msg&gt;</code> — duplicate warning set\n"
    "  /setmsg welcome <code>&lt;msg&gt;</code> — /start welcome set\n"
    "  /getmsg — messages ကြည့်\n"
    "  /resetmsg dup|welcome — default ပြန်ပြင်\n\n"
    "🎭 <b>Animated Emoji</b>\n"
    "  /setemoji <code>&lt;field&gt;</code> — emoji set\n"
    "    fields: <code>phone</code> | <code>whatsapp</code> | <code>id</code> | <code>username</code>\n"
    "  /getemoji — emoji settings ကြည့်\n"
    "  /resetemoji <code>&lt;field&gt;</code> — default ပြန်ပြင်\n\n"
    "🔘 <b>Start Buttons</b>\n"
    "  /addbutton <code>&lt;label&gt; | &lt;url&gt;</code> — button ထည့်\n"
    "  /listbuttons — buttons ကြည့်\n"
    "  /removebutton <code>&lt;n&gt;</code> — button ဖျက်\n"
    "  /resetbuttons — buttons အားလုံး ဖျက်\n"
    "━━━━━━━━━━━━━━━━━━"
)


def _build_start_keyboard(
    bot_username: str,
    custom_buttons: list,
    is_owner: bool = False,
) -> InlineKeyboardMarkup:
    """Build the /start inline keyboard.

    Always shows 3 default buttons + any custom buttons.
    Adds a hidden ⚙️ Owner Panel button at the bottom for admins only.
    """
    add_url   = f"https://t.me/{bot_username}?startgroup=start"
    share_url = f"https://t.me/share/url?url=https://t.me/{bot_username}"
    author_url = "https://t.me/yasha_sangi"

    keyboard = [
        [
            InlineKeyboardButton("➕ Add me to group", url=add_url),
            InlineKeyboardButton("📤 Share bot",       url=share_url),
        ],
        [
            InlineKeyboardButton("👤 Author", url=author_url),
        ],
    ]

    # Append custom buttons — one per row
    for btn in custom_buttons:
        keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])

    # Owner-only row — invisible to regular users
    if is_owner:
        keyboard.append([
            InlineKeyboardButton("⚙️ Owner Panel", callback_data=OWNER_PANEL_CALLBACK),
        ])

    return InlineKeyboardMarkup(keyboard)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    name = user.full_name or user.first_name or "there"
    db = db_module.get_db()
    if db is not None:
        template = db_module.get_start_msg(db)
        text = template.replace("{name}", name)
        custom_buttons = db_module.get_start_buttons(db)
    else:
        text = (
            f"👋 Welcome, <b>{name}</b>!\n\n"
            "🤖 <b>What I do:</b>\n"
            "I monitor group messages and automatically flag duplicate submissions "
            "(phone numbers, WhatsApp numbers, IDs, usernames).\n\n"
            "📋 <b>Group submission format:</b>\n"
            "<code>Phone number - 09xxxxxxxxx</code>\n"
            "<code>Whatsapp number - 09xxxxxxxxx</code> (optional)\n"
            "<code>ID - A123456</code> (optional)\n"
            "<code>Username - @yourname</code> (optional)"
        )
        custom_buttons = []

    bot_username = context.bot.username or ""
    is_owner = user.id in ADMIN_IDS
    keyboard = _build_start_keyboard(bot_username, custom_buttons, is_owner=is_owner)
    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Owner Panel callback
# ---------------------------------------------------------------------------

async def handle_owner_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the ⚙️ Owner Panel inline button — only responds to admins."""
    query = update.callback_query
    if not query:
        return
    user = query.from_user
    if not user or user.id not in ADMIN_IDS:
        await query.answer("⛔ Not authorised.", show_alert=True)
        return
    await query.answer()
    await query.message.reply_text(OWNER_PANEL_TEXT, parse_mode=ParseMode.HTML)


# ---------------------------------------------------------------------------
# Admin commands
# ---------------------------------------------------------------------------

async def cmd_getmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return
    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return
    dup = db_module.get_duplicate_msg(db)
    welcome = db_module.get_start_msg(db)
    await update.message.reply_text(
        f"<b>Duplicate message:</b>\n{dup}\n\n<b>Welcome message:</b>\n{welcome}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_setmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    HELP = (
        "Usage:\n"
        "  /setmsg dup &lt;message&gt;     — duplicate warning\n"
        "  /setmsg welcome &lt;message&gt; — /start welcome message\n\n"
        "<b>Duplicate placeholders:</b>\n"
        "  <code>{user_mention}</code> — user who submitted the duplicate\n"
        "  <code>{original_user}</code> — original submitter\n"
        "  <code>{matched_fields}</code> — all duplicate field lines\n"
        "  <code>{matched_field}</code> — first duplicate field name\n\n"
        "<b>Welcome placeholder:</b>\n"
        "  <code>{name}</code> — user's display name"
    )

    plain = update.message.text or ""
    parts = plain.split(None, 2)

    if len(parts) < 3:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    msg_type = parts[1].lower()
    full_html = update.message.text_html or plain
    new_message = re.sub(r"^/\S+\s+\S+\s*", "", full_html, count=1).strip()

    if msg_type not in ("dup", "welcome"):
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
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
        await update.message.reply_text("❌ Failed to update the message.")


async def cmd_setemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 1 — admin sends /setemoji <field>, bot asks for the emoji."""
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return ConversationHandler.END

    HELP = (
        "Usage: <code>/setemoji &lt;field&gt;</code>\n\n"
        "Fields: <code>phone</code> | <code>whatsapp</code> | <code>id</code> | <code>username</code>\n\n"
        "Example: <code>/setemoji phone</code>"
    )

    args = context.args or []
    if not args:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    alias = args[0].lower()
    field = db_module.FIELD_ALIASES.get(alias)
    if not field:
        await update.message.reply_text(
            f"❌ Unknown field <code>{alias}</code>.\n"
            "Use: <code>phone</code> | <code>whatsapp</code> | <code>id</code> | <code>username</code>",
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    context.user_data["setemoji_field"] = field
    context.user_data["setemoji_alias"] = alias
    await update.message.reply_text(
        f"🎭 Send the <b>animated emoji</b> you want to use for "
        f"<b>{FIELD_NAMES.get(field, alias)}</b>.\n\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return SETEMOJI_WAIT


async def setemoji_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 2 — extract custom_emoji entity from the message and save."""
    msg = update.message
    entities = list(msg.entities or []) + list(msg.caption_entities or [])
    text = msg.text or msg.caption or ""

    custom_emoji_id = None
    fallback = None
    for ent in entities:
        if ent.type == "custom_emoji":
            custom_emoji_id = ent.custom_emoji_id
            # Extract the visible character(s) the entity covers
            fallback = msg.parse_entity(ent)
            break

    if not custom_emoji_id:
        await msg.reply_text(
            "❌ No animated emoji found in that message.\n"
            "Please send a <b>custom animated emoji</b> (not a regular emoji).\n\n"
            "/cancel — to cancel",
            parse_mode=ParseMode.HTML,
        )
        return SETEMOJI_WAIT

    field = context.user_data["setemoji_field"]
    alias = context.user_data["setemoji_alias"]

    db = db_module.get_db()
    if db is None:
        await msg.reply_text("❌ Database is unavailable.")
        context.user_data.clear()
        return ConversationHandler.END

    if db_module.set_field_emoji(db, field, custom_emoji_id, fallback):
        preview = f'<tg-emoji emoji-id="{custom_emoji_id}">{fallback}</tg-emoji>'
        await msg.reply_text(
            f"✅ Emoji for <b>{alias}</b> updated!\n\n"
            f"Preview: {preview} {FIELD_NAMES.get(field, field)}",
            parse_mode=ParseMode.HTML,
        )
    else:
        await msg.reply_text("❌ Failed to save. Please try again.")

    context.user_data.clear()
    return ConversationHandler.END


async def setemoji_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Cancelled.")
    return ConversationHandler.END


async def cmd_getemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current animated emoji settings for all fields."""
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return

    emojis = db_module.get_field_emojis(db)
    lines = []
    for alias, field in db_module.FIELD_ALIASES.items():
        cfg = emojis.get(field, {})
        emoji_id = cfg.get("emoji_id")
        fallback = cfg.get("fallback", "•")
        if emoji_id:
            tag = f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
            lines.append(f"• <code>{alias}</code>: {tag}  (id: <code>{emoji_id}</code>)")
        else:
            lines.append(f"• <code>{alias}</code>: {fallback}  <i>(plain, no animation)</i>")

    text = (
        "🎭 <b>Animated emoji settings:</b>\n\n"
        + "\n".join(lines)
        + "\n\n<i>Change: /setemoji &lt;field&gt;</i>\n"
        "<i>Reset: /resetemoji &lt;field&gt;</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_resetemoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset animated emoji for a field back to the plain default."""
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    HELP = "Usage: <code>/resetemoji &lt;field&gt;</code>\nFields: phone | whatsapp | id | username"

    if not context.args:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    alias = context.args[0].lower()
    field = db_module.FIELD_ALIASES.get(alias)
    if not field:
        await update.message.reply_text(
            f"❌ Unknown field <code>{alias}</code>.", parse_mode=ParseMode.HTML
        )
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return

    if db_module.reset_field_emoji(db, field):
        await update.message.reply_text(f"✅ Emoji for <b>{alias}</b> reset to default.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Failed to reset.")


# ---------------------------------------------------------------------------
# /start inline-button management (admin only)
# ---------------------------------------------------------------------------

async def cmd_addbutton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Add a custom inline button to the /start keyboard.

    Usage:  /addbutton Button Label | https://example.com
    The label may contain any Unicode text, including animated emoji pasted directly.
    """
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    HELP = (
        "Usage: <code>/addbutton Label | URL</code>\n\n"
        "Example:\n"
        "<code>/addbutton 🌐 Visit website | https://example.com</code>\n\n"
        "You can paste an animated emoji directly into the label."
    )

    # Use text_html so animated emoji pasted in the label are preserved as-is
    raw_html = update.message.text_html or ""
    # Strip the command prefix (/addbutton ), keep the rest
    body = re.sub(r"^/\S+\s*", "", raw_html, count=1).strip()

    if "|" not in body:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    label_part, _, url_part = body.partition("|")
    label = label_part.strip()
    url   = url_part.strip()

    if not label or not url:
        await update.message.reply_text(HELP, parse_mode=ParseMode.HTML)
        return

    if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
        await update.message.reply_text(
            "❌ URL must start with <code>http://</code>, <code>https://</code>, or <code>tg://</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return

    if db_module.add_start_button(db, label, url):
        buttons = db_module.get_start_buttons(db)
        await update.message.reply_text(
            f"✅ Button added (#{len(buttons)})!\n\n"
            f"Label: {label}\nURL: <code>{url}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("❌ Failed to add button.")


async def cmd_listbuttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List all custom inline buttons (admin only)."""
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return

    buttons = db_module.get_start_buttons(db)
    if not buttons:
        await update.message.reply_text(
            "ℹ️ No custom buttons added yet.\n\n"
            "Default buttons (always shown):\n"
            "  1. ➕ Add me to group\n"
            "  2. 📤 Share bot\n"
            "  3. 👤 Author\n\n"
            "Add one with: <code>/addbutton Label | URL</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    lines = [
        "<b>🔘 Default buttons (always shown):</b>\n"
        "  • ➕ Add me to group\n"
        "  • 📤 Share bot\n"
        "  • 👤 Author\n\n"
        "<b>➕ Custom buttons:</b>"
    ]
    for i, btn in enumerate(buttons, 1):
        lines.append(f"  {i}. {btn['text']} — <code>{btn['url']}</code>")

    lines.append("\n<i>Remove: /removebutton &lt;number&gt;  |  Reset all: /resetbuttons</i>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_removebutton(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove a custom inline button by number (admin only).

    Usage:  /removebutton 2
    """
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: <code>/removebutton &lt;number&gt;</code>\n"
            "See the list with /listbuttons",
            parse_mode=ParseMode.HTML,
        )
        return

    index = int(context.args[0])
    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return

    if db_module.remove_start_button(db, index):
        remaining = db_module.get_start_buttons(db)
        await update.message.reply_text(
            f"✅ Button #{index} removed. {len(remaining)} custom button(s) remaining.",
        )
    else:
        buttons = db_module.get_start_buttons(db)
        await update.message.reply_text(
            f"❌ Invalid number. You have {len(buttons)} custom button(s). "
            "Use /listbuttons to see them."
        )


async def cmd_resetbuttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove ALL custom inline buttons (admin only)."""
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return

    if db_module.reset_start_buttons(db):
        await update.message.reply_text(
            "✅ All custom buttons removed.\n"
            "Only the 3 default buttons (Add to group, Share, Author) will now show."
        )
    else:
        await update.message.reply_text("❌ Failed to reset buttons.")


async def cmd_resetmsg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Not authorised.")
        return

    HELP = (
        "Usage:\n"
        "  /resetmsg dup     — reset duplicate warning to default\n"
        "  /resetmsg welcome — reset /start welcome to default"
    )

    if not context.args or context.args[0].lower() not in ("dup", "welcome"):
        await update.message.reply_text(HELP)
        return

    msg_type = context.args[0].lower()
    key = "duplicate_msg" if msg_type == "dup" else "start_msg"
    label = "Duplicate warning message" if msg_type == "dup" else "Welcome message"

    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database is unavailable.")
        return

    success = db_module.reset_setting(db, key)
    if success:
        await update.message.reply_text(f"✅ <b>{label}</b> reset to default.", parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text("❌ Failed to reset.")


# ---------------------------------------------------------------------------
# Group message handler (ID-check / duplicate detection)
# ---------------------------------------------------------------------------

async def handle_group_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    text = message.text or message.caption or ""
    parsed = parse_submission(text)

    if not parsed:
        return

    db = db_module.get_db()
    if db is None:
        logger.warning("Database unavailable; skipping duplicate check.")
        return

    result = db_module.check_duplicate(
        db,
        phone_number=parsed["phone_number"],
        whatsapp_number=parsed["whatsapp_number"],
        id_number=parsed["id_number"],
        username=parsed["username"],
    )

    sender = message.from_user
    sender_mention = user_html_mention(sender)
    sender_display = user_display(sender)

    if result["found"]:
        original_doc = result["doc"]
        original_user = original_doc.get("telegram_username") or str(original_doc.get("telegram_id", "unknown"))
        matches = result["matches"]
        template = db_module.get_duplicate_msg(db)
        field_emojis = db_module.get_field_emojis(db)
        reply_text = format_duplicate_reply(
            template,
            user_mention=sender_mention,
            original_user=original_user,
            matches=matches,
            field_emojis=field_emojis,
        )
        await message.reply_text(reply_text, parse_mode=ParseMode.HTML, reply_markup=ReplyKeyboardRemove())
        logger.info(
            "Duplicate detected for %s (fields: %s)",
            sender_display,
            ", ".join(m["field"] for m in matches),
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
            logger.info("Saved submission from %s", sender_display)
        else:
            logger.error("Failed to save submission from %s", sender_display)


# ---------------------------------------------------------------------------
# Flask keep-alive
# ---------------------------------------------------------------------------

flask_app = Flask(__name__)


@flask_app.route("/")
def health():
    return "OK", 200


def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

async def run_bot():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", cmd_start))

    # Admin commands (private chat only)
    application.add_handler(CommandHandler("setmsg",       cmd_setmsg,       filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("getmsg",       cmd_getmsg,       filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("resetmsg",     cmd_resetmsg,     filters=filters.ChatType.PRIVATE))
    # /start button management
    application.add_handler(CommandHandler("addbutton",    cmd_addbutton,    filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("listbuttons",  cmd_listbuttons,  filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("removebutton", cmd_removebutton, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("resetbuttons", cmd_resetbuttons, filters=filters.ChatType.PRIVATE))
    # Owner Panel callback
    application.add_handler(CallbackQueryHandler(handle_owner_panel, pattern=f"^{OWNER_PANEL_CALLBACK}$"))
    # /setemoji — 2-step conversation (private only)
    setemoji_conv = ConversationHandler(
        entry_points=[CommandHandler("setemoji", cmd_setemoji, filters=filters.ChatType.PRIVATE)],
        states={
            SETEMOJI_WAIT: [
                MessageHandler(filters.Regex("^/cancel$"), setemoji_cancel),
                MessageHandler(filters.ALL & ~filters.COMMAND, setemoji_receive),
            ],
        },
        fallbacks=[CommandHandler("cancel", setemoji_cancel)],
        allow_reentry=True,
    )
    application.add_handler(setemoji_conv)
    application.add_handler(CommandHandler("getemoji",   cmd_getemoji,   filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("resetemoji", cmd_resetemoji, filters=filters.ChatType.PRIVATE))

    # Group ID-check listener
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
        await asyncio.Event().wait()
        await application.updater.stop()
        await application.stop()


def main() -> None:
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Keep-alive server started on port %d", PORT)

    if not BOT_TOKEN:
        logger.error("BOT_TOKEN is missing. Set it and restart.")
        flask_thread.join()
        return

    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
