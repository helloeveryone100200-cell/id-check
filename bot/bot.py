"""
bot.py — Telegram Bot with MongoDB integration, ID-check, and Sticker/Emoji creation.

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
import random
import re
import string
import threading
from io import BytesIO

from flask import Flask

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from PIL import Image, ImageDraw, ImageFont
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputSticker,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode, StickerFormat, StickerType
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
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
# Conversation states
# ---------------------------------------------------------------------------

(
    EMOJI_TITLE,
    EMOJI_LINK,
    EMOJI_MEDIA,
    STICKER_TITLE,
    STICKER_LINK,
    STICKER_MEDIA,
    CONVERT_WAITING,
    CONVERT_TITLE,
    CONVERT_LINK,
    LOVE_NAME,
    LOVE_STYLE,
    BRAND_TITLE,
    BRAND_LINK,
    BRAND_LOGO,
    BRAND_MEDIA,
) = range(15)

# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["✨ Create Premium emoji"],
        ["📦 Create sticker pack"],
        ["🔰 Create brand logo emoji"],
        ["🔄 Convert stickers to emoji"],
        ["💞 Create love name status"],
        ["📋 List of my packs"],
    ],
    resize_keyboard=True,
)

# ---------------------------------------------------------------------------
# Regex patterns for form parsing (existing feature)
# ---------------------------------------------------------------------------

RE_USERNAME = re.compile(r".*username\s*-\s*(.+)$", re.IGNORECASE | re.MULTILINE)
RE_PHONE = re.compile(r".*(?:client|phone)\s*number\s*-\s*(.+)$", re.IGNORECASE | re.MULTILINE)
RE_WHATSAPP = re.compile(r".*whatsapp\s*number\s*-\s*(.+)$", re.IGNORECASE | re.MULTILINE)
RE_ID = re.compile(r".*\bid\b\s*-\s*(.+)$", re.IGNORECASE | re.MULTILINE)

RE_VALID_LINK = re.compile(r"^[A-Za-z0-9_]{1,48}$")

# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _make_square_webp(data: bytes, size: int) -> BytesIO:
    """Convert raw image bytes to a static square WebP with transparent background."""
    raw = BytesIO(data)
    img = Image.open(raw)
    # Always take the FIRST frame only — prevents animated WebP which causes
    # 'Sticker_video_dimensions' error when Telegram treats it as a video sticker.
    try:
        img.seek(0)
    except (AttributeError, EOFError):
        pass
    img = img.convert("RGBA")
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    offset = ((size - img.width) // 2, (size - img.height) // 2)
    canvas.paste(img, offset, img)
    out = BytesIO()
    # save_all=False ensures a single-frame (non-animated) WebP is written
    canvas.save(out, format="WEBP", save_all=False)
    out.name = "sticker.webp"
    out.seek(0)
    return out


def _make_love_name_image(name: str, style: int = 1) -> BytesIO:
    """Generate a 100x100 custom emoji image with a love-name design."""
    size = 100
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    # Background circle
    palettes = [
        ("#FF6B9D", "#FFB3CC"),   # pink
        ("#C84B31", "#ECDBBA"),   # red-cream
        ("#845EC2", "#D5CAFF"),   # purple
        ("#0089BA", "#C7F2FF"),   # blue
        ("#1A7431", "#B5FFCA"),   # green
    ]
    style_idx = (style - 1) % len(palettes)
    bg_color, text_color = palettes[style_idx]

    draw.ellipse([2, 2, size - 2, size - 2], fill=bg_color)

    # Hearts decoration
    draw.text((8, 4), "♥", fill=text_color, font=None)
    draw.text((78, 4), "♥", fill=text_color, font=None)
    draw.text((8, 80), "♥", fill=text_color, font=None)
    draw.text((78, 80), "♥", fill=text_color, font=None)

    # Name text — truncate if too long
    display = name[:8] if len(name) > 8 else name
    text_x = size // 2
    text_y = size // 2
    draw.text((text_x, text_y), display, fill=text_color, anchor="mm", font=None)

    out = BytesIO()
    canvas.save(out, format="WEBP")
    out.name = "love.webp"
    out.seek(0)
    return out


def _make_brand_logo_emoji(logo_data: bytes | None, brand_name: str) -> BytesIO:
    """Overlay brand name text on a logo image (or plain color) for emoji."""
    size = 100
    if logo_data:
        canvas = _make_square_webp(logo_data, size)
        img = Image.open(canvas).convert("RGBA")
    else:
        img = Image.new("RGBA", (size, size), (34, 34, 34, 255))

    draw = ImageDraw.Draw(img)
    # Semi-transparent banner at the bottom
    draw.rectangle([0, 70, size, size], fill=(0, 0, 0, 140))
    short = brand_name[:8]
    draw.text((size // 2, 85), short, fill="white", anchor="mm", font=None)

    out = BytesIO()
    img.save(out, format="WEBP")
    out.name = "brand.webp"
    out.seek(0)
    return out


def _random_link(length: int = 8) -> str:
    """Generate a random alphanumeric link."""
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


async def _make_input_sticker(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    size: int,
    emoji_list: list[str],
) -> tuple["InputSticker | None", "str | None"]:
    """
    Build an InputSticker from whatever media the user sent.
    Returns (InputSticker, None) on success, or (None, error_text) on failure.

    Processing rules:
    - Static sticker → use file_id directly (already correct WebP format)
    - Animated / video sticker → download and convert first frame to static WebP
    - Photo / image document → download, resize to `size`x`size`, save as static WebP
    """
    msg = update.message

    if msg.sticker:
        sticker = msg.sticker
        if not sticker.is_animated and not sticker.is_video:
            # Already a static .webp — pass file_id straight through
            return (
                InputSticker(
                    sticker=sticker.file_id,
                    emoji_list=emoji_list,
                    format=StickerFormat.STATIC,
                ),
                None,
            )
        # Animated / video: pull bytes and extract the first frame
        try:
            f = await context.bot.get_file(sticker.file_id)
            data = bytes(await f.download_as_bytearray())
            webp_io = _make_square_webp(data, size)
            return (
                InputSticker(sticker=webp_io, emoji_list=emoji_list, format=StickerFormat.STATIC),
                None,
            )
        except Exception as exc:
            logger.error("animated sticker conversion failed: %s", exc)
            return None, "⚠️ Could not convert this animated sticker. Try a static image instead."

    # Photo message
    if msg.photo:
        f = await context.bot.get_file(msg.photo[-1].file_id)
        data = bytes(await f.download_as_bytearray())
        webp_io = _make_square_webp(data, size)
        return InputSticker(sticker=webp_io, emoji_list=emoji_list, format=StickerFormat.STATIC), None

    # Image sent as document
    if msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/"):
        f = await context.bot.get_file(msg.document.file_id)
        data = bytes(await f.download_as_bytearray())
        webp_io = _make_square_webp(data, size)
        return InputSticker(sticker=webp_io, emoji_list=emoji_list, format=StickerFormat.STATIC), None

    return None, "⚠️ Please send a photo, sticker, or image file."


async def _add_sticker_and_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    processing_msg,
    input_sticker: "InputSticker",
    pack_name: str,
    pack_title: str,
    emoji_list: list[str],
    sticker_type: "StickerType",
    link_prefix: str,
    max_count: int,
) -> bool:
    """
    Create the pack (first sticker) or add to an existing one.
    Edits processing_msg with the result.
    Returns True on success, False on failure.
    """
    user = update.effective_user
    stickers_done: list = context.user_data.setdefault("stickers_uploaded", [])
    pack_created: bool = context.user_data.get("pack_created", False)
    link_base: str = context.user_data["pack_link_base"]

    if len(stickers_done) >= max_count:
        await processing_msg.edit_text(
            f"⚠️ Maximum limit reached ({max_count} items). Use /done to finish the pack."
        )
        return False

    try:
        if not pack_created:
            await context.bot.create_new_sticker_set(
                user_id=user.id,
                name=pack_name,
                title=pack_title,
                stickers=[input_sticker],
                sticker_type=sticker_type,
            )
            context.user_data["pack_created"] = True
        else:
            await context.bot.add_sticker_to_set(
                user_id=user.id,
                name=pack_name,
                sticker=input_sticker,
            )

        stickers_done.append(1)
        count = len(stickers_done)
        await processing_msg.edit_text(
            f"✅ Sticker added to the pack <b>{pack_title}</b>\n"
            f"Emoji in pack: <b>{count}</b>\n\n"
            f"Send me the next images/GIFs/stickers to be added to the pack.\n\n"
            f"⏰ <i>It will become available to all Telegram users within an hour. "
            f"(Or re-add the sticker pack to see the changes right away)</i>\n\n"
            f"/done — finish &amp; get pack link",
            parse_mode=ParseMode.HTML,
        )
        return True

    except Exception as exc:
        logger.error("create/add sticker failed: %s", exc)
        err = str(exc)
        if "STICKERSET_INVALID" in err or "name" in err.lower():
            await processing_msg.edit_text(
                "❌ Pack name already taken or invalid.\n"
                "Please /cancel and start over with a different link."
            )
        elif "video_dimensions" in err.lower() or "dimensions" in err.lower():
            await processing_msg.edit_text(
                "❌ Image dimensions not supported.\n"
                "Please send a regular JPEG or PNG photo and try again."
            )
        elif "STICKERSET_NOT_MODIFIED" in err:
            await processing_msg.edit_text(
                "⚠️ That sticker is already in the pack. Send a different image."
            )
        else:
            await processing_msg.edit_text(
                f"❌ Failed: {err}\n\nTry a different image or /cancel."
            )
        return False


# ---------------------------------------------------------------------------
# Existing helpers
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


def format_duplicate_reply(template: str, user_mention: str, original_user: str, matches: list) -> str:
    field_labels = {
        "phone_number": "📞 Phone number",
        "whatsapp_number": "💬 WhatsApp number",
        "id_number": "🪪 ID",
        "username": "👤 Username",
    }
    lines = [
        f"  • {field_labels.get(m['field'], m['field'])}: <code>{m['value']}</code>"
        for m in matches
    ]
    matched_fields = "\n".join(lines)
    first_field = field_labels.get(matches[0]["field"], matches[0]["field"]) if matches else ""
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

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    name = user.full_name or user.first_name or "there"
    db = db_module.get_db()
    if db is not None:
        template = db_module.get_start_msg(db)
        text = template.replace("{name}", name)
    else:
        text = (
            f"👋 Welcome, <b>{name}</b>!\n\n"
            "🤖 <b>What I do:</b>\n"
            "I monitor group messages and automatically flag duplicate submissions.\n\n"
            "🎨 <b>I also create Telegram emoji &amp; sticker packs!</b>\n"
            "Use the menu buttons below to get started."
        )

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


# ---------------------------------------------------------------------------
# ✨ CREATE PREMIUM EMOJI  (custom_emoji type)
# ---------------------------------------------------------------------------

async def emoji_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["pack_type"] = "custom_emoji"
    await update.message.reply_text(
        "✨ <b>Create new premium-emoji pack</b>\n\n"
        "If you want your emojis to be <i>Adaptive</i> send /adaptive now.\n\n"
        "✍️ <b>Enter a title for the new pack:</b>\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return EMOJI_TITLE


async def emoji_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text or len(text) > 64:
        await update.message.reply_text("❌ Title must be 1–64 characters. Try again:")
        return EMOJI_TITLE
    context.user_data["pack_title"] = text
    await update.message.reply_text(
        "✅ Title saved!\n\n"
        "✍️ Now enter a <b>short link</b> for the pack.\n"
        "Only <b>English letters</b>, <b>digits</b> and <b>underscores</b>\n\n"
        "Example: <code>MyCoolEmoji</code>\n"
        "(link will be <code>t.me/addemoji/MyCoolEmoji_by_YourBot</code>)\n\n"
        "/generate — generate random link\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return EMOJI_LINK


async def emoji_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/generate":
        text = _random_link()
        await update.message.reply_text(f"🔀 Generated link: <code>{text}</code>", parse_mode=ParseMode.HTML)
    if not RE_VALID_LINK.match(text):
        await update.message.reply_text(
            "❌ Invalid link. Only English letters, digits, and underscores (max 48).\n"
            "Try again or /generate:"
        )
        return EMOJI_LINK

    bot_username = (await context.bot.get_me()).username
    pack_name = f"{text}_by_{bot_username}"
    context.user_data["pack_name"] = pack_name
    context.user_data["pack_link_base"] = text
    context.user_data["stickers_uploaded"] = []

    await update.message.reply_text(
        "✅ Link saved!\n\n"
        "📎 <b>Now send me photos/stickers/GIFs</b> to add to the pack.\n"
        f"Telegram limits: maximum <b>200 emoji</b> per pack.\n\n"
        "/done — finish and create the pack\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return EMOJI_MEDIA


async def emoji_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive one image/sticker at a time, create pack on first, add_sticker_to_set after."""
    msg = update.message
    processing_msg = await msg.reply_text("⏳ Creating sticker, wait...")

    input_sticker, error = await _make_input_sticker(update, context, 100, ["⭐"])
    if error:
        await processing_msg.edit_text(error)
        return EMOJI_MEDIA

    await _add_sticker_and_reply(
        update, context, processing_msg,
        input_sticker=input_sticker,
        pack_name=context.user_data["pack_name"],
        pack_title=context.user_data["pack_title"],
        emoji_list=["⭐"],
        sticker_type=StickerType.CUSTOM_EMOJI,
        link_prefix="addemoji",
        max_count=200,
    )
    return EMOJI_MEDIA


async def emoji_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    stickers = context.user_data.get("stickers_uploaded", [])
    if not stickers or not context.user_data.get("pack_created"):
        await update.message.reply_text(
            "❌ No images added yet. Send at least one photo/sticker first.\n/cancel to cancel."
        )
        return EMOJI_MEDIA

    user = update.effective_user
    pack_name = context.user_data["pack_name"]
    pack_title = context.user_data["pack_title"]
    link_base = context.user_data.get("pack_link_base", pack_name)
    count = len(stickers)

    db = db_module.get_db()
    if db is not None:
        db_module.save_sticker_pack(
            db, user.id, pack_name, pack_title,
            context.user_data.get("pack_type", "custom_emoji"), count,
        )

    await update.message.reply_text(
        f"🎉 <b>Congratulations!</b> Your premium emoji pack is ready!\n\n"
        f"📦 <b>{pack_title}</b>\n"
        f"🔗 <a href='https://t.me/addemoji/{link_base}'>t.me/addemoji/{link_base}</a>\n"
        f"✨ Emojis: {count}",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# 📦 CREATE STICKER PACK  (regular stickers)
# ---------------------------------------------------------------------------

async def sticker_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["pack_type"] = "regular"
    await update.message.reply_text(
        "📦 <b>Create new sticker pack</b>\n\n"
        "✍️ <b>Enter a title for the new pack:</b>\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return STICKER_TITLE


async def sticker_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text or len(text) > 64:
        await update.message.reply_text("❌ Title must be 1–64 characters. Try again:")
        return STICKER_TITLE
    context.user_data["pack_title"] = text
    await update.message.reply_text(
        "✅ Title saved!\n\n"
        "✍️ Enter a <b>short link</b> for the pack.\n"
        "Only <b>English letters</b>, <b>digits</b> and <b>underscores</b>\n\n"
        "Example: <code>MyCoolStickers</code>\n\n"
        "/generate — generate random link\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return STICKER_LINK


async def sticker_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/generate":
        text = _random_link()
        await update.message.reply_text(f"🔀 Generated link: <code>{text}</code>", parse_mode=ParseMode.HTML)
    if not RE_VALID_LINK.match(text):
        await update.message.reply_text(
            "❌ Invalid link. Only English letters, digits, and underscores (max 48).\n"
            "Try again or /generate:"
        )
        return STICKER_LINK

    bot_username = (await context.bot.get_me()).username
    pack_name = f"{text}_by_{bot_username}"
    context.user_data["pack_name"] = pack_name
    context.user_data["pack_link_base"] = text
    context.user_data["stickers_uploaded"] = []

    await update.message.reply_text(
        "✅ Link saved!\n\n"
        "📎 <b>Now send me photos or images</b> to add to the pack.\n"
        "Telegram limits: maximum <b>120 static stickers</b> per pack.\n\n"
        "/done — finish and create the pack\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return STICKER_MEDIA


async def sticker_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive one image/sticker at a time — create pack on first, add after."""
    msg = update.message
    processing_msg = await msg.reply_text("⏳ Creating sticker, wait...")

    input_sticker, error = await _make_input_sticker(update, context, 512, ["⭐"])
    if error:
        await processing_msg.edit_text(error)
        return STICKER_MEDIA

    await _add_sticker_and_reply(
        update, context, processing_msg,
        input_sticker=input_sticker,
        pack_name=context.user_data["pack_name"],
        pack_title=context.user_data["pack_title"],
        emoji_list=["⭐"],
        sticker_type=StickerType.REGULAR,
        link_prefix="addstickers",
        max_count=120,
    )
    return STICKER_MEDIA


async def sticker_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    stickers = context.user_data.get("stickers_uploaded", [])
    if not stickers or not context.user_data.get("pack_created"):
        await update.message.reply_text(
            "❌ No images added yet. Send at least one photo first.\n/cancel to cancel."
        )
        return STICKER_MEDIA

    user = update.effective_user
    pack_name = context.user_data["pack_name"]
    pack_title = context.user_data["pack_title"]
    link_base = context.user_data.get("pack_link_base", pack_name)
    count = len(stickers)

    db = db_module.get_db()
    if db is not None:
        db_module.save_sticker_pack(db, user.id, pack_name, pack_title, "regular", count)

    await update.message.reply_text(
        f"🎉 <b>Sticker pack created!</b>\n\n"
        f"📦 <b>{pack_title}</b>\n"
        f"🔗 <a href='https://t.me/addstickers/{link_base}'>t.me/addstickers/{link_base}</a>\n"
        f"🖼 Stickers: {count}",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )
    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# 🔰 CREATE BRAND LOGO EMOJI
# ---------------------------------------------------------------------------

async def brand_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    context.user_data["pack_type"] = "brand_emoji"
    await update.message.reply_text(
        "🔰 <b>Create brand logo emoji pack</b>\n\n"
        "✍️ Enter your <b>brand name</b> (will appear on the emoji):\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return BRAND_TITLE


async def brand_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text or len(text) > 64:
        await update.message.reply_text("❌ Brand name must be 1–64 characters. Try again:")
        return BRAND_TITLE
    context.user_data["pack_title"] = text
    context.user_data["brand_name"] = text
    await update.message.reply_text(
        "✅ Brand name saved!\n\n"
        "✍️ Enter a <b>short link</b> for the pack:\n"
        "/generate — generate random link\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return BRAND_LINK


async def brand_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == "/generate":
        text = _random_link()
        await update.message.reply_text(f"🔀 Generated: <code>{text}</code>", parse_mode=ParseMode.HTML)
    if not RE_VALID_LINK.match(text):
        await update.message.reply_text("❌ Invalid link. Try again or /generate:")
        return BRAND_LINK

    bot_username = (await context.bot.get_me()).username
    context.user_data["pack_name"] = f"{text}_by_{bot_username}"
    context.user_data["pack_link_base"] = text

    await update.message.reply_text(
        "✅ Link saved!\n\n"
        "🖼 Now send your <b>brand logo image</b> (optional)\n"
        "or /skip to use a text-only emoji.\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return BRAND_LOGO


async def brand_logo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    logo_data: bytes | None = None
    if msg.text and msg.text.strip() == "/skip":
        context.user_data["logo_data"] = None
    else:
        data = await _download_photo(update, context)
        if data is None:
            await msg.reply_text("⚠️ Please send an image or /skip.")
            return BRAND_LOGO
        logo_data = bytes(data)
        context.user_data["logo_data"] = logo_data

    await msg.reply_text(
        "✅ Got it!\n\n"
        "📎 Now send <b>additional photos</b> to add more brand emojis (optional)\n"
        "/done — create the pack now\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    context.user_data.setdefault("stickers_uploaded", [])
    return BRAND_MEDIA


async def brand_media(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    msg = update.message

    data = await _download_photo(update, context)
    if data is None:
        await msg.reply_text("⚠️ Please send a photo or /done to finish.")
        return BRAND_MEDIA

    await msg.reply_text("⏳ Processing…")
    webp_io = _make_square_webp(bytes(data), 100)
    file_id = await _upload_emoji_sticker(context, user.id, webp_io)
    if file_id:
        context.user_data["stickers_uploaded"].append(file_id)

    count = len(context.user_data["stickers_uploaded"])
    await msg.reply_text(f"✅ Added! Total extra images: {count}\nSend more or /done.")
    return BRAND_MEDIA


async def brand_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    pack_name = context.user_data["pack_name"]
    pack_title = context.user_data["pack_title"]
    brand_name = context.user_data["brand_name"]
    logo_data = context.user_data.get("logo_data")

    await update.message.reply_text("⏳ Creating brand emoji pack…")

    # Create the main brand logo emoji
    brand_img = _make_brand_logo_emoji(logo_data, brand_name)
    main_file_id = await _upload_emoji_sticker(context, user.id, brand_img)
    if main_file_id is None:
        await update.message.reply_text(
            "❌ Failed to create brand emoji. Please try again.",
            reply_markup=MAIN_KEYBOARD,
        )
        context.user_data.clear()
        return ConversationHandler.END

    stickers = [main_file_id] + context.user_data.get("stickers_uploaded", [])
    input_stickers = [
        InputSticker(sticker=fid, emoji_list=["🔰"], format=StickerFormat.STATIC)
        for fid in stickers[:200]
    ]

    try:
        await context.bot.create_new_sticker_set(
            user_id=user.id,
            name=pack_name,
            title=pack_title,
            stickers=input_stickers,
            sticker_type=StickerType.CUSTOM_EMOJI,
        )
        db = db_module.get_db()
        if db is not None:
            db_module.save_sticker_pack(
                db, user.id, pack_name, pack_title, "brand_emoji", len(input_stickers)
            )
        link_base = context.user_data.get("pack_link_base", pack_name)
        await update.message.reply_text(
            f"🎉 <b>Brand emoji pack created!</b>\n\n"
            f"🔰 <b>{pack_title}</b>\n"
            f"🔗 <a href='https://t.me/addemoji/{link_base}'>t.me/addemoji/{link_base}</a>",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
    except Exception as exc:
        logger.error("create_new_sticker_set (brand) failed: %s", exc)
        await update.message.reply_text(
            f"❌ Failed to create pack: {exc}",
            reply_markup=MAIN_KEYBOARD,
        )

    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# 🔄 CONVERT STICKERS TO EMOJI
# ---------------------------------------------------------------------------

async def convert_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "🔄 <b>Convert stickers to emoji</b>\n\n"
        "📎 Send me a <b>sticker</b> from the pack you want to convert.\n"
        "I will convert the whole pack into custom emoji size (100x100).\n\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return CONVERT_WAITING


async def convert_sticker_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    if not msg.sticker:
        await msg.reply_text("⚠️ Please send a sticker. /cancel to cancel.")
        return CONVERT_WAITING

    sticker = msg.sticker
    set_name = sticker.set_name
    if not set_name:
        await msg.reply_text("❌ This sticker has no pack. Please send a sticker from a pack.")
        return CONVERT_WAITING

    context.user_data["source_set_name"] = set_name
    await msg.reply_text(
        f"✅ Got sticker from pack: <code>{set_name}</code>\n\n"
        "✍️ Enter a <b>title</b> for the new emoji pack:\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return CONVERT_TITLE


async def convert_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text or len(text) > 64:
        await update.message.reply_text("❌ Title must be 1–64 characters. Try again:")
        return CONVERT_TITLE
    context.user_data["pack_title"] = text
    await update.message.reply_text(
        "✅ Title saved!\n\n"
        "✍️ Enter a <b>short link</b> for the new pack:\n"
        "/generate — generate random link\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return CONVERT_LINK


async def convert_link(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    text = update.message.text.strip()
    if text == "/generate":
        text = _random_link()
        await update.message.reply_text(f"🔀 Generated: <code>{text}</code>", parse_mode=ParseMode.HTML)
    if not RE_VALID_LINK.match(text):
        await update.message.reply_text("❌ Invalid link. Try again or /generate:")
        return CONVERT_LINK

    bot_username = (await context.bot.get_me()).username
    pack_name = f"{text}_by_{bot_username}"
    source_set_name = context.user_data["source_set_name"]
    pack_title = context.user_data["pack_title"]

    await update.message.reply_text("⏳ Fetching source sticker pack and converting, please wait…")

    try:
        source_set = await context.bot.get_sticker_set(source_set_name)
    except Exception as exc:
        logger.error("get_sticker_set failed: %s", exc)
        await update.message.reply_text(
            "❌ Could not fetch the source sticker pack. Please try again.",
            reply_markup=MAIN_KEYBOARD,
        )
        context.user_data.clear()
        return ConversationHandler.END

    input_stickers = []
    for s in source_set.stickers[:200]:
        try:
            file = await context.bot.get_file(s.file_id)
            data = await file.download_as_bytearray()
            webp_io = _make_square_webp(bytes(data), 100)
            file_id = await _upload_emoji_sticker(context, user.id, webp_io)
            if file_id:
                input_stickers.append(
                    InputSticker(sticker=file_id, emoji_list=["⭐"], format=StickerFormat.STATIC)
                )
        except Exception as exc:
            logger.warning("Skipping sticker during convert: %s", exc)
            continue

    if not input_stickers:
        await update.message.reply_text(
            "❌ Could not convert any stickers from that pack.",
            reply_markup=MAIN_KEYBOARD,
        )
        context.user_data.clear()
        return ConversationHandler.END

    try:
        await context.bot.create_new_sticker_set(
            user_id=user.id,
            name=pack_name,
            title=pack_title,
            stickers=input_stickers,
            sticker_type=StickerType.CUSTOM_EMOJI,
        )
        db = db_module.get_db()
        if db is not None:
            db_module.save_sticker_pack(
                db, user.id, pack_name, pack_title, "converted_emoji", len(input_stickers)
            )
        await update.message.reply_text(
            f"🎉 <b>Converted {len(input_stickers)} stickers to emoji!</b>\n\n"
            f"📦 <b>{pack_title}</b>\n"
            f"🔗 <a href='https://t.me/addemoji/{text}'>t.me/addemoji/{text}</a>",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
    except Exception as exc:
        logger.error("create_new_sticker_set (convert) failed: %s", exc)
        await update.message.reply_text(
            f"❌ Failed to create pack: {exc}",
            reply_markup=MAIN_KEYBOARD,
        )

    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# 💞 CREATE LOVE NAME STATUS
# ---------------------------------------------------------------------------

LOVE_STYLES = {
    "1": ("💗 Pink Love", 1),
    "2": ("❤️ Red Passion", 2),
    "3": ("💜 Purple Dream", 3),
    "4": ("💙 Blue Calm", 4),
    "5": ("💚 Green Nature", 5),
}


async def love_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "💞 <b>Create love name status emoji</b>\n\n"
        "✍️ Enter the <b>name</b> to display on the emoji:\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return LOVE_NAME


async def love_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name or len(name) > 32:
        await update.message.reply_text("❌ Name must be 1–32 characters. Try again:")
        return LOVE_NAME
    context.user_data["love_name"] = name

    style_text = "\n".join(f"  <code>{k}</code> — {v[0]}" for k, v in LOVE_STYLES.items())
    await update.message.reply_text(
        f"✅ Name: <b>{name}</b>\n\n"
        "🎨 Choose a <b>color style</b> by sending the number:\n"
        f"{style_text}\n\n"
        "/cancel — to cancel",
        parse_mode=ParseMode.HTML,
    )
    return LOVE_STYLE


async def love_style_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    choice = update.message.text.strip()
    if choice not in LOVE_STYLES:
        await update.message.reply_text(
            "⚠️ Please send a number 1-5 to choose a style.\n"
            + "\n".join(f"  <code>{k}</code> — {v[0]}" for k, v in LOVE_STYLES.items()),
            parse_mode=ParseMode.HTML,
        )
        return LOVE_STYLE

    name = context.user_data["love_name"]
    style_label, style_idx = LOVE_STYLES[choice]
    await update.message.reply_text(f"⏳ Creating {style_label} emoji for <b>{name}</b>…", parse_mode=ParseMode.HTML)

    # Generate multiple love name emojis (5 variants)
    uploaded_ids = []
    for variant in range(1, 6):
        img_io = _make_love_name_image(name, (style_idx + variant - 1) % 5 + 1)
        file_id = await _upload_emoji_sticker(context, user.id, img_io)
        if file_id:
            uploaded_ids.append(file_id)

    if not uploaded_ids:
        await update.message.reply_text(
            "❌ Failed to create emoji. Please try again.",
            reply_markup=MAIN_KEYBOARD,
        )
        context.user_data.clear()
        return ConversationHandler.END

    bot_username = (await context.bot.get_me()).username
    pack_link = f"love_{_random_link(6)}"
    pack_name = f"{pack_link}_by_{bot_username}"
    pack_title = f"{name} Love Status"

    input_stickers = [
        InputSticker(sticker=fid, emoji_list=["💞"], format=StickerFormat.STATIC)
        for fid in uploaded_ids
    ]

    try:
        await context.bot.create_new_sticker_set(
            user_id=user.id,
            name=pack_name,
            title=pack_title,
            stickers=input_stickers,
            sticker_type=StickerType.CUSTOM_EMOJI,
        )
        db = db_module.get_db()
        if db is not None:
            db_module.save_sticker_pack(
                db, user.id, pack_name, pack_title, "love_status", len(input_stickers)
            )
        await update.message.reply_text(
            f"🎉 <b>Love name emoji created!</b>\n\n"
            f"💞 <b>{pack_title}</b>\n"
            f"🎨 Style: {style_label}\n"
            f"🔗 <a href='https://t.me/addemoji/{pack_link}'>t.me/addemoji/{pack_link}</a>",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
    except Exception as exc:
        logger.error("create_new_sticker_set (love) failed: %s", exc)
        await update.message.reply_text(
            f"❌ Failed to create pack: {exc}",
            reply_markup=MAIN_KEYBOARD,
        )

    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# 📋 LIST OF MY PACKS
# ---------------------------------------------------------------------------

async def list_packs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    db = db_module.get_db()
    if db is None:
        await update.message.reply_text("❌ Database unavailable.", reply_markup=MAIN_KEYBOARD)
        return

    packs = db_module.get_user_packs(db, user.id)
    if not packs:
        await update.message.reply_text(
            "📋 You have no packs yet.\n\nUse the menu to create one!",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    type_labels = {
        "custom_emoji": "✨ Premium emoji",
        "regular": "📦 Sticker pack",
        "brand_emoji": "🔰 Brand emoji",
        "converted_emoji": "🔄 Converted emoji",
        "love_status": "💞 Love status",
    }
    lines = []
    for p in packs[:20]:
        t = type_labels.get(p.get("pack_type", ""), "📦")
        name = p.get("pack_title", "Untitled")
        pack_name = p.get("pack_name", "")
        count = p.get("sticker_count", 0)
        # Determine link prefix
        if p.get("pack_type") in ("regular",):
            url = f"https://t.me/addstickers/{pack_name}"
        else:
            url = f"https://t.me/addemoji/{pack_name}"
        lines.append(f"{t} <a href='{url}'>{name}</a> ({count} items)")

    text = "📋 <b>Your packs:</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=MAIN_KEYBOARD)


# ---------------------------------------------------------------------------
# /cancel — universal conversation cancel
# ---------------------------------------------------------------------------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "❌ Cancelled.",
        reply_markup=MAIN_KEYBOARD,
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Admin commands (existing)
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
# Group message handler (existing ID-check feature)
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
        reply_text = format_duplicate_reply(
            template,
            user_mention=sender_mention,
            original_user=original_user,
            matches=matches,
        )
        await message.reply_text(reply_text, parse_mode=ParseMode.HTML)
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
# Menu text → conversation router
# ---------------------------------------------------------------------------

async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route main keyboard button taps to the right action."""
    text = update.message.text.strip()
    if text == "📋 List of my packs":
        await list_packs(update, context)


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

def _conv(entry_text: str, start_handler, title_state, title_handler,
          link_state, link_handler, media_state, media_handler,
          done_handler, extra_states=None):
    """Helper to build a standard title→link→media ConversationHandler."""
    states = {
        title_state: [
            MessageHandler(filters.Regex("^/cancel$"), cancel),
            MessageHandler(filters.TEXT & ~filters.COMMAND, title_handler),
        ],
        link_state: [
            MessageHandler(filters.Regex("^/cancel$"), cancel),
            MessageHandler(filters.Regex("^/generate$"), link_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler),
        ],
        media_state: [
            MessageHandler(filters.Regex("^/cancel$"), cancel),
            MessageHandler(filters.Regex("^/done$"), done_handler),
            MessageHandler(filters.PHOTO | filters.Document.ALL | filters.Sticker.ALL, media_handler),
            MessageHandler(filters.TEXT & ~filters.COMMAND, media_handler),
        ],
    }
    if extra_states:
        states.update(extra_states)
    return ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(entry_text)}$"), start_handler)],
        states=states,
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )


async def run_bot():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # ── Premium emoji conversation ──────────────────────────────────────────
    emoji_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^✨ Create Premium emoji$"), emoji_start)],
        states={
            EMOJI_TITLE: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, emoji_title),
            ],
            EMOJI_LINK: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/generate$"), emoji_link),
                MessageHandler(filters.TEXT & ~filters.COMMAND, emoji_link),
            ],
            EMOJI_MEDIA: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/done$"), emoji_done),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.Sticker.ALL, emoji_media),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ── Sticker pack conversation ───────────────────────────────────────────
    sticker_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^📦 Create sticker pack$"), sticker_start)],
        states={
            STICKER_TITLE: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sticker_title),
            ],
            STICKER_LINK: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/generate$"), sticker_link),
                MessageHandler(filters.TEXT & ~filters.COMMAND, sticker_link),
            ],
            STICKER_MEDIA: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/done$"), sticker_done),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE | filters.Sticker.ALL, sticker_media),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ── Brand logo emoji conversation ───────────────────────────────────────
    brand_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^🔰 Create brand logo emoji$"), brand_start)],
        states={
            BRAND_TITLE: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, brand_title),
            ],
            BRAND_LINK: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/generate$"), brand_link),
                MessageHandler(filters.TEXT & ~filters.COMMAND, brand_link),
            ],
            BRAND_LOGO: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/skip$"), brand_logo),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, brand_logo),
            ],
            BRAND_MEDIA: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/done$"), brand_done),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, brand_media),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ── Convert stickers to emoji conversation ──────────────────────────────
    convert_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^🔄 Convert stickers to emoji$"), convert_start)],
        states={
            CONVERT_WAITING: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Sticker.ALL, convert_sticker_received),
            ],
            CONVERT_TITLE: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, convert_title),
            ],
            CONVERT_LINK: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.Regex("^/generate$"), convert_link),
                MessageHandler(filters.TEXT & ~filters.COMMAND, convert_link),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ── Love name status conversation ───────────────────────────────────────
    love_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(r"^💞 Create love name status$"), love_start)],
        states={
            LOVE_NAME: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, love_name_received),
            ],
            LOVE_STYLE: [
                MessageHandler(filters.Regex("^/cancel$"), cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, love_style_received),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    # ── Register all handlers ───────────────────────────────────────────────
    application.add_handler(emoji_conv)
    application.add_handler(sticker_conv)
    application.add_handler(brand_conv)
    application.add_handler(convert_conv)
    application.add_handler(love_conv)

    application.add_handler(CommandHandler("start", cmd_start))

    # List packs via menu button
    application.add_handler(
        MessageHandler(filters.Regex(r"^📋 List of my packs$"), list_packs)
    )

    # Admin commands (private chat only)
    application.add_handler(CommandHandler("setmsg", cmd_setmsg, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("getmsg", cmd_getmsg, filters=filters.ChatType.PRIVATE))
    application.add_handler(CommandHandler("resetmsg", cmd_resetmsg, filters=filters.ChatType.PRIVATE))

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
