import os
import re
import io
import math
import logging
import asyncio
import threading
import pytz
import requests
import json
from datetime import datetime, time, timedelta
from pymongo import MongoClient
from pymongo.errors import PyMongoError

try:
    from langdetect import detect as langdetect_detect
except Exception:
    langdetect_detect = None
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler,
    PicklePersistence, filters
)
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
    InputFile, BotCommand, MessageEntity
)
from telegram.ext import CallbackContext
from web_server import keep_alive

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TOKEN = os.getenv('BOT_TOKEN')

ADMIN_IDS = [7196380140, 1827336632, 7039073770, 8746232946]

FEEDBACK_AWAITING = 3
BROADCAST_SELECT_CHAT = 10
BROADCAST_AWAITING_MESSAGE = 11
BROADCAST_CONFIRMATION = 12

SCHEDULE_SET_TIME = 20
SCHEDULE_SET_MESSAGE = 21
SCHEDULE_SELECT_TYPE = 23
SCHEDULE_SELECT_GROUP = 22

BOT_SETTINGS_SELECT   = 40
BOT_SETTINGS_AWAITING = 41
BOT_SETTINGS_PHOTO    = 42

SETMSG_SELECT = 60
SETMSG_AWAIT  = 61


# ============================================================
# CUSTOM MESSAGES  (Owner-only /setmsg)
# ============================================================

CUSTOM_MSG_LABELS = {
    "welcome":          "🏠 Welcome / Start Message",
    "help":             "❓ Help Message",
    "hidemenu":         "🙈 Hide Menu Message",
    "form":             "📋 /form — Report template intro",
    "cleardata_ok":     "✅ /cleardata — Data deleted message",
    "cleardata_empty":  "📭 /cleardata — No data message",
    "showdata_empty":   "📭 /showdata — No data message",
    "showdata_footer":  "💬 /showdata — Footer hint",
    "total_plus_empty": "📊 /total_plus — No data message",
    "total_plus_footer":"💬 /total_plus — Footer hint",
    "reset_plus_empty": "📊 /reset_plus — No counter message",
    "reset_plus_ok":    "✅ /reset_plus — Reset success message",
    "plus_fmt":         "➕ Plus count format  ({count} သုံးပါ)",
    "digit_emoji":      "🔢 Animated number emoji (0→9 အစဉ်လိုက်)",
    # Plus / minus reply messages
    "plus_already":     "⚠️ Already counted reply",
    "minus_not_data":   "⚠️ Not deposit/plus data warning",
    "minus_del_entry":  "🗑️ Deposit entry deleted msg  ({entry} သုံးနိုင်)",
    "minus_del_plus":   "🗑️ Plus count removed msg  ({given_count} animated)",
    # Total plus display
    "total_plus_header":"📊 Plus counter header",
    "total_plus_row":   "📝 Counter row  ({i} {name} {count} animated)",
    "total_plus_grand": "🔢 Counter total line  ({grand_total} animated)",
    # 打枪 / သာချန်း reply
    "daqiang_reply":    "🎯 打枪/သာချန်း reply ({username_value} နှင့် {sender_mention} သုံးနိုင်)",
    # Green alert
    "green_alert":      "🟢 Green alert ({count} သုံးနိုင်)",
}

DEFAULT_MSGS: dict = {
    "welcome": (
        "မင်္ဂလာပါ။ {name}\n"
        "Bot အသုံးပြုနည်းသိအောင် /guide 📝 ကိုနှိပ်၍ကြည့်နိုင်ပါသည်။📌\n\n"
        "🧮 Bot PM တွင် math expression ရိုက်ပါ (e.g. 2+2)"
    ),
    "help": (
        "Bot commands:\n\n"
        " /form - Report template\n"
        " /showdata - Show today's data\n"
        " /cleardata - Clear today's data\n"
        " /total_plus - Plus counter\n"
        " /reset_plus - Reset plus counter\n"
        " /feedback - Send feedback to admin\n"
        " /guide - Usage guide\n"
        " /hidemenu - Hide menu\n\n"
        "🧮 Math: Bot PM တွင် expression ရိုက်ပါ (e.g. 2+2)"
    ),
    "hidemenu":          "Menu keyboard ကို ဖျက်လိုက်ပါပြီ။ /start ဖြင့် ပြန်ခေါ်နိုင်ပါသည်။😒",
    "form":              "📋 Deposit Report Form Template\n\nကော်ပီကူးယူ၍ ဖြည့်စွက်ပြီး ပို့ပေးပါ:\n\n",
    "cleardata_ok":      "✅ Data deleted for today ({today}).\n✅ Plus counter reset ပြုလုပ်ပြီးပါပြီ။",
    "cleardata_empty":   "No data found for today ({today}).",
    "showdata_empty":    "No data collected yet for today ({today}) in this chat.",
    "showdata_footer":   "{mention} report ပြင်ဆင်ပြီးပါက /cleardata နှိပ်ပါ။",
    "total_plus_empty":  "📊 ဤ chat တွင် (+) reply မရှိသေးပါ။",
    "total_plus_footer": "{mention} အလုပ်ဆင်းမည်ဆိုပါက /reset_plus နှိပ်ခဲ့ပါ",
    "reset_plus_empty":  "📊 ဤ chat တွင် ရှင်လင်းစရာ Plus counter မရှိသေးပါ။",
    "reset_plus_ok":     "✅ Plus counter reset ပြုလုပ်ပြီးပါပြီ။\n🗑️ ဤ chat ရှိ အဖွဲ့ဝင် {count} ဦး၏ ကောင်တာများ ပြန်လည်သုညမှ စတင်ပြီ။",
    "plus_fmt":          "+{count}",
    "digit_emoji":       "",   # empty = use plain digits
    # Plus / minus reply messages
    "plus_already":      "⚠️ ဤ message အား (+) ပေးပြီးပြီ ဖြစ်ပါသည်။ (+{given_count})",
    "minus_not_data":    "⚠️ ဤ message သည် Deposit data (သို့) (+) မဟုတ်ပါ။",
    "minus_del_entry":   "🗑️ ပယ်ဖျက်လိုက်ပါသည်:\n{entry}",
    "minus_del_plus":    "🗑️ +{given_count} ကို ပယ်ဖျက်လိုက်ပါသည်။",
    # Total plus display
    "total_plus_header": "📊 Plus Counter",
    "total_plus_row":    "  {i}. {name} → +{count}",
    "total_plus_grand":  "Total = {grand_total}",
    # 打枪 / သာချန်း reply
    "daqiang_reply":     "{username_value}\n\n{sender_mention} ဒီ client ကို သင့်ဘက်မှာ မှတ်သားထားဖို့ မမေ့ပါနဲ့",
    # Green alert
    "green_alert":       "ဒီနေ့ Green ပေးပို့သည်မှာ {count} ယောက် ရှိပါပြီ။\n\nTarget ပြည့်ချင်ရင် လူကောင်းရှာဖို့ အကြံပြုပါသည်။",
}


def _is_owner(user_id: int) -> bool:
    """Bot owner = OWNER_ID env var (single ID).
    If OWNER_ID is not set, any ADMIN_ID can use /setmsg."""
    env = os.getenv("OWNER_ID", "").strip()
    if env.isdigit():
        return user_id == int(env)
    return user_id in ADMIN_IDS


def _get_custom_msgs(bot_data: dict) -> dict:
    return bot_data.setdefault("custom_msgs", {})


def get_msg(bot_data: dict, key: str, **fmt) -> str:
    """Return owner-customised message, falling back to DEFAULT_MSGS."""
    stored = _get_custom_msgs(bot_data).get(key, {})
    text = stored.get("text") or DEFAULT_MSGS.get(key, "")
    return _safe_substitute(text, **fmt) if fmt else text


def _build_entities(entities_raw: list):
    """Construct MessageEntity objects directly from stored dicts.

    Uses the constructor (no bot reference needed) so custom_emoji
    entities are built correctly without requiring a live Bot instance.
    Handles both snake_case and camelCase dict keys produced by to_dict().
    """
    result = []
    for e in entities_raw:
        try:
            result.append(MessageEntity(
                type=e.get("type", ""),
                offset=int(e.get("offset", 0)),
                length=int(e.get("length", 0)),
                url=e.get("url"),
                language=e.get("language"),
                # to_dict() may store as snake_case or camelCase
                custom_emoji_id=(
                    e.get("custom_emoji_id") or e.get("customEmojiId")
                ),
            ))
        except Exception:
            pass
    return result or None


async def _reply_custom(message, bot_data: dict, key: str,
                        reply_markup=None, parse_mode=None, **fmt):
    """Reply with a customisable message, preserving premium-emoji entities.

    Entity offsets are adjusted automatically when format placeholders
    (e.g. {today}, {name}) change the text length.
    """
    stored = _get_custom_msgs(bot_data).get(key, {})
    raw_text = stored.get("text") or DEFAULT_MSGS.get(key, "")
    raw_entities = stored.get("entities")

    text, adj_entities_raw = _apply_fmt_and_adjust_entities(raw_text, raw_entities, **fmt)

    entities = _build_entities(adj_entities_raw) if adj_entities_raw else None

    if entities:
        await message.reply_text(text, entities=entities, reply_markup=reply_markup)
    else:
        await message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)


# ─── /setmsg conversation handlers ───────────────────────────────────────────

async def setmsg_start(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    if not user or not _is_owner(user.id):
        await update.message.reply_text("❌ Bot owner သာ ဤ command ကို သုံးနိုင်သည်။")
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Bot PM ထဲတွင်သာ အသုံးပြုနိုင်သည်။")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton(label, callback_data=f"setmsg_{key}")]
        for key, label in CUSTOM_MSG_LABELS.items()
    ]
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="setmsg_cancel")])
    await update.message.reply_text(
        "✏️ ပြောင်းလဲလိုသော message ကို ရွေးပါ:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SETMSG_SELECT


async def setmsg_select(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "setmsg_cancel":
        await query.edit_message_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
        return ConversationHandler.END

    key = data[len("setmsg_"):]
    if key not in CUSTOM_MSG_LABELS:
        await query.edit_message_text("❌ Invalid selection.")
        return ConversationHandler.END

    context.user_data["setmsg_key"] = key
    label = CUSTOM_MSG_LABELS[key]
    stored = _get_custom_msgs(context.application.bot_data).get(key, {})
    current = stored.get("text") or DEFAULT_MSGS.get(key, "(default)")

    if key == "digit_emoji":
        digit_map = _get_digit_map(context.application.bot_data)
        if digit_map:
            digits_preview = "".join(info["char"] for _, info in sorted(digit_map.items()))
            status = f"<b>လက်ရှိ (0→9):</b> <blockquote>{digits_preview}</blockquote>"
        else:
            status = "<b>လက်ရှိ:</b> မသတ်မှတ်ရသေး (plain digits သုံးနေသည်)"
        await query.edit_message_text(
            f"🔢 <b>{label}</b>\n\n"
            f"{status}\n\n"
            "✏️ <b>Animated number emoji 0 မှ 9 ထိ</b> — အစဉ်လိုက် တစ်ကြောင်းတည်း ပို့ပါ\n"
            "<i>ဥပမာ — Telegram emoji keyboard မှ</i> 0️⃣1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣9️⃣\n\n"
            "<i>/reset — plain digits ပြန်ထား</i>\n"
            "<i>/cancel — ဖျက်သိမ်း</i>",
            parse_mode="HTML"
        )
        return SETMSG_AWAIT

    await query.edit_message_text(
        f"📝 <b>{label}</b>\n\n"
        f"<b>လက်ရှိ message:</b>\n"
        f"<blockquote>{current[:800]}</blockquote>\n\n"
        "✏️ အသစ်ရိုက်ထည့်ပါ (Premium animated emoji ပါ တိုက်ရိုက်ထည့်နိုင်သည်)\n\n"
        "<i>/reset — default ပြန်ထား</i>\n"
        "<i>/cancel — ဖျက်သိမ်း</i>",
        parse_mode="HTML"
    )
    return SETMSG_AWAIT


async def setmsg_receive(update: Update, context: CallbackContext) -> int:
    msg = update.message
    text = msg.text or ""

    if text.strip() in ("/cancel", "cancel"):
        await msg.reply_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
        return ConversationHandler.END

    key = context.user_data.get("setmsg_key")
    if not key:
        await msg.reply_text("❌ Session expired. /setmsg ထပ်စမ်းပါ။")
        return ConversationHandler.END

    if text.strip() == "/reset":
        custom = _get_custom_msgs(context.application.bot_data)
        custom.pop(key, None)
        if context.application.persistence:
            await context.application.persistence.flush()
        save_bot_config_to_mongo(context.application.bot_data)
        label = CUSTOM_MSG_LABELS.get(key, key)
        await msg.reply_text(
            f"✅ <b>{label}</b> — default သို့ ပြန်သတ်မှတ်ပြီးပါပြီ။",
            parse_mode="HTML"
        )
        return ConversationHandler.END

    # Store text + entities (premium animated emoji entities preserved)
    entities_raw = None
    if msg.entities:
        try:
            entities_raw = [e.to_dict() for e in msg.entities]
        except Exception:
            entities_raw = None

    custom = _get_custom_msgs(context.application.bot_data)
    custom[key] = {"text": text, "entities": entities_raw}
    if context.application.persistence:
        await context.application.persistence.flush()
    save_bot_config_to_mongo(context.application.bot_data)

    label = CUSTOM_MSG_LABELS.get(key, key)
    emoji_note = " ✨ (Premium emoji သိမ်းဆည်းပြီး)" if entities_raw else ""
    await msg.reply_text(
        f"✅ <b>{label}</b> — သိမ်းဆည်းပြီးပါပြီ!{emoji_note}\n\n"
        f"<blockquote>{text[:500]}</blockquote>",
        parse_mode="HTML"
    )
    return ConversationHandler.END


async def setmsg_cancel_conv(update: Update, context: CallbackContext) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
    elif update.message:
        await update.message.reply_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
    return ConversationHandler.END


def _safe_substitute(raw_text: str, **fmt) -> str:
    """Replace only known {key} placeholders using regex — never raises.

    Unknown or malformed {…} patterns are left intact, so animated emoji
    and other literal braces in the text are preserved safely.
    """
    if not fmt or "{" not in raw_text:
        return raw_text

    def _replacer(m: re.Match) -> str:
        key = m.group(1)
        return str(fmt[key]) if key in fmt else m.group(0)

    return re.sub(r'\{(\w+)\}', _replacer, raw_text)


def _apply_fmt_and_adjust_entities(raw_text: str, entities_raw, **fmt) -> tuple:
    """Apply safe placeholder substitution to raw_text and shift entity offsets.

    Returns (rendered_text, adjusted_entities_raw_list | None).
    Uses _safe_substitute() instead of str.format() so unknown/malformed
    {…} patterns (e.g. from animated emoji surroundings) never cause failures.
    """
    if not fmt or "{" not in raw_text:
        return raw_text, (list(entities_raw) if entities_raw else None)

    rendered = _safe_substitute(raw_text, **fmt)

    if not entities_raw:
        return rendered, None

    if rendered == raw_text:
        # Nothing was replaced — offsets unchanged
        return rendered, list(entities_raw)

    # Replay substitutions left-to-right, shifting offsets after each replacement.
    adjusted = [dict(e) for e in entities_raw]
    working = raw_text

    for key, val in fmt.items():
        placeholder = "{" + key + "}"
        val_str = str(val)
        delta = len(val_str) - len(placeholder)
        search_from = 0
        while True:
            idx = working.find(placeholder, search_from)
            if idx == -1:
                break
            if delta != 0:
                end_ph = idx + len(placeholder)
                for e in adjusted:
                    if e.get("offset", 0) >= end_ph:
                        e["offset"] = e["offset"] + delta
            working = working[:idx] + val_str + working[idx + len(placeholder):]
            search_from = idx + len(val_str)

    return rendered, adjusted or None


def _adjust_entities_for_count(template: str, entities_raw, count: int) -> tuple:
    """Convenience wrapper around _apply_fmt_and_adjust_entities for {count}."""
    return _apply_fmt_and_adjust_entities(template, entities_raw, count=count)


def _utf16_len(s: str) -> int:
    """UTF-16 code-unit length — the unit Telegram uses for entity offsets."""
    return len(s.encode("utf-16-le")) // 2


def _get_digit_map(bot_data: dict) -> dict:
    """Parse stored digit_emoji data into {digit_str: {"char": ..., "cid": ...}}.

    Expects the owner to have sent animated emoji in order 0–9 (10 emojis).
    Entity order in the message = digit order.
    """
    stored = _get_custom_msgs(bot_data).get("digit_emoji", {})
    if not stored:
        return {}
    text = stored.get("text", "")
    raw_ents = stored.get("entities") or []
    if not text or not raw_ents:
        return {}

    digit_ents = sorted(
        [e for e in raw_ents if e.get("type") == "custom_emoji"],
        key=lambda e: e.get("offset", 0),
    )
    if not digit_ents:
        return {}

    utf16_bytes = text.encode("utf-16-le")
    digit_map: dict = {}
    for i, ent in enumerate(digit_ents[:10]):
        off   = ent.get("offset", 0) * 2
        length = ent.get("length", 1) * 2
        try:
            char = utf16_bytes[off: off + length].decode("utf-16-le")
        except Exception:
            continue
        cid = ent.get("custom_emoji_id") or ent.get("customEmojiId")
        digit_map[str(i)] = {"char": char, "cid": cid}
    return digit_map


def _render_count(count: int, digit_map: dict) -> tuple:
    """Convert an integer count to (text, entities_raw).

    Uses animated emoji for each digit when digit_map is set.
    Returned entity offsets are relative to the start of the returned text.
    """
    text = ""
    entities_raw: list = []
    for d in str(count):
        info = digit_map.get(d)
        if info and info.get("cid"):
            off  = _utf16_len(text)
            char = info["char"]
            entities_raw.append({
                "type": "custom_emoji",
                "offset": off,
                "length": _utf16_len(char),
                "custom_emoji_id": info["cid"],
            })
            text += char
        else:
            text += d
    return text, entities_raw


async def _reply_custom_plus(message, bot_data: dict, count: int) -> None:
    """Send the plus-count reply with animated digits + template entity support."""
    stored   = _get_custom_msgs(bot_data).get("plus_fmt", {})
    template = stored.get("text") or DEFAULT_MSGS["plus_fmt"]
    tmpl_ents_raw: list = stored.get("entities") or []

    digit_map              = _get_digit_map(bot_data)
    count_text, count_ents = _render_count(count, digit_map)

    placeholder = "{count}"
    ph_str_idx  = template.find(placeholder)

    if ph_str_idx == -1:
        # No {count} in template — send as-is (no digit substitution)
        entities = _build_entities(tmpl_ents_raw) if tmpl_ents_raw else None
        if entities:
            await message.reply_text(template, entities=entities)
        else:
            await message.reply_text(template)
        return

    prefix = template[:ph_str_idx]
    suffix = template[ph_str_idx + len(placeholder):]
    text   = prefix + count_text + suffix

    # UTF-16 positions
    prefix_u16    = _utf16_len(prefix)
    ph_u16        = _utf16_len(placeholder)        # len("{count}") in UTF-16 units
    count_u16     = _utf16_len(count_text)
    delta         = count_u16 - ph_u16
    suffix_u16_at = prefix_u16 + ph_u16            # where suffix began (before shift)

    merged: list = []

    # Template entities: shift those that start at/after the suffix boundary
    for e in tmpl_ents_raw:
        e2 = dict(e)
        if e2.get("offset", 0) >= suffix_u16_at:
            e2["offset"] = e2["offset"] + delta
        merged.append(e2)

    # Count digit entities: shift right by prefix UTF-16 length
    for ce in count_ents:
        ce2 = dict(ce)
        ce2["offset"] = ce2.get("offset", 0) + prefix_u16
        merged.append(ce2)

    # Sort by offset
    merged.sort(key=lambda e: e.get("offset", 0))

    entities = _build_entities(merged) if merged else None
    if entities:
        await message.reply_text(text, entities=entities)
    else:
        await message.reply_text(text)


# ─── Animated-count helpers ──────────────────────────────────────────────────

def _insert_animated_count(template: str, count_val: int, count_key: str,
                            digit_map: dict) -> tuple:
    """Replace {count_key} in template with animated digit text.

    Returns (final_text, entities_raw) with offsets relative to start of text.
    """
    count_text, count_ents_raw = _render_count(count_val, digit_map)
    placeholder = "{" + count_key + "}"
    ph_idx = template.find(placeholder)
    if ph_idx == -1:
        return template, []
    prefix   = template[:ph_idx]
    suffix   = template[ph_idx + len(placeholder):]
    final    = prefix + count_text + suffix
    base_u16 = _utf16_len(prefix)
    shifted  = [{**e, "offset": e.get("offset", 0) + base_u16} for e in count_ents_raw]
    return final, shifted


async def _reply_custom_animated(message, bot_data: dict, key: str,
                                  animated_counts: dict = None,
                                  reply_markup=None, **other_fmt):
    """Reply with a customisable message, substituting {key} placeholders with
    animated digits for each entry in animated_counts={placeholder: int_value}.

    Non-count placeholders in other_fmt are substituted first with entity-offset
    adjustment; then each animated count placeholder is replaced using _render_count.
    """
    stored     = _get_custom_msgs(bot_data).get(key, {})
    raw_text   = stored.get("text") or DEFAULT_MSGS.get(key, "")
    raw_ents   = stored.get("entities") or []
    digit_map  = _get_digit_map(bot_data)

    # Step 1: substitute non-count placeholders (adjusts entity offsets)
    text, working_ents = _apply_fmt_and_adjust_entities(raw_text, raw_ents, **other_fmt)
    working_ents = list(working_ents or [])

    # Step 2: substitute each animated-count placeholder
    for count_key, count_val in (animated_counts or {}).items():
        placeholder = "{" + count_key + "}"
        ph_idx = text.find(placeholder)
        if ph_idx == -1:
            continue
        count_text, count_ents_raw = _render_count(count_val, digit_map)
        prefix     = text[:ph_idx]
        suffix     = text[ph_idx + len(placeholder):]
        prefix_u16 = _utf16_len(prefix)
        ph_u16     = _utf16_len(placeholder)
        count_u16  = _utf16_len(count_text)
        delta      = count_u16 - ph_u16
        sfx_start  = prefix_u16 + ph_u16

        new_ents = []
        for e in working_ents:
            e2 = dict(e)
            if e2.get("offset", 0) >= sfx_start:
                e2["offset"] = e2["offset"] + delta
            new_ents.append(e2)
        for ce in count_ents_raw:
            new_ents.append({**ce, "offset": ce.get("offset", 0) + prefix_u16})
        new_ents.sort(key=lambda e: e.get("offset", 0))
        working_ents = new_ents
        text = prefix + count_text + suffix

    entities = _build_entities(working_ents) if working_ents else None
    if entities:
        await message.reply_text(text, entities=entities, reply_markup=reply_markup)
    else:
        await message.reply_text(text, reply_markup=reply_markup)


async def _send_total_plus(message, bot_data: dict,
                            entries: dict, grand_total: int, names: dict) -> None:
    """Build and send the complete total-plus counter as a single message.

    Header, row format, and total line are all customisable; counts inside
    rows and the grand total use animated digits when digit_map is set.
    """
    digit_map = _get_digit_map(bot_data)

    hdr_stored   = _get_custom_msgs(bot_data).get("total_plus_header", {})
    hdr_text     = hdr_stored.get("text") or DEFAULT_MSGS["total_plus_header"]
    hdr_ents_raw = list(hdr_stored.get("entities") or [])
    row_tmpl     = get_msg(bot_data, "total_plus_row")    # {i}, {name}, {count}
    grand_tmpl   = get_msg(bot_data, "total_plus_grand")  # {grand_total}

    text: str    = ""
    all_ents: list = []

    def _push(chunk: str, chunk_ents=None):
        nonlocal text
        base = _utf16_len(text)
        if chunk_ents:
            for e in chunk_ents:
                all_ents.append({**dict(e), "offset": e.get("offset", 0) + base})
        text += chunk

    def _push_count(tmpl: str, count_key: str, count_val: int, **pre_fmt):
        """Substitute pre_fmt then replace {count_key} with animated digits."""
        pre = _safe_substitute(tmpl, **pre_fmt)
        chunk, chunk_ents = _insert_animated_count(pre, count_val, count_key, digit_map)
        _push(chunk, chunk_ents)

    # --- Header ---
    _push(hdr_text, hdr_ents_raw)
    _push("\n\n")

    # --- Rows ---
    items = list(entries.items())
    for j, (uid, cnt) in enumerate(items, 1):
        name = names.get(uid, str(uid))
        _push_count(row_tmpl, "count", cnt, i=j, name=name)
        if j < len(items):
            _push("\n")

    # --- Grand total ---
    _push("\n\n")
    _push_count(grand_tmpl, "grand_total", grand_total)

    entities = _build_entities(all_ents) if all_ents else None
    if entities:
        await message.reply_text(text, entities=entities)
    else:
        await message.reply_text(text)


# ============================================================
# MONGODB CONNECTION
# ============================================================
_mongo_client = None
_mongo_db = None

def get_mongo_db():
    global _mongo_client, _mongo_db
    if _mongo_db is not None:
        return _mongo_db
    uri = os.getenv("MONGO_URI")
    if not uri:
        logging.warning("MONGO_URI not set — MongoDB disabled")
        return None
    try:
        _mongo_client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        _mongo_client.admin.command("ping")
        _mongo_db = _mongo_client["deposit_bot"]
        logging.info("MongoDB connected OK")
    except Exception as e:
        logging.warning(f"MongoDB connection failed: {e}")
        _mongo_db = None
    return _mongo_db


# ============================================================
# PLUS COUNTER DATA  (MongoDB-backed, JSON fallback)
# ============================================================
PLUS_DATA_FILE = os.path.join(os.path.dirname(__file__), 'plus_data.json')

plus_counters: dict = {}
plus_names: dict = {}
plus_counted_msgs: dict = {}

# Green alert daily counter — (chat_id, date_key) -> count  (in-memory only, resets each day)
GREEN_ALERT_THRESHOLD = 10  # start alerting from the (THRESHOLD+1)-th submission
green_counters: dict = {}


def _plus_key_to_str(key: tuple) -> str:
    return f"{key[0]}:{key[1]}"


def _str_to_plus_key(s: str) -> tuple:
    parts = s.split(":", 1)
    return (int(parts[0]), int(parts[1]))


def save_plus_data() -> None:
    data = {
        "counters":     {_plus_key_to_str(k): v for k, v in plus_counters.items()},
        "names":        {str(k): v for k, v in plus_names.items()},
        "counted_msgs": {_plus_key_to_str(k): v for k, v in plus_counted_msgs.items()},
    }
    # MongoDB save
    db = get_mongo_db()
    if db is not None:
        try:
            db["plus_data"].replace_one({"_id": "plus_data"}, {"_id": "plus_data", **data}, upsert=True)
        except PyMongoError as e:
            logging.warning(f"MongoDB save_plus_data error: {e}")
    # JSON fallback
    try:
        with open(PLUS_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"JSON save_plus_data error: {e}")


def load_plus_data() -> None:
    global plus_counters, plus_names, plus_counted_msgs
    data = None
    # Try MongoDB first
    db = get_mongo_db()
    if db is not None:
        try:
            doc = db["plus_data"].find_one({"_id": "plus_data"})
            if doc:
                data = doc
                logging.info("plus_data loaded from MongoDB")
        except PyMongoError as e:
            logging.warning(f"MongoDB load_plus_data error: {e}")
    # Fallback to JSON
    if data is None:
        try:
            with open(PLUS_DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            logging.info("plus_data loaded from JSON fallback")
        except FileNotFoundError:
            data = {}
        except Exception as e:
            logging.warning(f"JSON load_plus_data error: {e}")
            data = {}
    plus_counters     = {_str_to_plus_key(k): v for k, v in data.get("counters", {}).items()}
    plus_names        = {int(k): v for k, v in data.get("names", {}).items()}
    raw_msgs = data.get("counted_msgs", {})
    plus_counted_msgs = {} if isinstance(raw_msgs, list) else {_str_to_plus_key(k): v for k, v in raw_msgs.items()}
    logging.info(f"plus_data ready: {len(plus_counters)} counters, {len(plus_counted_msgs)} msgs")


load_plus_data()


# ============================================================
# DATA MSG MAP  (MongoDB-backed, JSON fallback)
# ============================================================
DATA_MSG_MAP_FILE = os.path.join(os.path.dirname(__file__), 'data_msg_map.json')
data_msg_map: dict = {}


def _data_key_to_str(key: tuple) -> str:
    return f"{key[0]}:{key[1]}"


def _str_to_data_key(s: str) -> tuple:
    parts = s.split(":", 1)
    return (int(parts[0]), int(parts[1]))


# ============================================================
# BOT CONFIG  (custom_msgs + start_buttons → MongoDB)
# ============================================================

def save_bot_config_to_mongo(bot_data: dict) -> None:
    """Persist custom_msgs and start_buttons to MongoDB bot_config collection."""
    db = get_mongo_db()
    if db is None:
        return
    try:
        payload = {
            "_id":          "bot_config",
            "custom_msgs":  bot_data.get("custom_msgs", {}),
            "start_buttons": bot_data.get("start_buttons", []),
        }
        db["bot_config"].replace_one({"_id": "bot_config"}, payload, upsert=True)
        logging.info("bot_config saved to MongoDB")
    except PyMongoError as e:
        logging.warning(f"MongoDB save_bot_config error: {e}")


def load_bot_config_from_mongo(bot_data: dict) -> None:
    """Restore custom_msgs and start_buttons from MongoDB into bot_data (in-place)."""
    db = get_mongo_db()
    if db is None:
        return
    try:
        doc = db["bot_config"].find_one({"_id": "bot_config"})
        if doc:
            if "custom_msgs" in doc:
                bot_data["custom_msgs"] = doc["custom_msgs"]
                logging.info(f"bot_config: restored {len(doc['custom_msgs'])} custom_msgs from MongoDB")
            if "start_buttons" in doc:
                bot_data["start_buttons"] = doc["start_buttons"]
                logging.info(f"bot_config: restored {len(doc['start_buttons'])} start_buttons from MongoDB")
        else:
            logging.info("bot_config: no saved config in MongoDB (first run)")
    except PyMongoError as e:
        logging.warning(f"MongoDB load_bot_config error: {e}")


def save_data_msg_map() -> None:
    serializable = {_data_key_to_str(k): v for k, v in data_msg_map.items()}
    # MongoDB save
    db = get_mongo_db()
    if db is not None:
        try:
            db["data_msg_map"].replace_one(
                {"_id": "data_msg_map"},
                {"_id": "data_msg_map", "entries": serializable},
                upsert=True
            )
        except PyMongoError as e:
            logging.warning(f"MongoDB save_data_msg_map error: {e}")
    # JSON fallback
    try:
        with open(DATA_MSG_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False)
    except Exception as e:
        logging.warning(f"JSON save_data_msg_map error: {e}")


def load_data_msg_map() -> None:
    global data_msg_map
    raw = None
    # Try MongoDB first
    db = get_mongo_db()
    if db is not None:
        try:
            doc = db["data_msg_map"].find_one({"_id": "data_msg_map"})
            if doc:
                raw = doc.get("entries", {})
                logging.info("data_msg_map loaded from MongoDB")
        except PyMongoError as e:
            logging.warning(f"MongoDB load_data_msg_map error: {e}")
    # Fallback to JSON
    if raw is None:
        try:
            with open(DATA_MSG_MAP_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            logging.info("data_msg_map loaded from JSON fallback")
        except FileNotFoundError:
            raw = {}
        except Exception as e:
            logging.warning(f"JSON load_data_msg_map error: {e}")
            raw = {}
    data_msg_map = {_str_to_data_key(k): v for k, v in raw.items()}
    logging.info(f"data_msg_map ready: {len(data_msg_map)} entries")


load_data_msg_map()


# ============================================================
# GROUP DATA HELPERS  (MongoDB-backed, pickle fallback via bot_data)
# ============================================================
def mg_save_group_data(chat_id: str, date_key: str, entries: list) -> None:
    """Save a single chat+date entries list to MongoDB."""
    db = get_mongo_db()
    if db is None:
        return
    try:
        db["group_data"].replace_one(
            {"_id": f"{chat_id}:{date_key}"},
            {"_id": f"{chat_id}:{date_key}", "chat_id": chat_id, "date_key": date_key, "entries": entries},
            upsert=True
        )
    except PyMongoError as e:
        logging.warning(f"MongoDB mg_save_group_data error: {e}")


def mg_load_group_data(chat_id: str, date_key: str) -> list:
    """Load entries for a single chat+date from MongoDB."""
    db = get_mongo_db()
    if db is None:
        return []
    try:
        doc = db["group_data"].find_one({"_id": f"{chat_id}:{date_key}"})
        return doc["entries"] if doc else []
    except PyMongoError as e:
        logging.warning(f"MongoDB mg_load_group_data error: {e}")
        return []


def mg_delete_group_data(chat_id: str, date_key: str) -> None:
    """Delete a chat+date from MongoDB."""
    db = get_mongo_db()
    if db is None:
        return
    try:
        db["group_data"].delete_one({"_id": f"{chat_id}:{date_key}"})
    except PyMongoError as e:
        logging.warning(f"MongoDB mg_delete_group_data error: {e}")


def mg_delete_all_group_data(chat_id: str) -> None:
    """Delete all dates for a chat from MongoDB."""
    db = get_mongo_db()
    if db is None:
        return
    try:
        db["group_data"].delete_many({"chat_id": chat_id})
    except PyMongoError as e:
        logging.warning(f"MongoDB mg_delete_all_group_data error: {e}")


def get_all_duplicate_ids() -> list:
    """Return list of duplicate entry IDs from MongoDB group_data.
    Each item: {'id': str, 'poster_count': int, 'posters': list[str]}
    """
    db = get_mongo_db()
    if db is None:
        return []
    try:
        seen = {}
        for doc in db["group_data"].find({}, {"entries": 1, "chat_id": 1}):
            chat_id = str(doc.get("chat_id", "unknown"))
            for entry in doc.get("entries", []):
                if not isinstance(entry, str):
                    continue
                if entry not in seen:
                    seen[entry] = []
                if chat_id not in seen[entry]:
                    seen[entry].append(chat_id)
        dupes = []
        for entry, chat_ids in seen.items():
            if len(chat_ids) > 1:
                dupes.append({
                    "id": entry,
                    "poster_count": len(chat_ids),
                    "posters": chat_ids,
                })
        return dupes
    except PyMongoError as e:
        logging.warning(f"MongoDB get_all_duplicate_ids error: {e}")
        return []


# ============================================================
# REPORT TEMPLATE
# ============================================================
REPORT_TEMPLATE = (
    "Gmail        - \n"
    "  \n"
    "Tele name    - \n"
    "    \n"
    "Username    - \n"
    "    \n"
    "Date        - \n"
    "    \n"
    "Age         - \n"
    "    \n"
    "Current work - \n"
    "    \n"
    "Phone number      - \n"
    "\n"
    "\n"
    "Khaifa - "
)


def get_yangon_tz() -> pytz.timezone:
    return pytz.timezone('Asia/Yangon')


def get_data_key() -> str:
    try:
        tz = get_yangon_tz()
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()

    cut_off_time = time(hour=12, minute=0, second=0)

    if now.time() < cut_off_time:
        work_day = now.date() - timedelta(days=1)
    else:
        work_day = now.date()

    return work_day.strftime('%Y-%m-%d')


get_today_key = get_data_key


async def save_chat_id(chat_id: int, context: CallbackContext, chat_type: str) -> None:
    if 'users' not in context.application.bot_data:
        context.application.bot_data['users'] = set()
    if 'groups' not in context.application.bot_data:
        context.application.bot_data['groups'] = set()

    if chat_type == 'private' and chat_id not in context.application.bot_data['users']:
        context.application.bot_data['users'].add(chat_id)
    elif chat_type in ['group', 'supergroup'] and chat_id not in context.application.bot_data['groups']:
        context.application.bot_data['groups'].add(chat_id)

    if context.application.persistence:
        await context.application.persistence.flush()


# ============================================================
# ADMIN ERROR NOTIFICATION
# ============================================================

async def notify_admins_error(context: CallbackContext, error_text: str) -> None:
    msg = (
        f"⚠️ <b>Bot Error Alert</b>\n\n"
        f"<pre>{error_text[:3000]}</pre>"
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.application.bot.send_message(
                chat_id=admin_id,
                text=msg,
                parse_mode='HTML'
            )
        except Exception as e:
            logging.warning(f"notify_admins_error: could not reach {admin_id}: {e}")


# ============================================================
# MATH CALCULATOR (PM only)
# ============================================================

def _safe_eval_math(expr: str):
    expr = expr.strip()
    expr = expr.replace('×', '*').replace('÷', '/').replace('^', '**')
    expr = expr.replace(',', '')

    allowed_names = {k: getattr(math, k) for k in dir(math) if not k.startswith('_')}
    allowed_names.update({'abs': abs, 'round': round, 'int': int, 'float': float})

    try:
        code = compile(expr, '<string>', 'eval')
        result = eval(code, {"__builtins__": {}}, allowed_names)
        return result
    except ZeroDivisionError:
        raise ValueError("Division by zero")
    except Exception:
        raise ValueError("Invalid expression")


def _looks_like_math(text: str) -> bool:
    text = text.strip()
    if re.match(r'^[\d\s\+\-\*\/\(\)\.\,\%\^×÷]+$', text):
        if re.search(r'\d', text) and re.search(r'[\+\-\*\/\^×÷]', text):
            return True
    if re.match(r'^[\d\s\(\)]+[\+\-\*\/\^×÷][\d\s\(\)\.]+', text):
        return True
    return False


async def handle_pm_math(update: Update, context: CallbackContext) -> None:
    msg = update.message
    if not msg or not msg.text:
        return
    if update.effective_chat.type != 'private':
        return

    text = msg.text.strip()
    if text.startswith('/'):
        return
    if not _looks_like_math(text):
        return

    try:
        result = _safe_eval_math(text)
        if isinstance(result, float):
            result_str = str(int(result)) if result == int(result) else f"{result:.10g}"
        else:
            result_str = str(result)

        await msg.reply_text(
            f"🧮 <b>{text} = {result_str}</b>",
            parse_mode='HTML'
        )
    except ValueError:
        pass
    except Exception:
        pass


# ============================================================
# COMMANDS
# ============================================================

async def start(update: Update, context: CallbackContext) -> None:
    await main_menu_command(update, context)


async def help_command(update: Update, context: CallbackContext) -> None:
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)
    await _reply_custom(update.message, context.application.bot_data, "help")


async def report_form_command(update: Update, context: CallbackContext) -> None:
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)
    intro = get_msg(context.application.bot_data, "form")
    await update.message.reply_text(intro + REPORT_TEMPLATE)


async def main_menu_command(update: Update, context: CallbackContext) -> None:
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)

    keyboard = [
        [KeyboardButton("/showdata"), KeyboardButton("/cleardata")],
        [KeyboardButton("/feedback"), KeyboardButton("/form")],
        [KeyboardButton("/total_plus"), KeyboardButton("/reset_plus")],
        [KeyboardButton("/guide"), KeyboardButton("/hidemenu")],
    ]

    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    user_name = update.effective_user.full_name if update.effective_user else "User"

    bot_username = context.bot.username

    # Load dynamic buttons; initialise defaults on first run
    if 'start_buttons' not in context.application.bot_data:
        context.application.bot_data['start_buttons'] = [
            {"text": "🔞 Blue Bot", "url": "https://t.me/blue_xxx69_bot?start=7157442403"},
            {"text": "📝 Note bot", "url": "https://t.me/chanmyae1539_bot?start=ref_7196380140"},
        ]
        if context.application.persistence:
            await context.application.persistence.flush()
        save_bot_config_to_mongo(context.application.bot_data)

    inline_rows = [
        [InlineKeyboardButton("➕ Add me to your chat!", url=f"https://t.me/{bot_username}?startgroup=true")],
    ]
    for btn in context.application.bot_data['start_buttons']:
        inline_rows.append([InlineKeyboardButton(btn['text'], url=btn['url'])])

    inline_kb = InlineKeyboardMarkup(inline_rows)
    await _reply_custom(
        update.message, context.application.bot_data, "welcome",
        reply_markup=inline_kb, name=user_name
    )

async def remove_menu(update: Update, context: CallbackContext) -> None:
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)
    await _reply_custom(
        update.message, context.application.bot_data, "hidemenu",
        reply_markup=ReplyKeyboardRemove()
    )


# ============================================================
# START BUTTON MANAGEMENT  (Admin only)
# ============================================================

async def addbutton_command(update: Update, context: CallbackContext) -> None:
    """Admin: /addbutton <text> | <url>"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    if update.effective_chat.type != 'private':
        await update.message.reply_text("❌ Bot PM ထဲတွင်သာ အသုံးပြုနိုင်သည်။")
        return

    raw = ' '.join(context.args).strip() if context.args else ''
    if '|' not in raw:
        await update.message.reply_text(
            "📌 <b>Usage:</b> /addbutton &lt;text&gt; | &lt;url&gt;\n\n"
            "Example:\n<code>/addbutton 🎮 Game Bot | https://t.me/gamebot</code>",
            parse_mode='HTML'
        )
        return

    parts = raw.split('|', 1)
    text = parts[0].strip()
    url  = parts[1].strip()

    if not text or not url:
        await update.message.reply_text("❌ Text နှင့် URL နှစ်ခုလုံး ထည့်ပေးပါ။")
        return
    if not (url.startswith('http://') or url.startswith('https://')):
        await update.message.reply_text("❌ URL သည် http:// သို့မဟုတ် https:// ဖြင့် စရမည်။")
        return

    buttons = context.application.bot_data.setdefault('start_buttons', [])
    buttons.append({"text": text, "url": url})
    if context.application.persistence:
        await context.application.persistence.flush()
    save_bot_config_to_mongo(context.application.bot_data)

    await update.message.reply_text(
        f"✅ Button ထည့်ပြီးပါပြီ!\n\n"
        f"🔘 <b>{text}</b>\n🔗 {url}\n\n"
        f"စုစုပေါင်း buttons: {len(buttons)} ခု",
        parse_mode='HTML'
    )


async def removebutton_command(update: Update, context: CallbackContext) -> None:
    """Admin: /removebutton — show inline keyboard to pick a button to delete"""
    if update.effective_user.id not in ADMIN_IDS:
        return
    if update.effective_chat.type != 'private':
        await update.message.reply_text("❌ Bot PM ထဲတွင်သာ အသုံးပြုနိုင်သည်။")
        return

    buttons = context.application.bot_data.get('start_buttons', [])
    if not buttons:
        await update.message.reply_text("ℹ️ ဖျက်ရမည့် custom buttons မရှိသေးပါ။")
        return

    keyboard = []
    for i, btn in enumerate(buttons):
        keyboard.append([InlineKeyboardButton(f"🗑 {btn['text']}", callback_data=f"rmbtn_{i}")])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data="rmbtn_cancel")])

    await update.message.reply_text(
        "🗑 <b>ဖျက်မည့် button ကိုရွေးပါ:</b>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )


async def removebutton_callback(update: Update, context: CallbackContext) -> None:
    """Callback for /removebutton inline keyboard."""
    query = update.callback_query
    await query.answer()

    if query.data == "rmbtn_cancel":
        await query.edit_message_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
        return

    try:
        idx = int(query.data[len("rmbtn_"):])
    except ValueError:
        await query.edit_message_text("❌ Invalid callback.")
        return

    buttons = context.application.bot_data.get('start_buttons', [])
    if idx >= len(buttons):
        await query.edit_message_text("❌ Button မတွေ့ပါ (ဖျက်ပြီးသား ဖြစ်နိုင်သည်)။")
        return

    removed = buttons.pop(idx)
    if context.application.persistence:
        await context.application.persistence.flush()
    save_bot_config_to_mongo(context.application.bot_data)

    await query.edit_message_text(
        f"✅ Button ဖျက်ပြီးပါပြီ!\n\n🗑 <b>{removed['text']}</b>\n🔗 {removed['url']}\n\n"
        f"ကျန် buttons: {len(buttons)} ခု",
        parse_mode='HTML'
    )


async def listbuttons_command(update: Update, context: CallbackContext) -> None:
    """Admin: /listbuttons — show all current start buttons"""
    if update.effective_user.id not in ADMIN_IDS:
        return

    buttons = context.application.bot_data.get('start_buttons', [])
    if not buttons:
        await update.message.reply_text("ℹ️ Custom buttons မရှိသေးပါ။")
        return

    lines = [f"🔘 <b>Start Message Buttons</b> ({len(buttons)} ခု)\n"]
    for i, btn in enumerate(buttons, 1):
        lines.append(f"{i}. <b>{btn['text']}</b>\n   🔗 {btn['url']}")

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode='HTML',
        disable_web_page_preview=True
    )


async def clear_data(update: Update, context: CallbackContext) -> None:
    chat_id = str(update.effective_chat.id)
    today_key = get_data_key()
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)

    # Check MongoDB first, then pickle fallback
    mg_entries = mg_load_group_data(chat_id, today_key)
    pickle_has = (
        'group_data' in context.application.bot_data
        and chat_id in context.application.bot_data['group_data']
        and today_key in context.application.bot_data['group_data'][chat_id]
    )
    if mg_entries or pickle_has:
        # Delete from MongoDB
        mg_delete_group_data(chat_id, today_key)
        # Delete from pickle
        if pickle_has:
            del context.application.bot_data['group_data'][chat_id][today_key]
            if context.application.persistence:
                await context.application.persistence.flush()

        for k in [k for k in plus_counters if k[0] == int(chat_id)]:
            del plus_counters[k]
        for k in [k for k in plus_counted_msgs if k[0] == int(chat_id)]:
            del plus_counted_msgs[k]
        save_plus_data()

        for k in [k for k in data_msg_map if k[0] == int(chat_id)]:
            del data_msg_map[k]
        save_data_msg_map()

        await _reply_custom(update.message, context.application.bot_data,
                            "cleardata_ok", today=today_key)
    else:
        await _reply_custom(update.message, context.application.bot_data,
                            "cleardata_empty", today=today_key)


async def admin_clearall_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    if update.effective_chat.type != 'private':
        await update.message.reply_text("❌ Bot PM ထဲတွင်သာ အသုံးပြုနိုင်သည်။")
        return

    today_key = get_data_key()
    group_data = context.application.bot_data.get('group_data', {})
    db = get_mongo_db()
    mg_count = 0
    if db is not None:
        try:
            mg_count = db["group_data"].count_documents({"date_key": today_key})
        except Exception:
            pass
    pickle_count = sum(1 for d in group_data.values() if today_key in d)
    total_count = max(mg_count, pickle_count)
    has_data = total_count > 0

    if not has_data:
        await update.message.reply_text(f"ℹ️ ယနေ့ ({today_key}) ရှင်းလင်းစရာ data မရှိပါ။")
        return

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ အတည်ပြုရှင်းလင်းမည်", callback_data="adminall_clear_confirm"),
        InlineKeyboardButton("❌ မလုပ်တော့ပါ", callback_data="adminall_cancel"),
    ]])
    await update.message.reply_text(
        f"⚠️ <b>Group အားလုံး ရှင်းလင်းမည်</b>\n\n"
        f"ယနေ့ ({today_key}) data ရှိသော group <b>{total_count}</b> ခု ကို ရှင်းမည်။\nဆက်လုပ်မည်လား?",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def admin_resetplusall_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    if update.effective_chat.type != 'private':
        await update.message.reply_text("❌ Bot PM ထဲတွင်သာ အသုံးပြုနိုင်သည်။")
        return

    if not plus_counters:
        await update.message.reply_text("ℹ️ ရှင်းလင်းစရာ Plus counter မရှိပါ။")
        return

    chat_count = len(set(k[0] for k in plus_counters))
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ အတည်ပြု Reset မည်", callback_data="adminall_resetplus_confirm"),
        InlineKeyboardButton("❌ မလုပ်တော့ပါ", callback_data="adminall_cancel"),
    ]])
    await update.message.reply_text(
        f"⚠️ <b>Group အားလုံး Plus Counter Reset မည်</b>\n\nGroup <b>{chat_count}</b> ခု ကို reset မည်။\nဆက်လုပ်မည်လား?",
        parse_mode='HTML',
        reply_markup=keyboard
    )


async def adminall_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        await query.edit_message_text("❌ Admin သာ ဤ action ကို ပြုလုပ်နိုင်သည်။")
        return

    data = query.data

    if data == "adminall_cancel":
        await query.edit_message_text("❌ ပယ်ဖျက်လိုက်ပါသည်။")
        return

    if data == "adminall_clear_confirm":
        today_key = get_data_key()
        group_data = context.application.bot_data.get('group_data', {})
        cleared_groups = 0

        # Clear from MongoDB
        db = get_mongo_db()
        if db is not None:
            try:
                result = db["group_data"].delete_many({"date_key": today_key})
                cleared_groups = result.deleted_count
            except Exception as e:
                logging.warning(f"MongoDB adminall clear error: {e}")

        # Clear from pickle fallback
        for chat_id_str, days in list(group_data.items()):
            if today_key in days:
                del days[today_key]
                chat_id_int = int(chat_id_str)
                for k in [k for k in plus_counters if k[0] == chat_id_int]:
                    del plus_counters[k]
                for k in [k for k in plus_counted_msgs if k[0] == chat_id_int]:
                    del plus_counted_msgs[k]
                for k in [k for k in data_msg_map if k[0] == chat_id_int]:
                    del data_msg_map[k]
                if cleared_groups == 0:
                    cleared_groups += 1

        save_plus_data()
        save_data_msg_map()
        if context.application.persistence:
            await context.application.persistence.flush()

        await query.edit_message_text(
            f"✅ <b>ရှင်းလင်းမှု ပြီးပါပြီ</b>\n\nGroup <b>{cleared_groups}</b> ခု၏ ယနေ့ ({today_key}) data ရှင်းပြီးပါပြီ။",
            parse_mode='HTML'
        )
        return

    if data == "adminall_resetplus_confirm":
        chat_count = len(set(k[0] for k in plus_counters))
        key_count = len(list(plus_counters.keys()))
        for k in list(plus_counters.keys()):
            del plus_counters[k]
        for k in list(plus_counted_msgs.keys()):
            del plus_counted_msgs[k]
        save_plus_data()

        await query.edit_message_text(
            f"✅ <b>Plus Counter Reset ပြီးပါပြီ</b>\n\nGroup <b>{chat_count}</b> ခု (entries <b>{key_count}</b>) reset ပြုလုပ်ပြီးပါပြီ။",
            parse_mode='HTML'
        )
        return


async def show_data(update: Update, context: CallbackContext) -> None:
    chat_id = str(update.effective_chat.id)
    today_key = get_data_key()
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)

    # Load from MongoDB first, fallback to pickle
    collected_data_list = mg_load_group_data(chat_id, today_key)
    if not collected_data_list:
        collected_data_list = context.application.bot_data.get('group_data', {}).get(chat_id, {}).get(today_key, [])

    if not collected_data_list:
        await _reply_custom(update.message, context.application.bot_data,
                            "showdata_empty", today=today_key)
        return

    grouped_data: dict = {}
    for entry in collected_data_list:
        parts = entry.split('    ')
        khaifa_name = parts[1].strip() if len(parts) >= 2 else "N/A"
        key = khaifa_name.replace(" ", "").lower()
        grouped_data.setdefault(key, []).append(entry)

    parts_list = []
    separator = "------------------------------------"
    for i, (_, entries) in enumerate(sorted(grouped_data.items())):
        if i > 0:
            parts_list.append(separator)
        parts_list.append("\n\n".join(entries))

    response_text = "\n".join(parts_list)

    if len(response_text) > 4096:
        await update.message.reply_text("Warning: Data too long. Partial display:")
        await update.message.reply_text(response_text[:4000])
    else:
        await update.message.reply_text(response_text)

    _u = update.effective_user
    _mention = f"@{_u.username}" if (_u and _u.username) else (_u.full_name if _u else "User")
    await _reply_custom(update.message, context.application.bot_data,
                        "showdata_footer", parse_mode='HTML', mention=_mention)


async def extract_and_save_data(update: Update, context: CallbackContext) -> None:
    if not update.message:
        return

    chat_id = str(update.effective_chat.id)
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)

    full_text = update.message.text or update.message.caption
    if not full_text:
        return

    required_fields_present = all(
        re.search(field, full_text, re.IGNORECASE)
        for field in ["Khaifa", "Date"]
    )
    if not required_fields_present:
        return

    khaifa_match = re.search(r"(?:Khaifa|Khat)\s*[-\]]?\s*(.+?)(?:\r?\n|$)", full_text, re.IGNORECASE | re.DOTALL)
    extracted_khaifa = khaifa_match.group(1).strip() if khaifa_match else "N/A"

    date_match = re.search(r"Date\s*[-\]]?\s*(.+?)(?:\n|$)", full_text, re.IGNORECASE | re.DOTALL)
    extracted_date = date_match.group(1).strip() if date_match else "N/A"

    email_phone_match = re.search(r"(?:Gmail|Email|Phone number|Phone)\s*[-\]]?\s*(.+?)(?:\n|$)", full_text, re.IGNORECASE | re.DOTALL)
    extracted_email_phone = email_phone_match.group(1).strip() if email_phone_match else "N/A"

    stored_entry = f"{extracted_date}    {extracted_khaifa}    {extracted_email_phone}"

    today_key = get_data_key()

    # Save to MongoDB (primary)
    existing = mg_load_group_data(chat_id, today_key)
    existing.append(stored_entry)
    mg_save_group_data(chat_id, today_key, existing)

    # Also keep pickle in sync (fallback)
    if 'group_data' not in context.application.bot_data:
        context.application.bot_data['group_data'] = {}
    if chat_id not in context.application.bot_data['group_data']:
        context.application.bot_data['group_data'][chat_id] = {}
    if today_key not in context.application.bot_data['group_data'][chat_id]:
        context.application.bot_data['group_data'][chat_id][today_key] = []
    context.application.bot_data['group_data'][chat_id][today_key].append(stored_entry)

    if context.application.persistence:
        await context.application.persistence.flush()

    sent_msg = await update.message.reply_text(stored_entry)

    # Track bot reply msg so (-) reply can delete this entry
    data_msg_map[(int(chat_id), sent_msg.message_id)] = {
        "entry": stored_entry,
        "date_key": today_key,
        "chat_id": chat_id,
    }
    save_data_msg_map()

# ============================================================
# FEEDBACK
# ============================================================

async def start_feedback(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text(
        "Admin ထံ စာပေးပို့နိုင်ပါသည်။\n\n(ရပ်လိုပါက /cancel)"
    )
    return FEEDBACK_AWAITING


async def process_feedback(update: Update, context: CallbackContext) -> int:
    user = update.effective_user
    feedback_text = update.message.text

    for admin_id in ADMIN_IDS:
        try:
            await context.application.bot.send_message(
                chat_id=admin_id,
                text=f"📩 <b>[NEW FEEDBACK]</b>\nFrom: {user.full_name} (@{user.username} - ID: {user.id})\n\n{feedback_text}",
                parse_mode='HTML'
            )
        except Exception:
            pass

    await update.message.reply_text("သင်၏ မှတ်ချက်ကို Admin ထံ ပေးပို့ပြီးပါပြီ။")
    return ConversationHandler.END


async def cancel_conversation(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text('❌ Action cancelled.')
    return ConversationHandler.END


# ============================================================
# BROADCAST
# ============================================================

async def broadcast_start(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Admin only.")
        return ConversationHandler.END

    users = context.application.bot_data.get('users', set())
    groups = context.application.bot_data.get('groups', set())

    if not users and not groups:
        await update.message.reply_text("No tracked users or groups found.")
        return ConversationHandler.END

    keyboard = []
    for user_id in sorted(list(users)):
        try:
            user = await context.application.bot.get_chat(chat_id=user_id)
            name = user.full_name or f"User {user_id}"
        except Exception:
            name = f"User {user_id}"
        keyboard.append([InlineKeyboardButton(f"👤 {name} ({user_id})", callback_data=f'bcast_id_{user_id}')])

    for group_id in sorted(list(groups)):
        try:
            chat = await context.application.bot.get_chat(chat_id=group_id)
            name = chat.title or f"Group {group_id}"
        except Exception:
            name = f"Group {group_id}"
        keyboard.append([InlineKeyboardButton(f"👥 {name} ({group_id})", callback_data=f'bcast_id_{group_id}')])

    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data='bcast_cancel')])

    await update.message.reply_text(
        "📢 Broadcast — ပေးပို့မည့် chat ရွေးချယ်ပါ:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return BROADCAST_SELECT_CHAT


async def broadcast_select_chat(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    target_id_str = query.data[len('bcast_id_'):]
    context.user_data['target_broadcast_id'] = target_id_str

    try:
        chat = await context.application.bot.get_chat(chat_id=target_id_str)
        context.user_data['target_name'] = chat.title or chat.full_name
    except Exception:
        context.user_data['target_name'] = f"Chat {target_id_str}"

    await query.edit_message_text(
        f"✅ <b>{context.user_data['target_name']}</b> သို့ ပေးပို့ရန် ရွေးပြီး။\n\nMessage ကို forward (သို့) ရိုက်ထည့်ပါ။\n(/cancel ဖြင့် ရပ်နိုင်)",
        parse_mode='HTML'
    )
    return BROADCAST_AWAITING_MESSAGE


async def broadcast_await_message(update: Update, context: CallbackContext) -> int:
    msg = update.message
    context.user_data['broadcast_msg_id'] = msg.message_id
    context.user_data['broadcast_from_chat'] = msg.chat_id
    target_name = context.user_data.get('target_name', 'Chat')

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm Send", callback_data='bcast_confirm')],
        [InlineKeyboardButton("❌ Cancel", callback_data='bcast_cancel')]
    ])
    await msg.reply_text(
        f"📨 <b>{target_name}</b> သို့ ပေးပို့ရန် သေချာပါသလား?",
        reply_markup=keyboard,
        parse_mode='HTML'
    )
    return BROADCAST_CONFIRMATION


async def broadcast_confirm(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    target_id = context.user_data.pop('target_broadcast_id', None)
    msg_id = context.user_data.pop('broadcast_msg_id', None)
    from_chat = context.user_data.pop('broadcast_from_chat', None)
    target_name = context.user_data.pop('target_name', 'Unknown')

    if not target_id or not msg_id or not from_chat:
        await query.edit_message_text("❌ အချက်အလက်မပြည့်စုံ။")
        return ConversationHandler.END

    try:
        await context.application.bot.copy_message(
            chat_id=target_id, from_chat_id=from_chat, message_id=msg_id
        )
        await query.edit_message_text(f"✅ <b>{target_name}</b> ထံ ပေးပို့ပြီးပါပြီ။", parse_mode='HTML')
    except Exception as e:
        await query.edit_message_text(f"❌ Error: {e}")

    return ConversationHandler.END


async def broadcast_cancel(update: Update, context: CallbackContext) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Broadcast ဖျက်သိမ်းလိုက်ပါသည်။")
    elif update.message:
        await update.message.reply_text("❌ Broadcast cancelled.")
    for key in ['target_broadcast_id', 'broadcast_msg_id', 'broadcast_from_chat', 'target_name']:
        context.user_data.pop(key, None)
    return ConversationHandler.END


# ============================================================
# ADMIN PANEL
# ============================================================

async def list_users(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return

    users = context.application.bot_data.get('users', set())
    if not users:
        await update.message.reply_text("👤 PM user မရှိသေးပါ။")
        return

    lines = [f"👤 <b>PM Users</b> ({len(users)} ဦး)\n"]
    for uid in sorted(list(users)):
        try:
            chat = await context.application.bot.get_chat(chat_id=uid)
            name = chat.full_name or f"User {uid}"
            username = f" (@{chat.username})" if chat.username else ""
            lines.append(f"• {name}{username} — <code>{uid}</code>")
        except Exception:
            lines.append(f"• User <code>{uid}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode='HTML')


async def list_groups(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return

    groups = context.application.bot_data.get('groups', set())
    if not groups:
        await update.message.reply_text("Bot က group မှာ မရှိသေးပါ။")
        return

    await update.message.reply_text("📋 Tracked Groups:")
    for group_id in list(groups):
        try:
            chat = await context.application.bot.get_chat(chat_id=group_id)
            group_name = chat.title
        except Exception:
            group_name = "Unknown Group"

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("🗑️ Clear Data", callback_data=f'admin_clear_{group_id}'),
            InlineKeyboardButton("❌ Cancel", callback_data='admin_cancel')
        ]])
        await context.application.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"<b>{group_name}</b> ({group_id})",
            reply_markup=keyboard,
            parse_mode='HTML'
        )


async def clear_group_data_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        await query.edit_message_text("Admin only.")
        return

    group_id_to_clear = query.data.split('_')[2]
    chat_id_str = str(group_id_to_clear)

    mg_delete_all_group_data(chat_id_str)
    if 'group_data' in context.application.bot_data and chat_id_str in context.application.bot_data['group_data']:
        del context.application.bot_data['group_data'][chat_id_str]
        if context.application.persistence:
            await context.application.persistence.flush()
        try:
            chat = await context.application.bot.get_chat(chat_id=group_id_to_clear)
            group_name = chat.title
        except Exception:
            group_name = "Unknown Group"
        await query.edit_message_text(f"✅ {group_name} ({group_id_to_clear}) data ရှင်းပြီးပါပြီ။")
    else:
        await query.edit_message_text(f"No data for group {group_id_to_clear}.")


async def cancel_group_action(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Cancelled.")


async def stats(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return

    user_count = len(context.application.bot_data.get('users', set()))
    group_count = len(context.application.bot_data.get('groups', set()))

    duplicates = get_all_duplicate_ids()
    dup_count = len(duplicates)

    dup_preview = ""
    if duplicates:
        top5 = duplicates[:5]
        lines = []
        for d in top5:
            lines.append(f"  • <code>{d['id']}</code> ({d['poster_count']} posters)")
        dup_preview = "\n\n🔁 <b>Top Duplicate IDs:</b>\n" + "\n".join(lines)
        if dup_count > 5:
            dup_preview += f"\n  ... နှင့် {dup_count - 5} ခု ထပ်ရှိ"

    await update.message.reply_text(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👤 Users (PM): {user_count}\n"
        f"👥 Groups: {group_count}\n\n"
        f"🆔 Duplicate IDs: {dup_count}"
        f"{dup_preview}",
        parse_mode='HTML'
    )


async def admin_command(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Stats", callback_data='adm_stats'),
         InlineKeyboardButton("👥 Groups", callback_data='adm_groups')],
        [InlineKeyboardButton("📢 Broadcast", callback_data='adm_broadcast')],
        [InlineKeyboardButton("🆔 Duplicate IDs", callback_data='adm_duplicates')],
        [InlineKeyboardButton("⚙️ Bot Settings", callback_data='adm_botsettings')],
        [InlineKeyboardButton("❌ Close", callback_data='adm_close')],
    ])
    await update.message.reply_text("🔧 <b>Admin Panel</b>", parse_mode='HTML', reply_markup=keyboard)


async def admin_panel_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()

    if query.from_user.id not in ADMIN_IDS:
        await query.edit_message_text("❌ Admin only.")
        return

    data = query.data
    if data == 'adm_close':
        await query.edit_message_text("✅ Closed.")
    elif data == 'adm_stats':
        user_count = len(context.application.bot_data.get('users', set()))
        group_count = len(context.application.bot_data.get('groups', set()))
        dup_count = len(get_all_duplicate_ids())
        await query.edit_message_text(
            f"📊 Users: {user_count}\nGroups: {group_count}\n🔁 Duplicates: {dup_count}"
        )
    elif data == 'adm_groups':
        groups = context.application.bot_data.get('groups', set())
        await query.edit_message_text(f"👥 Groups: {len(groups)}\n/listgroups ဖြင့် details ကြည့်ပါ။")
    elif data == 'adm_broadcast':
        await query.edit_message_text("📢 /broadcast command သုံးပါ။")
    elif data == 'adm_duplicates':
        duplicates = get_all_duplicate_ids()
        if not duplicates:
            await query.edit_message_text("✅ Duplicate ID မရှိပါ။")
        else:
            lines = [f"🔁 <b>Duplicate IDs</b> ({len(duplicates)} ခု)\n"]
            for i, dup in enumerate(duplicates[:10], 1):
                poster_names = ", ".join(dup['posters'])
                lines.append(f"{i}. <code>{dup['id']}</code> — {dup['poster_count']} posters\n   {poster_names}")
            if len(duplicates) > 10:
                lines.append(f"... နှင့် {len(duplicates) - 10} ခု ထပ်ရှိ")
            await query.edit_message_text("\n".join(lines), parse_mode='HTML')
    elif data == 'adm_botsettings':
        await _bot_settings_inline(query, context)


async def _bot_settings_inline(query, context: CallbackContext) -> int:
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton('✏️ Bot Name', callback_data='admbs_name'),
            InlineKeyboardButton('📝 Short About', callback_data='admbs_about'),
        ],
        [
            InlineKeyboardButton('📄 Description', callback_data='admbs_desc'),
        ],
        [
            InlineKeyboardButton('🖼️ Profile Photo', callback_data='admbs_photo'),
        ],
        [InlineKeyboardButton('❌ Cancel', callback_data='admbs_cancel')],
    ])
    await query.edit_message_text(
        '⚙️ <b>Bot Settings</b>\n━━━━━━━━━━━━━━━━━━━━\n'
        'ပြောင်းလဲလိုသည့် setting ကိုနှိပ်ပါ:\n\n'
        '• <b>Bot Name</b> — Telegram တွင်ပြသောနာမည်\n'
        '• <b>Short About</b> — Profile အကျဉ်းချုပ်\n'
        '• <b>Description</b> — Bot ဖွင့်သောအခါ ပြသောဖော်ပြချက်\n'
        '• <b>Profile Photo</b> — Bot ၏ profile ပုံ ပြောင်းလဲ',
        parse_mode='HTML',
        reply_markup=keyboard
    )
    return BOT_SETTINGS_SELECT


async def bot_settings_menu(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    return await _bot_settings_inline(query, context)


async def bot_settings_select(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    field = query.data[len('admbs_'):]
    context.user_data['admbs_field'] = field
    if field == 'photo':
        await query.edit_message_text('🖼️ Bot profile ပုံ ပြောင်းလဲရန် ဓာတ်ပုံ တစ်ပုံ ပေးပို့ပါ:\n(/cancel ဖြင့် ရပ်)')
        return BOT_SETTINGS_PHOTO
    labels = {'name': 'Name', 'about': 'About', 'desc': 'Description'}
    await query.edit_message_text(f"✏️ New {labels.get(field, field)} ရိုက်ထည့်ပါ:\n(/cancel ဖြင့် ရပ်)")
    return BOT_SETTINGS_AWAITING


async def bot_settings_apply(update: Update, context: CallbackContext) -> int:
    field = context.user_data.pop('admbs_field', None)
    text = update.message.text.strip()
    try:
        if field == 'name':
            await context.application.bot.set_my_name(text)
        elif field == 'about':
            await context.application.bot.set_my_short_description(text)
        elif field == 'desc':
            await context.application.bot.set_my_description(text)
        labels = {'name': 'Name', 'about': 'Short About', 'desc': 'Description'}
        await update.message.reply_text(
            f"✅ Bot <b>{labels.get(field, field)}</b> ‘{text}’ သိုပြောင်ပြီးပီးပီးပာပြီးသည်၊",
            parse_mode='HTML'
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")
    return ConversationHandler.END


async def bot_settings_photo_receive(update: Update, context: CallbackContext) -> int:
    msg = update.message
    if not msg.photo:
        await msg.reply_text('❌ ဓာတ်ပုံ မဟုတ်ပါ။ ဓာတ်ပုံ တစ်ပုံ ပေးပို့ပါ:')
        return BOT_SETTINGS_PHOTO
    photo_file_id = msg.photo[-1].file_id
    try:
        photo_file = await context.application.bot.get_file(photo_file_id)
        photo_bytes = await photo_file.download_as_bytearray()
        response = requests.post(
            f'https://api.telegram.org/bot{TOKEN}/setMyPhoto',
            files={'photo': ('photo.jpg', bytes(photo_bytes), 'image/jpeg')}
        )
        if response.ok:
            await msg.reply_text('✅ Bot profile ပုံ ပြောင်းလဲပြီးပါပြီ! 🖼️')
        else:
            err_msg = response.json().get('description', 'Unknown error')
            await msg.reply_text(f'❌ Error: {err_msg}')
    except Exception as e:
        await msg.reply_text(f'❌ Error: {e}')
    return ConversationHandler.END


async def bot_settings_cancel(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
    return ConversationHandler.END


# ============================================================
# DEPOSIT REPORT SYSTEM
# ============================================================
DEPOSIT_REPORT_KEY = 'deposit_reports'
WHATSAPP_REPORT_KEY = 'whatsapp_reports'


def _make_deposit_entry(jie: float, shou: float, section: str, msg_id: int, user_id=None) -> dict:
    return {'jie': jie, 'shou': shou, 'section': section, 'msg_id': msg_id, 'user_id': user_id, 'parser_version': 2}


def _upsert_deposit_entry(day_list: list, entry: dict) -> None:
    uid = entry.get('user_id')
    mid = entry.get('msg_id')
    key_fn = (lambda o: o.get('user_id') == uid) if uid else (lambda o: o.get('msg_id') == mid)
    for i, old in enumerate(day_list):
        if key_fn(old):
            day_list[i] = entry
            return
    day_list.append(entry)


def _parse_number_field(pattern: str, chunk: str):
    m = re.search(pattern + r'\s*([^\n]+)', chunk)
    if not m:
        return None
    raw = m.group(1).strip()
    eq_m = re.search(r'=\s*(\d+(?:\.\d+)?)', raw)
    if eq_m:
        return float(eq_m.group(1))
    num_m = re.search(r'(\d+(?:\.\d+)?)', raw)
    return float(num_m.group(1)) if num_m else None


def _parse_number_field_strict(pattern: str, chunk: str):
    m = re.search(pattern + r'[^\S\n]*([^\n]*)', chunk)
    if not m:
        return None
    raw = m.group(1).strip()
    if not raw:
        return None
    num_m = re.search(r'^\s*(\d+(?:\.\d+)?)\s*$', raw)
    return float(num_m.group(1)) if num_m else None


def _parse_deposit_form(text: str):
    required_patterns = [r'接电报\s*[：:]', r'首冲\s*[：:]', r'👉\s*second', r'👉\s*third', r'👉\s*last']
    if not all(re.search(p, text, re.IGNORECASE) for p in required_patterns):
        return None

    second_m = re.search(r'👉\s*second', text, re.IGNORECASE)
    third_m = re.search(r'👉\s*third', text, re.IGNORECASE)
    last_m = re.search(r'👉\s*last', text, re.IGNORECASE)
    if not (second_m and third_m and last_m):
        return None

    slices = {
        'first': text[:second_m.start()],
        'second': text[second_m.start():third_m.start()],
        'third': text[third_m.start():last_m.start()],
        'last': text[last_m.start():],
    }

    def _extract(chunk):
        jie = _parse_number_field(r'接电报\s*[：:]', chunk)
        shou = _parse_number_field(r'首冲\s*[：:]', chunk)
        return (jie, shou) if jie is not None and shou is not None else None

    def _extract_strict(chunk):
        jie = _parse_number_field_strict(r'接电报\s*[：:]', chunk)
        shou = _parse_number_field_strict(r'首冲\s*[：:]', chunk)
        return (jie, shou) if jie is not None and shou is not None else None

    r = _extract_strict(slices['last'])
    if r:
        return r[0], r[1], 'last'
    for key in ['third', 'second', 'first']:
        r = _extract(slices[key])
        if r:
            return r[0], r[1], key
    return None


async def handle_deposit_report(update: Update, context: CallbackContext) -> None:
    msg = update.message
    if not msg:
        return
    text = msg.text or msg.caption or ''
    if '接电报' not in text or '首冲' not in text:
        return
    result = _parse_deposit_form(text)
    if not result:
        return
    jie, shou, section = result
    chat_id = str(update.effective_chat.id)
    today = get_data_key()
    reports = context.application.bot_data.setdefault(DEPOSIT_REPORT_KEY, {})
    chat_day = reports.setdefault(chat_id, {}).setdefault(today, [])
    _upsert_deposit_entry(chat_day, _make_deposit_entry(jie, shou, section, msg.message_id, msg.from_user.id if msg.from_user else None))
    if context.application.persistence:
        await context.application.persistence.flush()


async def handle_deposit_report_edit(update: Update, context: CallbackContext) -> None:
    msg = update.edited_message
    if not msg:
        return
    text = msg.text or msg.caption or ''
    if '接电报' not in text or '首冲' not in text:
        return
    user_id = msg.from_user.id if msg.from_user else None
    chat_id = str(update.effective_chat.id)
    today = get_data_key()
    reports = context.application.bot_data.setdefault(DEPOSIT_REPORT_KEY, {})
    day_list = reports.setdefault(chat_id, {}).setdefault(today, [])
    result = _parse_deposit_form(text)

    def _fmt(v): return int(v) if v == int(v) else v
    key_fn = (lambda e: e.get('user_id') == user_id) if user_id else (lambda e: e.get('msg_id') == msg.message_id)
    updated = False
    for i, entry in enumerate(day_list):
        if key_fn(entry):
            if result:
                day_list[i] = _make_deposit_entry(result[0], result[1], result[2], msg.message_id, user_id)
                updated = True
            else:
                day_list.pop(i)
            break
    else:
        if result:
            _upsert_deposit_entry(day_list, _make_deposit_entry(result[0], result[1], result[2], msg.message_id, user_id))
            updated = True

    if context.application.persistence:
        await context.application.persistence.flush()
    if updated and result:
        jie, shou, section = result
        pct = round(shou * 100 / jie, 2) if jie > 0 else 0
        await msg.reply_text(f"✅ Edit [{section}]\n接电报：{_fmt(jie)}   首冲：{_fmt(shou)}   ({pct}%)")


async def deposit_total_command(update: Update, context: CallbackContext) -> None:
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)
    chat_id = str(update.effective_chat.id)
    today = get_data_key()
    day_list = context.application.bot_data.get(DEPOSIT_REPORT_KEY, {}).get(chat_id, {}).get(today, [])
    valid = [r for r in day_list if r.get('parser_version') == 2 and r.get('section') in {'first', 'second', 'third', 'last'}]

    if not valid:
        await update.message.reply_text("📊 ယနေ့ deposit report မရှိသေးပါ။")
        return

    def _fmt(v): return int(v) if v == int(v) else v
    total_jie = sum(r['jie'] for r in valid)
    total_shou = sum(r['shou'] for r in valid)
    pct_str = f"{round(total_shou * 100 / total_jie, 2)}%" if total_jie > 0 else "N/A"

    await update.message.reply_text(
        f"📊 <b>Deposit Total</b>  ({today})\n\n"
        f"接电报：<b>{_fmt(total_jie)}</b>\n首冲：<b>{_fmt(total_shou)}</b>\n百分之：<b>{pct_str}</b>\n\n"
        f"<i>From {len(valid)} reports</i>",
        parse_mode='HTML'
    )
    context.application.bot_data.get(DEPOSIT_REPORT_KEY, {}).get(chat_id, {}).pop(today, None)
    if context.application.persistence:
        await context.application.persistence.flush()


def _make_whatsapp_entry(jinfen, zhuanhua, register, section, msg_id, user_id=None) -> dict:
    return {'jinfen': jinfen, 'zhuanhua': zhuanhua, 'register': register, 'section': section,
            'msg_id': msg_id, 'user_id': user_id, 'parser_version': 1}


def _parse_whatsapp_form(text: str):
    required = [r'👉\s*first', r'👉\s*second', r'👉\s*third', r'👉\s*last',
                r'进粉数量\s*[：:]', r'转化到电报\s*[：:]', r'register\s*[：:]']
    if not all(re.search(p, text, re.IGNORECASE) for p in required):
        return None

    first_m = re.search(r'👉\s*first', text, re.IGNORECASE)
    second_m = re.search(r'👉\s*second', text, re.IGNORECASE)
    third_m = re.search(r'👉\s*third', text, re.IGNORECASE)
    last_m = re.search(r'👉\s*last', text, re.IGNORECASE)
    if not (first_m and second_m and third_m and last_m):
        return None

    slices = {
        'first': text[first_m.start():second_m.start()],
        'second': text[second_m.start():third_m.start()],
        'third': text[third_m.start():last_m.start()],
        'last': text[last_m.start():],
    }

    def _extract(chunk):
        jinfen = _parse_number_field(r'进粉数量\s*[：:]', chunk)
        zhuanhua = _parse_number_field(r'转化到电报\s*[：:]', chunk)
        register = _parse_number_field(r'register\s*[：:]', chunk)
        if jinfen is not None and zhuanhua is not None:
            return jinfen, zhuanhua, (register if register is not None else 0)
        return None

    for key in ['last', 'third', 'second', 'first']:
        r = _extract(slices[key])
        if r:
            return r[0], r[1], r[2], key
    return None


async def handle_whatsapp_report(update: Update, context: CallbackContext) -> None:
    msg = update.message
    if not msg:
        return
    text = msg.text or msg.caption or ''
    if '进粉数量' not in text or '转化到电报' not in text:
        return
    result = _parse_whatsapp_form(text)
    if not result:
        return
    jinfen, zhuanhua, register, section = result
    chat_id = str(update.effective_chat.id)
    today = get_data_key()
    reports = context.application.bot_data.setdefault(WHATSAPP_REPORT_KEY, {})
    _upsert_deposit_entry(reports.setdefault(chat_id, {}).setdefault(today, []),
                          _make_whatsapp_entry(jinfen, zhuanhua, register, section, msg.message_id, msg.from_user.id if msg.from_user else None))
    if context.application.persistence:
        await context.application.persistence.flush()


async def handle_whatsapp_report_edit(update: Update, context: CallbackContext) -> None:
    msg = update.edited_message
    if not msg:
        return
    text = msg.text or msg.caption or ''
    if '进粉数量' not in text or '转化到电报' not in text:
        return
    user_id = msg.from_user.id if msg.from_user else None
    chat_id = str(update.effective_chat.id)
    today = get_data_key()
    reports = context.application.bot_data.setdefault(WHATSAPP_REPORT_KEY, {})
    day_list = reports.setdefault(chat_id, {}).setdefault(today, [])
    result = _parse_whatsapp_form(text)

    def _fmt(v): return int(v) if v == int(v) else v
    key_fn = (lambda e: e.get('user_id') == user_id) if user_id else (lambda e: e.get('msg_id') == msg.message_id)
    updated = False
    for i, entry in enumerate(day_list):
        if key_fn(entry):
            if result:
                day_list[i] = _make_whatsapp_entry(result[0], result[1], result[2], result[3], msg.message_id, user_id)
                updated = True
            else:
                day_list.pop(i)
            break
    else:
        if result:
            _upsert_deposit_entry(day_list, _make_whatsapp_entry(result[0], result[1], result[2], result[3], msg.message_id, user_id))
            updated = True

    if context.application.persistence:
        await context.application.persistence.flush()
    if updated and result:
        jinfen, zhuanhua, register, section = result
        pct = round(zhuanhua * 100 / jinfen, 2) if jinfen > 0 else 0
        await msg.reply_text(f"✅ Edit [{section}]\n进粉数量：{_fmt(jinfen)}   转化到电报：{_fmt(zhuanhua)}   register：{_fmt(register)}   ({pct}%)")


async def whatsapp_total_command(update: Update, context: CallbackContext) -> None:
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)
    chat_id = str(update.effective_chat.id)
    today = get_data_key()
    day_list = context.application.bot_data.get(WHATSAPP_REPORT_KEY, {}).get(chat_id, {}).get(today, [])
    valid = [r for r in day_list if r.get('parser_version') == 1 and r.get('section') in {'first', 'second', 'third', 'last'}]

    if not valid:
        await update.message.reply_text("📊 ယနေ့ WhatsApp report မရှိသေးပါ။")
        return

    def _fmt(v): return int(v) if v == int(v) else v
    total_jinfen = sum(r['jinfen'] for r in valid)
    total_zhuanhua = sum(r['zhuanhua'] for r in valid)
    total_register = sum(r.get('register', 0) for r in valid)
    pct_str = f"{round(total_zhuanhua * 100 / total_jinfen, 2)}%" if total_jinfen > 0 else "N/A"

    await update.message.reply_text(
        f"📊 <b>WhatsApp Total</b>  ({today})\n\n"
        f"进粉数量：<b>{_fmt(total_jinfen)}</b>\n转化到电报：<b>{_fmt(total_zhuanhua)}</b>\n"
        f"register：<b>{_fmt(total_register)}</b>\n百分之：<b>{pct_str}</b>\n\n"
        f"<i>From {len(valid)} reports</i>",
        parse_mode='HTML'
    )
    context.application.bot_data.get(WHATSAPP_REPORT_KEY, {}).get(chat_id, {}).pop(today, None)
    if context.application.persistence:
        await context.application.persistence.flush()


# ============================================================
# SCHEDULE
# ============================================================

async def scheduled_message_job(context: CallbackContext) -> None:
    sched_id = context.job.data.get('sched_id')
    schedules = context.application.bot_data.get('schedules', {})
    sched = schedules.get(sched_id)
    if not sched:
        return

    for group_id in sched.get('group_ids', []):
        try:
            await context.application.bot.send_message(chat_id=group_id, text=sched['message'])
        except Exception as e:
            logging.warning(f"Scheduled message failed for {group_id}: {e}")

    if sched.get('type') == 'once':
        schedules.pop(sched_id, None)
        if context.application.persistence:
            await context.application.persistence.flush()


async def setschedule_command(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id not in ADMIN_IDS:
        return ConversationHandler.END
    await update.message.reply_text(
        "⏰ Schedule time ကို HH:MM ပုံစံဖြင့် ရိုက်ပါ (Yangon time):\nဥပမာ: 09:30\n\n(/cancel ဖြင့် ရပ်)"
    )
    return SCHEDULE_SET_TIME


async def schedule_set_time(update: Update, context: CallbackContext) -> int:
    m = re.match(r'^(\d{1,2}):(\d{2})$', update.message.text.strip())
    if not m:
        await update.message.reply_text("❌ HH:MM ပုံစံ မမှန်ပါ (ဥပမာ: 09:30)")
        return SCHEDULE_SET_TIME
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        await update.message.reply_text("❌ အချိန်မမှန်ပါ။")
        return SCHEDULE_SET_TIME
    context.user_data['new_schedule_hour'] = hour
    context.user_data['new_schedule_minute'] = minute
    await update.message.reply_text(f"✅ {hour:02d}:{minute:02d} (Yangon)\n\nMessage ကို ရိုက်ထည့်ပါ:")
    return SCHEDULE_SET_MESSAGE


async def schedule_set_message(update: Update, context: CallbackContext) -> int:
    context.user_data['new_schedule_message'] = update.message.text
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("1️⃣ One-time", callback_data='sched_type_once')],
        [InlineKeyboardButton("🔁 Every Day", callback_data='sched_type_daily')],
        [InlineKeyboardButton("❌ Cancel", callback_data='sched_cancel')],
    ])
    await update.message.reply_text("📌 Schedule type ရွေးပါ:", reply_markup=keyboard)
    return SCHEDULE_SELECT_TYPE


async def schedule_select_type(update: Update, context: CallbackContext) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == 'sched_cancel':
        await query.edit_message_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
        return ConversationHandler.END

    context.user_data['new_schedule_type'] = 'once' if query.data == 'sched_type_once' else 'daily'
    type_label = "1️⃣ One-time" if context.user_data['new_schedule_type'] == 'once' else "🔁 Every Day"

    groups = context.application.bot_data.get('groups', set())
    if not groups:
        await query.edit_message_text("❌ Group မရှိသေးပါ။ Bot ကို group ထဲ ထည့်ပါ။")
        return ConversationHandler.END

    keyboard = []
    for group_id in sorted(list(groups)):
        try:
            chat = await context.application.bot.get_chat(chat_id=group_id)
            name = chat.title or f"Group {group_id}"
        except Exception:
            name = f"Group {group_id}"
        keyboard.append([InlineKeyboardButton(f"👥 {name}", callback_data=f'sched_grp_{group_id}')])
    keyboard.append([InlineKeyboardButton("✅ All Groups", callback_data='sched_grp_ALL')])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data='sched_cancel')])

    await query.edit_message_text(
        f"✅ Type: <b>{type_label}</b>\n\n👥 Group ရွေးပါ:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='HTML'
    )
    return SCHEDULE_SELECT_GROUP


async def schedule_select_group(update: Update, context: CallbackContext) -> int:
    import uuid
    from datetime import datetime as _dt, timedelta as _td

    query = update.callback_query
    await query.answer()

    hour = context.user_data.pop('new_schedule_hour', None)
    minute = context.user_data.pop('new_schedule_minute', None)
    message_text = context.user_data.pop('new_schedule_message', None)
    sched_type = context.user_data.pop('new_schedule_type', 'daily')

    if hour is None or minute is None or not message_text:
        await query.edit_message_text("❌ အချက်အလက် မပြည့်စုံပါ။")
        return ConversationHandler.END

    groups = context.application.bot_data.get('groups', set())
    if query.data == 'sched_grp_ALL':
        selected_groups = list(groups)
    elif query.data.startswith('sched_grp_'):
        selected_groups = [int(query.data[len('sched_grp_'):])]
    elif query.data == 'sched_cancel':
        await query.edit_message_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
        return ConversationHandler.END
    else:
        await query.edit_message_text("❌ Invalid.")
        return ConversationHandler.END

    sched_id = str(uuid.uuid4())[:8]
    context.application.bot_data.setdefault('schedules', {})[sched_id] = {
        'hour': hour, 'minute': minute, 'message': message_text,
        'group_ids': selected_groups, 'type': sched_type,
    }

    tz = get_yangon_tz()
    if sched_type == 'once':
        now = _dt.now(tz)
        target_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target_dt <= now:
            target_dt += _td(days=1)
        context.application.job_queue.run_once(scheduled_message_job, when=target_dt, name=sched_id, data={'sched_id': sched_id})
        fire_label = target_dt.strftime("%Y-%m-%d %H:%M") + " (Yangon)"
    else:
        context.application.job_queue.run_daily(
            scheduled_message_job, time=time(hour=hour, minute=minute, tzinfo=tz), name=sched_id, data={'sched_id': sched_id}
        )
        fire_label = f"နေ့တိုင်း {hour:02d}:{minute:02d} (Yangon)"

    if context.application.persistence:
        await context.application.persistence.flush()

    group_names = []
    for gid in selected_groups:
        try:
            chat = await context.application.bot.get_chat(chat_id=gid)
            group_names.append(chat.title or str(gid))
        except Exception:
            group_names.append(str(gid))

    await query.edit_message_text(
        f"✅ <b>Schedule ထည့်ပြီး!</b>\n\n"
        f"⏰ {fire_label}\n💬 {message_text[:60]}{'...' if len(message_text) > 60 else ''}\n"
        f"👥 {', '.join(group_names)}\n🆔 <code>{sched_id}</code>",
        parse_mode='HTML'
    )
    return ConversationHandler.END


async def schedule_cancel(update: Update, context: CallbackContext) -> int:
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ ဖျက်သိမ်းလိုက်ပါသည်။")
    elif update.message:
        await update.message.reply_text("❌ Cancelled.")
    for key in ['new_schedule_hour', 'new_schedule_minute', 'new_schedule_message', 'new_schedule_type']:
        context.user_data.pop(key, None)
    return ConversationHandler.END


async def listschedules_command(update: Update, context: CallbackContext) -> None:
    schedules = context.application.bot_data.get('schedules', {})
    if not schedules:
        await update.message.reply_text("⏰ Active schedule မရှိသေးပါ။")
        return

    text = "⏰ <b>Active Schedules:</b>\n\n"
    for i, (sched_id, s) in enumerate(schedules.items(), 1):
        t = "1️⃣ One-time" if s.get('type') == 'once' else "🔁 Daily"
        text += f"{i}. <code>{sched_id}</code> | {t} | {s['hour']:02d}:{s['minute']:02d} | Groups: {len(s['group_ids'])}\n"
        text += f"   💬 {s['message'][:50]}{'...' if len(s['message']) > 50 else ''}\n\n"
    await update.message.reply_text(text, parse_mode='HTML')


async def removeschedule_command(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Usage: /removeschedule <schedule_id>\n/listschedules ဖြင့် IDs ကြည့်ပါ")
        return
    sched_id = context.args[0].strip()
    schedules = context.application.bot_data.get('schedules', {})
    if sched_id not in schedules:
        await update.message.reply_text(f"❌ Schedule <code>{sched_id}</code> မတွေ့ပါ။", parse_mode='HTML')
        return
    sched = schedules.pop(sched_id)
    for job in context.application.job_queue.get_jobs_by_name(sched_id):
        job.schedule_removal()
    if context.application.persistence:
        await context.application.persistence.flush()
    await update.message.reply_text(f"✅ Schedule <code>{sched_id}</code> ({sched['hour']:02d}:{sched['minute']:02d}) ဖျက်ပြီးပါပြီ။", parse_mode='HTML')


def restore_schedules(application: Application) -> None:
    from datetime import datetime as _dt, timedelta as _td
    schedules = application.bot_data.get('schedules', {})
    tz = get_yangon_tz()
    for sched_id, sched in schedules.items():
        try:
            if not application.job_queue.get_jobs_by_name(sched_id):
                if sched.get('type') == 'once':
                    now = _dt.now(tz)
                    target_dt = now.replace(hour=sched['hour'], minute=sched['minute'], second=0, microsecond=0)
                    if target_dt <= now:
                        target_dt += _td(days=1)
                    application.job_queue.run_once(scheduled_message_job, when=target_dt, name=sched_id, data={'sched_id': sched_id})
                else:
                    application.job_queue.run_daily(
                        scheduled_message_job,
                        time=time(hour=sched['hour'], minute=sched['minute'], tzinfo=tz),
                        name=sched_id, data={'sched_id': sched_id}
                    )
        except Exception as e:
            logging.warning(f"restore schedule {sched_id} failed: {e}")


# ============================================================
# PLUS COUNTER
# ============================================================

async def handle_plus_reply(update: Update, context: CallbackContext) -> None:
    msg = update.message
    if not msg or not msg.reply_to_message:
        return
    original = msg.reply_to_message
    sender = original.from_user
    if not sender:
        return

    sender_id = sender.id
    chat_id = msg.chat.id
    msg_key = (chat_id, original.message_id)
    count_key = (chat_id, sender_id)

    if msg_key in plus_counted_msgs:
        given_count = plus_counted_msgs[msg_key]["count"]
        await _reply_custom_animated(
            original, context.application.bot_data, "plus_already",
            animated_counts={"given_count": given_count},
        )
        return

    plus_names[sender_id] = sender.full_name or sender.username or str(sender_id)
    plus_counters[count_key] = plus_counters.get(count_key, 0) + 1
    count = plus_counters[count_key]
    plus_counted_msgs[msg_key] = {"count": count, "sender_id": sender_id}
    save_plus_data()
    await _reply_custom_plus(original, context.application.bot_data, count)


async def handle_minus_reply(update: Update, context: CallbackContext) -> None:
    msg = update.message
    if not msg or not msg.reply_to_message:
        return
    original = msg.reply_to_message
    chat_id = msg.chat.id
    msg_key = (chat_id, original.message_id)

    if msg_key in data_msg_map:
        record = data_msg_map.pop(msg_key)
        entry = record["entry"]
        date_key = record["date_key"]
        cid_str = record["chat_id"]

        # Remove from MongoDB
        entries = mg_load_group_data(cid_str, date_key)
        if entry in entries:
            entries.remove(entry)
            mg_save_group_data(cid_str, date_key, entries)
        # Remove from pickle fallback
        group_data = context.application.bot_data.get('group_data', {})
        p_entries = group_data.get(cid_str, {}).get(date_key, [])
        if entry in p_entries:
            p_entries.remove(entry)
            group_data.setdefault(cid_str, {})[date_key] = p_entries
            if context.application.persistence:
                await context.application.persistence.flush()

        save_data_msg_map()
        await _reply_custom(original, context.application.bot_data,
                            "minus_del_entry", entry=entry)
        return

    if msg_key not in plus_counted_msgs:
        await _reply_custom(original, context.application.bot_data, "minus_not_data")
        return

    record = plus_counted_msgs.pop(msg_key)
    given_count = record["count"]
    sender_id = record["sender_id"]
    count_key = (chat_id, sender_id)
    if count_key in plus_counters and plus_counters[count_key] > 0:
        plus_counters[count_key] -= 1
    save_plus_data()
    await _reply_custom_animated(
        original, context.application.bot_data, "minus_del_plus",
        animated_counts={"given_count": given_count},
    )


async def handle_daqiang_reply(update: Update, context: CallbackContext) -> None:
    """Reply to '打枪' or 'သာချန်း' with the Username field from the quoted message.

    Trigger : any REPLY message whose text is exactly '打枪' or 'သာချန်း'.
    Action  : extract 'Username - <value>' from the replied-to message and send
              a customisable reply (supports animated emoji, stored in MongoDB
              via the existing /setmsg → save_bot_config_to_mongo flow).
    """
    msg = update.message
    if not msg or not msg.reply_to_message:
        return

    original = msg.reply_to_message
    original_text = (original.text or original.caption or '').strip()
    if not original_text:
        return

    # Extract Gmail field (handles "Gmail - value", "gmail: value", "Gmail — value")
    username_match = re.search(
        r'[Gg]mail\s*[-–—：:]\s*(.+?)(?:\n|$)',
        original_text,
    )
    if not username_match:
        # No Gmail field in the quoted message — silently ignore
        return

    username_value = username_match.group(1).strip()
    if not username_value or username_value in ('-', '–', '—'):
        return

    # Build sender mention from the ORIGINAL message's author (report owner)
    original_sender = original.from_user
    if original_sender:
        sender_mention = f"@{original_sender.username}" if original_sender.username else (original_sender.full_name or "User")
    else:
        sender_mention = "User"

    await _reply_custom(
        msg,
        context.application.bot_data,
        "daqiang_reply",
        username_value=username_value,
        sender_mention=sender_mention,
    )


def _is_green_data_submission(text: str) -> bool:
    """Return True only when 'text' looks like a member data submission containing Green.

    Rules (all must pass):
      1. Contains the word 'green' (case-insensitive, standalone or as prefix like 'Green account').
      2. Contains at least one numeric sequence of 8 or more consecutive digits
         — this is the phone number or account ID that every submission has, and that
         casual conversation about the color green almost never contains.

    This prevents ordinary chat messages such as
        "the green light is on"  or  "Green ရောင် ကောင်းသည်"
    from triggering the counter while still catching every format shown in production
    (text-only, photo+caption, single-line, multi-line, any language mix).
    """
    if not re.search(r'green', text, re.IGNORECASE):
        return False
    # At least one run of 8+ digits anywhere in the message
    if not re.search(r'\d{8,}', text):
        return False
    return True


async def handle_green_report(update: Update, context: CallbackContext) -> None:
    """Count data-submission messages containing 'Green' per chat per day and alert after threshold.

    - Works with text-only messages AND photo/media with caption.
    - Only fires for data submissions (must contain 8+ digit number alongside 'green').
    - Counts in-memory only (resets on bot restart or the next calendar day).
    - Starts alerting from (GREEN_ALERT_THRESHOLD + 1)-th submission onward.
    - Custom message stored in MongoDB via /setmsg → 'green_alert'.
    - Placeholder: {count} = today's running total for this chat.
    """
    msg = update.message
    if not msg or not msg.chat:
        return
    # Group/supergroup only
    if msg.chat.type not in ('group', 'supergroup'):
        return

    # Accept text messages and any media with a caption (photo, document, etc.)
    text = (msg.text or msg.caption or '').strip()
    if not text:
        return

    if not _is_green_data_submission(text):
        return

    chat_id = str(msg.chat.id)
    today_key = get_data_key()
    key = (chat_id, today_key)
    green_counters[key] = green_counters.get(key, 0) + 1
    count = green_counters[key]

    if count > GREEN_ALERT_THRESHOLD:
        await _reply_custom(
            msg,
            context.application.bot_data,
            "green_alert",
            count=count,
        )


async def total_plus_command(update: Update, context: CallbackContext) -> None:
    current_chat = update.effective_chat.id
    chat_entries = {uid: cnt for (cid, uid), cnt in plus_counters.items() if cid == current_chat}

    if not chat_entries:
        await _reply_custom(update.message, context.application.bot_data, "total_plus_empty")
        return

    grand_total = sum(chat_entries.values())
    await _send_total_plus(
        update.message, context.application.bot_data,
        chat_entries, grand_total, plus_names,
    )
    _u = update.effective_user
    _mention = f"@{_u.username}" if (_u and _u.username) else (_u.full_name if _u else "User")
    await _reply_custom(update.message, context.application.bot_data,
                        "total_plus_footer", parse_mode='HTML', mention=_mention)


async def reset_plus_command(update: Update, context: CallbackContext) -> None:
    """/reset_plus — ဤ chat ထဲးမှာ plus_counters ကိုသာ ရှင်လင်းသည်။"""
    current_chat = update.effective_chat.id
    keys_to_del = [k for k in plus_counters if k[0] == current_chat]

    if not keys_to_del:
        await _reply_custom(update.message, context.application.bot_data, "reset_plus_empty")
        return

    for k in keys_to_del:
        del plus_counters[k]
    for k in [k for k in plus_counted_msgs if k[0] == current_chat]:
        del plus_counted_msgs[k]
    save_plus_data()

    await _reply_custom(update.message, context.application.bot_data,
                        "reset_plus_ok", count=len(keys_to_del))


# ============================================================
# GUIDE
# ============================================================

# ============================================================
# GUIDE PAGES - Multi-page inline navigation
# ============================================================

GUIDE_PAGES = [
    {
        "title": "📖 Bot လမ်းညွှန် (1/5) — Report Form",
        "text": (
            "<b>📋 Report Form ပုံစံ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/form ကိုနှိပ်၍ template ကူးယူပါ။\n\n"
            "<b>ဖြည့်ရမည့် field များ:</b>\n"
            "• Gmail\n"
            "• Tele name\n"
            "• Username\n"
            "• Date\n"
            "• Age\n"
            "• Current work\n"
            "• Phone number\n"
            "• ID\n"
            "• Khaifa\n\n"
            "ဖြည့်ပြီးပါက group ထဲ paste လုပ်ပါ။"
        ),
    },
    {
        "title": "📖 Bot လမ်းညွှန် (2/5) — Data စီမံခန့်ခွဲမှု",
        "text": (
            "<b>📊 Data စီမံခန့်ခွဲမှု</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "<b>/showdata</b>\n"
            "ယနေ့ deposit data တစ်စုတစ်စည်းထုတ်ပေးသည်။\n\n"
            "<b>/cleardata</b>\n"
            "ယနေ့ data နှင့် plus counter ရှင်းလင်းသည်။\n"
            "⚠️ နေ့တိုင်း အလုပ်မဆင်းမီ သုံးပါ။\n\n"
            "<b>‼️ Data တစ်ခုတည်း ဖျက်နည်း:</b>\n"
            "Bot reply ပြန်သော message ကို\n"
            "<code>-</code> ဖြင့် reply ပြန်ပါ → ဆောင်ရွက်ပေးမည်။\n\n"
            "<b>/deposit_total</b> — Deposit report ကြည့်\n"
            "<b>/whatsapp_total</b> — WhatsApp report ကြည့်"
        ),
    },
    {
        "title": "📖 Bot လမ်းညွှန် (3/5) — Plus Counter",
        "text": (
            "<b>➕ Plus Counter စနစ်</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Message ကို <code>+</code> ဖြင့် reply ပြန်ပါ\n"
            "→ bot က <b>+1, +2, +3...</b> ရေတွက်ပေးမည်။\n\n"
            "မှားမိပါက <code>-</code> ဖြင့် reply → ပယ်ဖျက်ပေးမည်။\n\n"
            "<b>/total_plus</b> — Plus counter summary ကြည့်\n"
            "<b>/reset_plus</b> — Plus counter ရှင်းလင်း\n\n"
            "<b>🧮 Math Calculator</b>\n"
            "Bot PM ထဲတွင် expression ရိုက်ရုံဖြင့် တွက်ပေးသည်:\n"
            "ဥပမာ: <code>2+2</code>, <code>15*15</code>, <code>100/4</code>"
        ),
    },
    {
        "title": "📖 Bot လမ်းညွှန် (4/5) — ✉️ Feedback & Menu",
        "text": (
            "<b>✉️ Feedback ပေးပို့နည်း</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "/feedback ကိုနှိပ်ပြီး\n"
            "Admin ထံ မှတ်ချက်/အကြံပြုချက် ပေးပို့နိုင်သည်။\n\n"
            "<b>📱 Menu စီမံခန့်ခွဲမှု</b>\n"
            "<b>/menu</b> — Main menu ဖွင့်\n"
            "<b>/start</b> — Bot စတင် / menu ပြ\n"
            "<b>/hidemenu</b> — Keyboard ဖျောက်\n\n"
            "<b>🔎 Command အားလုံး:</b>\n"
            "<b>/help</b> ကိုနှိပ်ပြီး command list အပြည့်ကြည့်နိုင်သည်။"
        ),
    },
    {
        "title": "📖 Bot လမ်းညွှန် (5/5) — ⚠️ Duplicate ID သတိပေးပုံ",
        "text": (
            "<b>⚠️ ID Duplicate သတိပေးပုံစံ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "Duplicate စစ်ဆေးတွေ့ပါက bot က ဤပုံစံဖြင့် သတိပေးမည်:\n\n"
            "<i>⚠️ ဤ client သည် ရောက်ပြီးသားဖြစ်ပါသည်။⚠️\n"
            "အောက်တွင်ဖော်ပြထားသည်။ဘယ်အဆင့်ရောက်နေလဲမေးမြန်းပါ။\n"
            "Deposit - @example\n"
            "Gmail - example</i>\n\n"

            "━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 <b>Bot owner</b> — @satepryin1khouklite1"
        ),
    },
]


def _guide_keyboard(page: int) -> InlineKeyboardMarkup:
    total = len(GUIDE_PAGES)
    row = []
    if page > 0:
        row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"guide_page_{page - 1}"))
    row.append(InlineKeyboardButton("🏠 Home", callback_data="guide_page_0"))
    if page < total - 1:
        row.append(InlineKeyboardButton("Next ➡️", callback_data=f"guide_page_{page + 1}"))
    return InlineKeyboardMarkup([row])


async def guide_command(update: Update, context: CallbackContext) -> None:
    await save_chat_id(update.effective_chat.id, context, update.effective_chat.type)
    page = GUIDE_PAGES[0]
    await update.message.reply_text(
        f"{page['title']}\n━━━━━━━━━━━━━━━━━━━━\n\n{page['text']}",
        parse_mode='HTML',
        reply_markup=_guide_keyboard(0)
    )


async def guide_callback(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    await query.answer()
    page_idx = int(query.data.split("_")[-1])
    if page_idx < 0 or page_idx >= len(GUIDE_PAGES):
        return
    page = GUIDE_PAGES[page_idx]
    await query.edit_message_text(
        f"{page['title']}\n━━━━━━━━━━━━━━━━━━━━\n\n{page['text']}",
        parse_mode='HTML',
        reply_markup=_guide_keyboard(page_idx)
    )



# ============================================================
# AUTO CLEAR JOB (runs daily at 12:00 PM Yangon time)
# ============================================================

async def auto_clear_job(context: CallbackContext) -> None:
    tz = get_yangon_tz()
    now = datetime.now(tz)
    prev_key = (now.date() - timedelta(days=1)).strftime('%Y-%m-%d')

    cleared_count = 0

    db = get_mongo_db()
    if db is not None:
        try:
            result = db["group_data"].delete_many({"date_key": prev_key})
            cleared_count = result.deleted_count
        except Exception as e:
            logging.warning(f"auto_clear_job MongoDB error: {e}")

    group_data = context.application.bot_data.get('group_data', {})
    for chat_id_str, days in list(group_data.items()):
        if prev_key in days:
            del days[prev_key]
            chat_id_int = int(chat_id_str)
            for k in [k for k in list(plus_counters.keys()) if k[0] == chat_id_int]:
                del plus_counters[k]
            for k in [k for k in list(plus_counted_msgs.keys()) if k[0] == chat_id_int]:
                del plus_counted_msgs[k]
            for k in [k for k in list(data_msg_map.keys()) if k[0] == chat_id_int]:
                del data_msg_map[k]

    save_plus_data()
    save_data_msg_map()

    if context.application.persistence:
        await context.application.persistence.flush()

    logging.info(f"auto_clear_job: cleared shift {prev_key} ({cleared_count} MongoDB docs)")

    for admin_id in ADMIN_IDS:
        try:
            await context.application.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"🤖 <b>Auto Clear ပြုလုပ်ပြီးပါပြီ</b>\n\n"
                    f"⏰ နေ့လည် 12:00 (Yangon)\n"
                    f"🗑️ Shift: <b>{prev_key}</b> data ရှင်းပြီးပါပြီ\n"
                    f"📊 {cleared_count} group(s) cleared"
                ),
                parse_mode='HTML'
            )
        except Exception as e:
            logging.warning(f"auto_clear_job notify admin {admin_id}: {e}")


# POST INIT
# ============================================================

async def post_init(application: Application) -> None:
    # Restore custom_msgs + start_buttons from MongoDB (survives restarts/redeploys)
    load_bot_config_from_mongo(application.bot_data)
    restore_schedules(application)
    tz = get_yangon_tz()
    application.job_queue.run_daily(
        auto_clear_job,
        time=time(hour=12, minute=0, second=0, tzinfo=tz),
        name='auto_clear_daily'
    )
    await application.bot.set_my_commands([
        BotCommand("start",          "Bot စတင် / Menu ဖွင့်"),
        BotCommand("menu",           "Main menu"),
        BotCommand("guide",          "Bot လမ်းညွှန်"),
        BotCommand("showdata",       "ယနေ့ data ကြည့်"),
        BotCommand("cleardata",      "ယနေ့ data ဖျက်"),
        BotCommand("form",           "Report template"),
        BotCommand("total_plus",     "Plus counter ကြည့်"),
        BotCommand("reset_plus",     "Plus counter ရှင်း"),
        BotCommand("feedback",       "Admin ထံ မှတ်ချက်"),
        BotCommand("hidemenu",       "Keyboard ဖျောက်"),
        BotCommand("help",           "Help"),
        BotCommand("stats",          "Bot stats (Admin)"),
        BotCommand("listusers",      "User list (Admin)"),
        BotCommand("listgroups",     "Group list (Admin)"),
        BotCommand("listschedules",  "Schedule list"),
        BotCommand("admin",          "Admin panel (Admin)"),
        BotCommand("clearall",       "Data အားလုံး ရှင်း (Admin PM)"),
        BotCommand("resetplusall",   "Plus counter အားလုံး reset (Admin PM)"),
        BotCommand("deposit_total",  "Deposit report total"),
        BotCommand("whatsapp_total", "WhatsApp report total"),
    ])


# ============================================================
# ERROR HANDLER WITH ADMIN NOTIFICATION
# ============================================================

async def error_handler(update: object, context: CallbackContext) -> None:
    import traceback
    from telegram.error import Conflict, NetworkError, TimedOut

    err = context.error

    if isinstance(err, Conflict):
        logging.warning("Telegram Conflict: another bot instance running.")
        return
    if isinstance(err, (NetworkError, TimedOut)):
        logging.warning(f"Network error (will retry): {err}")
        return

    logging.error(f"Unhandled error: {err}", exc_info=err)

    tb_str = "".join(traceback.format_exception(type(err), err, err.__traceback__))

    update_info = ""
    if isinstance(update, Update):
        chat = update.effective_chat
        user = update.effective_user
        update_info = (
            f"\n👤 User: {user.full_name if user else 'N/A'} (ID: {user.id if user else 'N/A'})\n"
            f"💬 Chat: {chat.title if chat and chat.title else 'PM'} (ID: {chat.id if chat else 'N/A'})"
        )

    error_msg = (
        f"⚠️ <b>Bot Error</b>{update_info}\n\n"
        f"<b>Error:</b> <code>{str(err)[:500]}</code>\n\n"
        f"<b>Traceback:</b>\n<pre>{tb_str[-1500:]}</pre>"
    )

    for admin_id in ADMIN_IDS:
        try:
            await context.application.bot.send_message(
                chat_id=admin_id,
                text=error_msg,
                parse_mode='HTML'
            )
        except Exception as e:
            logging.warning(f"Could not notify admin {admin_id}: {e}")


# ============================================================
# MAIN
# ============================================================

def main():
    if not TOKEN:
        logging.error("BOT_TOKEN is not set!")
        return

    persistence = PicklePersistence(filepath=os.path.join(os.path.dirname(__file__), 'bot_data.pickle'))

    application = (
        Application.builder()
        .token(TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .connect_timeout(30.0)
        .read_timeout(30.0)
        .write_timeout(30.0)
        .pool_timeout(30.0)
        .build()
    )

    application.add_handler(CommandHandler("menu", main_menu_command))
    application.add_handler(CommandHandler("hidemenu", remove_menu))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("showdata", show_data))
    application.add_handler(CommandHandler("cleardata", clear_data))
    application.add_handler(CommandHandler("form", report_form_command))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("listusers", list_users))
    application.add_handler(CommandHandler("listgroups", list_groups))
    application.add_handler(CommandHandler("listschedules", listschedules_command))
    application.add_handler(CommandHandler("removeschedule", removeschedule_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("clearall", admin_clearall_command))
    application.add_handler(CommandHandler("resetplusall", admin_resetplusall_command))
    application.add_handler(CommandHandler("addbutton", addbutton_command))
    application.add_handler(CommandHandler("removebutton", removebutton_command))
    application.add_handler(CommandHandler("listbuttons", listbuttons_command))
    application.add_handler(CallbackQueryHandler(removebutton_callback, pattern=r'^rmbtn_'))
    application.add_handler(CommandHandler("guide", guide_command))
    application.add_handler(CallbackQueryHandler(guide_callback, pattern=r'^guide_page_\d+$'))
    application.add_handler(CommandHandler("deposit_total", deposit_total_command))
    application.add_handler(CommandHandler("whatsapp_total", whatsapp_total_command))
    application.add_handler(CommandHandler("total_plus", total_plus_command))
    application.add_handler(CommandHandler("reset_plus", reset_plus_command))

    application.add_handler(CallbackQueryHandler(clear_group_data_callback, pattern=r'^admin_clear_-?\d+$'))
    application.add_handler(CallbackQueryHandler(cancel_group_action, pattern='^admin_cancel$'))
    application.add_handler(CallbackQueryHandler(adminall_callback, pattern=r'^adminall_'))
    bot_settings_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(bot_settings_menu, pattern='^adm_botsettings$')],
        states={
            BOT_SETTINGS_SELECT: [
                CallbackQueryHandler(bot_settings_select, pattern='^admbs_(name|about|desc|photo)$'),
                CallbackQueryHandler(bot_settings_cancel, pattern='^admbs_cancel$'),
            ],
            BOT_SETTINGS_AWAITING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, bot_settings_apply),
            ],
            BOT_SETTINGS_PHOTO: [
                MessageHandler(filters.PHOTO, bot_settings_photo_receive),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(bot_settings_cancel, pattern='^admbs_cancel$'),
            CommandHandler('cancel', cancel_conversation),
        ],
        allow_reentry=True, per_message=False,
    )
    application.add_handler(bot_settings_handler)

    application.add_handler(CallbackQueryHandler(admin_panel_callback, pattern='^adm_'))

    # /setmsg — owner-only message customiser
    setmsg_handler = ConversationHandler(
        entry_points=[CommandHandler("setmsg", setmsg_start, filters=filters.ChatType.PRIVATE)],
        states={
            SETMSG_SELECT: [
                CallbackQueryHandler(setmsg_select, pattern=r'^setmsg_'),
            ],
            SETMSG_AWAIT: [
                CommandHandler("reset", setmsg_receive),
                CommandHandler("cancel", setmsg_cancel_conv),
                MessageHandler(filters.TEXT & ~filters.COMMAND, setmsg_receive),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(setmsg_cancel_conv, pattern=r'^setmsg_cancel$'),
            CommandHandler("cancel", setmsg_cancel_conv),
        ],
        allow_reentry=True,
        per_message=False,
    )
    application.add_handler(setmsg_handler)

    feedback_handler = ConversationHandler(
        entry_points=[CommandHandler("feedback", start_feedback)],
        states={FEEDBACK_AWAITING: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_feedback)]},
        fallbacks=[CommandHandler('cancel', cancel_conversation)],
        allow_reentry=True
    )
    application.add_handler(feedback_handler)

    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start, filters=filters.User(ADMIN_IDS))],
        states={
            BROADCAST_SELECT_CHAT: [CallbackQueryHandler(broadcast_select_chat, pattern='^bcast_id_')],
            BROADCAST_AWAITING_MESSAGE: [MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VIDEO | filters.Document.ALL |
                 filters.AUDIO | filters.ANIMATION | filters.VOICE | filters.VIDEO_NOTE |
                 filters.Sticker.ALL) & ~filters.COMMAND,
                broadcast_await_message
            )],
            BROADCAST_CONFIRMATION: [CallbackQueryHandler(broadcast_confirm, pattern='^bcast_confirm$')]
        },
        fallbacks=[
            CallbackQueryHandler(broadcast_cancel, pattern='^bcast_cancel$'),
            CommandHandler('cancel', cancel_conversation)
        ],
        allow_reentry=True
    )
    application.add_handler(broadcast_handler)

    schedule_handler = ConversationHandler(
        entry_points=[CommandHandler("setschedule", setschedule_command)],
        states={
            SCHEDULE_SET_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_set_time)],
            SCHEDULE_SET_MESSAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_set_message)],
            SCHEDULE_SELECT_TYPE: [CallbackQueryHandler(schedule_select_type, pattern='^sched_type_|^sched_cancel$')],
            SCHEDULE_SELECT_GROUP: [CallbackQueryHandler(schedule_select_group, pattern='^sched_grp_|^sched_cancel$')],
        },
        fallbacks=[
            CallbackQueryHandler(schedule_cancel, pattern='^sched_cancel$'),
            CommandHandler('cancel', cancel_conversation)
        ],
        allow_reentry=True
    )
    application.add_handler(schedule_handler)

    application.add_handler(MessageHandler(filters.REPLY & filters.Regex(r'^\+$'), handle_plus_reply))
    application.add_handler(MessageHandler(filters.REPLY & filters.Regex(r'^\-$'), handle_minus_reply))
    application.add_handler(MessageHandler(filters.REPLY & filters.Regex(r'^(打枪|သာချန်း)$'), handle_daqiang_reply))

    # Green alert — fires independently in group=3 so deposit/whatsapp handlers are unaffected
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND & filters.ChatType.GROUPS,
        handle_green_report,
    ), group=3)

    application.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_deposit_report
    ), group=1)
    application.add_handler(MessageHandler(
        (filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_whatsapp_report
    ), group=1)
    application.add_handler(MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & (filters.TEXT | filters.CAPTION), handle_deposit_report_edit
    ), group=1)
    application.add_handler(MessageHandler(
        filters.UpdateType.EDITED_MESSAGE & (filters.TEXT | filters.CAPTION), handle_whatsapp_report_edit
    ), group=1)

    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, handle_pm_math
    ), group=2)

    application.add_handler(MessageHandler(
        (filters.TEXT & ~filters.COMMAND) | filters.CAPTION, extract_and_save_data
    ))

    application.add_error_handler(error_handler)

    application.run_polling(poll_interval=1.0, drop_pending_updates=True, timeout=30)


if __name__ == '__main__':
    import time as _time
    keep_alive()
    while True:
        try:
            main()
        except Exception as _e:
            logging.error(f"Bot crashed: {_e}. Restarting in 10 seconds...")
            _time.sleep(10)
