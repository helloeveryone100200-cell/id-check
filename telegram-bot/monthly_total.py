"""Monthly Khaifa totals, independent from the daily report collection."""
import re
import secrets
import unicodedata
from datetime import datetime

COLLECTION = "monthly_total_entries"
BUTTON_TEXT = "Monthly Total"
CLEAR_TTL_SECONDS = 600
_index_ready = False

WARNING = (
    "ဤ Monthly Total Data ကို လကုန်ရန် နောက်ဆုံးရက်တွင် အလုပ်မဆင်းခင်အချိန်တွင် "
    "Monthly Clear Data ကို နှိပ်ပါ။ မနှိပ်ပါက Bot အသုံးပြုခွင့်ကို ပိတ်ပါမည်။"
)
EMPTY = "ဤ Group တွင် Monthly Total အတွက် ရေတွက်ထားသော Khaifa data မရှိသေးပါ။"


def normalize_khaifa(value):
    if value is None:
        return None, None
    label = unicodedata.normalize("NFC", str(value))
    label = re.sub(r"\s+", " ", label).strip()
    key = re.sub(r"[\uFE0E\uFE0F]", "", label).casefold()
    if key in {"", "-", "—", "–", "−", "－", "n/a", "na", "none", "null"}:
        return None, None
    return label, key


def _ensure_index(db):
    global _index_ready
    if _index_ready or db is None:
        return
    try:
        db[COLLECTION].create_index([("chat_id", 1), ("khaifa_key", 1)])
        _index_ready = True
    except Exception:
        pass


def save_record(db, chat_id, message_id, value):
    label, key = normalize_khaifa(value)
    if not label or not key or db is None:
        return None
    record_id = f"{chat_id}:{int(message_id)}"
    try:
        _ensure_index(db)
        db[COLLECTION].update_one(
            {"_id": record_id},
            {"$setOnInsert": {
                "chat_id": str(chat_id), "message_id": int(message_id),
                "khaifa": label, "khaifa_key": key,
            }},
            upsert=True,
        )
        return record_id
    except Exception:
        return None


def get_totals(db, chat_id):
    if db is None:
        return None
    try:
        _ensure_index(db)
        pipeline = [
            {"$match": {"chat_id": str(chat_id)}},
            {"$group": {
                "_id": "$khaifa_key",
                "label": {"$first": "$khaifa"},
                "count": {"$sum": 1},
            }},
            {"$sort": {"_id": 1}},
        ]
        return [
            {"khaifa": str(x.get("label") or x.get("_id") or ""),
             "count": int(x.get("count", 0))}
            for x in db[COLLECTION].aggregate(pipeline, maxTimeMS=5000)
            if int(x.get("count", 0)) > 0
        ]
    except Exception:
        return None


def remove_record(db, chat_id, record_id):
    if not record_id:
        return True
    if db is None:
        return False
    try:
        db[COLLECTION].delete_one({"_id": str(record_id), "chat_id": str(chat_id)})
        return True
    except Exception:
        return False


def clear_records(db, chat_id):
    """Clear this group's records one-by-one after an explicit confirmation."""
    if db is None:
        return None
    try:
        collection = db[COLLECTION]
        cursor = collection.find({"chat_id": str(chat_id)}, {"_id": 1})
        deleted = 0
        for record in cursor:
            result = collection.delete_one({"_id": record["_id"], "chat_id": str(chat_id)})
            deleted += int(result.deleted_count)
        return deleted
    except Exception:
        return None


async def total_command(update, context, *, get_db):
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None:
        return
    if chat.type not in {"group", "supergroup"}:
        await message.reply_text("Monthly Total ကို Group Chat ထဲတွင်သာ အသုံးပြုနိုင်ပါသည်။")
        return
    totals = get_totals(get_db(), chat.id)
    if totals is None:
        await message.reply_text("❌ Monthly Total Data ကို Database မှ ဖတ်၍မရပါ။")
        return
    lines = ["📊 Monthly Total"]
    if totals:
        lines.extend(f"{x['khaifa']}   -   {x['count']}" for x in totals)
    else:
        lines.append(EMPTY)
    lines.extend(["", WARNING])
    button = __import__("telegram").InlineKeyboardButton(
        "Monthly Clear Data", callback_data="monthly_clear_start", style="danger"
    )
    await message.reply_text(
        "\n".join(lines),
        reply_markup=__import__("telegram").InlineKeyboardMarkup([[button]]),
    )


async def clear_callback(update, context, *, get_db, is_group_admin):
    query = update.callback_query
    if query is None or query.message is None:
        if query:
            await query.answer()
        return
    chat, user = query.message.chat, query.from_user
    if chat.type not in {"group", "supergroup"} or user is None:
        await query.answer("Group ထဲတွင်သာ အသုံးပြုနိုင်ပါသည်။", show_alert=True)
        return
    chat_id, data = int(chat.id), query.data or ""

    if data == "monthly_clear_start":
        if not await is_group_admin(context, user.id, chat_id):
            await query.answer("🔒 Group Owner/Admin သာ Monthly Clear Data ကို အသုံးပြုနိုင်ပါသည်။", show_alert=True)
            return
        totals = get_totals(get_db(), chat_id)
        if totals is None:
            await query.answer("❌ Database မှ ဖတ်၍မရပါ။", show_alert=True)
            return
        if not totals:
            await query.answer("Monthly Total data မရှိသေးပါ။", show_alert=True)
            return
        token = secrets.token_urlsafe(6)
        context.chat_data["monthly_clear_pending"] = {
            "token": token, "user_id": int(user.id), "created_at": datetime.now().timestamp()
        }
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("Confirm", callback_data=f"monthly_clear_confirm_{token}", style="success"),
            InlineKeyboardButton("Cancel", callback_data=f"monthly_clear_cancel_{token}", style="danger"),
        ]])
        await query.answer()
        await query.edit_message_text(
            "⚠️ ဤ Group ၏ Monthly Total Data အားလုံးကို Database မှ ရှင်းလင်းမည်။\n\nဆက်လုပ်မည်လား?",
            reply_markup=keyboard,
        )
        return

    match = re.fullmatch(r"monthly_clear_(confirm|cancel)_([A-Za-z0-9_-]{8,20})", data)
    if not match:
        await query.answer()
        return
    action, token = match.groups()
    pending = context.chat_data.get("monthly_clear_pending") or {}
    created = pending.get("created_at")
    expired = not isinstance(created, (int, float)) or datetime.now().timestamp() - created > CLEAR_TTL_SECONDS
    if expired or pending.get("token") != token or int(pending.get("user_id", 0)) != int(user.id):
        context.chat_data.pop("monthly_clear_pending", None)
        await query.answer("⌛ Confirmation သက်တမ်းကုန်သွားပါပြီ။", show_alert=True)
        return
    if not await is_group_admin(context, user.id, chat_id):
        await query.answer("🔒 Group Owner/Admin သာ အသုံးပြုနိုင်ပါသည်။", show_alert=True)
        return
    if action == "cancel":
        context.chat_data.pop("monthly_clear_pending", None)
        await query.answer()
        await query.edit_message_text("↩️ Monthly Clear Data ကို ပယ်ဖျက်လိုက်ပါပြီ။ Data များ မပြောင်းလဲပါ။")
        return
    count = clear_records(get_db(), chat_id)
    if count is None:
        context.chat_data.pop("monthly_clear_pending", None)
        await query.answer()
        await query.edit_message_text("❌ Database error — Monthly Data များကို မဖျက်နိုင်ပါ။")
        return
    context.chat_data.pop("monthly_clear_pending", None)
    if context.application.persistence:
        await context.application.persistence.flush()
    await query.answer()
    await query.edit_message_text(f"✅ Monthly Total Data ရှင်းလင်းပြီးပါပြီ။\n🗑️ Record {count} ခု ဖျက်ပြီးပါပြီ။")
