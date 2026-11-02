"""Compact monthly Khaifa counters with current-day reversal tracking.

One document is stored per (group, normalized Khaifa), not per submission.
The active_ids array contains only records that are still reversible during
the current daily reporting window. Daily clearing empties that array while
leaving the month-to-date count intact.
"""
import re
import secrets
import threading
import unicodedata
from datetime import datetime

COLLECTION = "monthly_total_counts"
LEGACY_COLLECTION = "monthly_total_entries"
META_COLLECTION = "monthly_total_meta"
STORAGE_VERSION = 2
BUTTON_TEXT = "Monthly Total"
CLEAR_TTL_SECONDS = 600
_index_ready = False
_write_lock = threading.RLock()

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


def _counter_id(chat_id, key):
    return f"{str(chat_id)}:{key}"


def _ensure_index(db):
    global _index_ready
    if _index_ready or db is None:
        return
    try:
        collection = db[COLLECTION]
        collection.create_index([("chat_id", 1), ("khaifa_key", 1)])
        collection.create_index([("chat_id", 1), ("active_ids", 1)])
        _index_ready = True
    except Exception:
        # Index creation is retried on the next operation. Queries remain safe.
        pass


def _entry_khaifa(record):
    fields = str(record.get("entry") or "").split("    ", 2)
    if len(fields) < 2:
        return None, None
    return normalize_khaifa(fields[1])


def save_record(db, chat_id, message_id, value):
    """Increment one compact counter and register this ID for current-day '-'.

    The same message ID is stored only in the counter's active_ids array.
    MongoDB's atomic $addToSet + $inc update prevents duplicate increments.
    """
    label, key = normalize_khaifa(value)
    if not label or not key or db is None:
        return None

    chat_id = str(chat_id)
    record_id = f"{chat_id}:{int(message_id)}"
    try:
        _ensure_index(db)
        collection = db[COLLECTION]
        with _write_lock:
            # A duplicate delivery must not increment a different Khaifa row.
            existing = collection.find_one(
                {"chat_id": chat_id, "active_ids": record_id},
                {"_id": 1},
            )
            if existing:
                return record_id

            counter_id = _counter_id(chat_id, key)
            collection.update_one(
                {"_id": counter_id},
                {"$setOnInsert": {
                    "chat_id": chat_id,
                    "khaifa": label,
                    "khaifa_key": key,
                    "count": 0,
                    "active_ids": [],
                }},
                upsert=True,
            )
            collection.update_one(
                {"_id": counter_id, "active_ids": {"$ne": record_id}},
                {
                    "$inc": {"count": 1},
                    "$addToSet": {"active_ids": record_id},
                    "$set": {"khaifa": label, "khaifa_key": key},
                },
            )
        return record_id
    except Exception:
        return None


def backfill_active_records(db, data_msg_map):
    """Migrate legacy per-submission documents to one counter per Khaifa.

    Existing month totals are preserved. Only still-active reply mappings are
    copied into active_ids, so '-' remains reversible for today's submissions.
    A version marker makes the migration safe across restarts and after clear.
    """
    if db is None:
        return 0

    try:
        _ensure_index(db)
        legacy = db[LEGACY_COLLECTION]
        counters = db[COLLECTION]
        meta = db[META_COLLECTION]

        map_by_group = {}
        for map_key, record in list((data_msg_map or {}).items()):
            if (
                not isinstance(map_key, tuple)
                or len(map_key) != 2
                or not isinstance(record, dict)
            ):
                continue
            chat_id = str(record.get("chat_id") or map_key[0])
            map_by_group.setdefault(chat_id, []).append((map_key[1], record))

        legacy_rows = list(legacy.find({}))
        legacy_by_group = {}
        for row in legacy_rows:
            if not isinstance(row, dict):
                continue
            chat_id = str(row.get("chat_id") or "")
            if chat_id:
                legacy_by_group.setdefault(chat_id, []).append(row)

        group_ids = set(map_by_group) | set(legacy_by_group)
        migrated = 0
        for chat_id in group_ids:
            marker = meta.find_one({"_id": chat_id}) or {}
            if int(marker.get("storage_version", 0) or 0) >= STORAGE_VERSION:
                # Clean up leftovers from an interrupted migration.
                legacy.delete_many({"chat_id": chat_id})
                continue

            rows = legacy_by_group.get(chat_id, [])
            legacy_by_id = {str(row.get("_id")): row for row in rows}
            grouped = {}
            for row in rows:
                label, key = normalize_khaifa(
                    row.get("khaifa") or row.get("khaifa_key")
                )
                if not label or not key:
                    continue
                item = grouped.setdefault(key, {"label": label, "count": 0, "active_ids": []})
                item["count"] += 1
                migrated += 1

            initial_backfill_allowed = not bool(marker.get("initial_backfill_done"))
            for reply_message_id, record in map_by_group.get(chat_id, []):
                record_id = str(
                    record.get("monthly_record_id")
                    or f"{chat_id}:{int(reply_message_id)}"
                )
                old_row = legacy_by_id.get(record_id)
                if old_row:
                    label, key = normalize_khaifa(
                        old_row.get("khaifa") or old_row.get("khaifa_key")
                    )
                elif initial_backfill_allowed:
                    label, key = _entry_khaifa(record)
                    if label and key:
                        item = grouped.setdefault(
                            key, {"label": label, "count": 0, "active_ids": []}
                        )
                        item["count"] += 1
                        migrated += 1
                else:
                    # A previously cleared month must not be repopulated from
                    # old daily reply mappings.
                    continue

                if not label or not key:
                    continue
                item = grouped.setdefault(
                    key, {"label": label, "count": 0, "active_ids": []}
                )
                if record_id in legacy_by_id or initial_backfill_allowed:
                    if record_id not in item["active_ids"]:
                        item["active_ids"].append(record_id)
                    record["monthly_record_id"] = record_id
                    record["monthly_key"] = key

            # Replace any partially written v2 counters with exact values
            # derived from the legacy source before marking the migration done.
            counters.delete_many({"chat_id": chat_id})
            for key, item in grouped.items():
                counters.update_one(
                    {"_id": _counter_id(chat_id, key)},
                    {"$set": {
                        "chat_id": chat_id,
                        "khaifa_key": key,
                        "khaifa": item["label"],
                        "count": int(item["count"]),
                        "active_ids": list(dict.fromkeys(item["active_ids"])),
                    }},
                    upsert=True,
                )

            meta.update_one(
                {"_id": chat_id},
                {"$set": {
                    "initial_backfill_done": True,
                    "storage_version": STORAGE_VERSION,
                }},
                upsert=True,
            )
            legacy.delete_many({"chat_id": chat_id})

        return migrated
    except Exception:
        # Keep legacy data untouched when migration cannot be completed.
        return 0


def get_totals(db, chat_id):
    if db is None:
        return None
    try:
        _ensure_index(db)
        rows = db[COLLECTION].find({"chat_id": str(chat_id)})
        totals = [
            {
                "khaifa": str(row.get("khaifa") or row.get("khaifa_key") or ""),
                "count": int(row.get("count", 0) or 0),
            }
            for row in rows
            if int(row.get("count", 0) or 0) > 0
        ]
        return sorted(totals, key=lambda row: str(row["khaifa"]).casefold())
    except Exception:
        return None


def remove_record(db, chat_id, record_id):
    """Reverse an active record once; old IDs after clear cannot touch new counts."""
    if not record_id:
        return True
    if db is None:
        return False
    try:
        _ensure_index(db)
        with _write_lock:
            db[COLLECTION].update_one(
                {
                    "chat_id": str(chat_id),
                    "active_ids": str(record_id),
                    "count": {"$gt": 0},
                },
                {
                    "$inc": {"count": -1},
                    "$pull": {"active_ids": str(record_id)},
                },
            )
        # No matching active ID is expected after a daily or monthly clear.
        return True
    except Exception:
        return False


def clear_active_records(db, chat_id):
    """Forget current-day reversal IDs without changing month-to-date totals."""
    if db is None:
        return None
    try:
        _ensure_index(db)
        with _write_lock:
            result = db[COLLECTION].update_many(
                {"chat_id": str(chat_id)},
                {"$set": {"active_ids": []}},
            )
        return int(getattr(result, "modified_count", 0) or 0)
    except Exception:
        return None


def clear_records(db, chat_id):
    """Clear one group's monthly counters in a single bulk delete."""
    if db is None:
        return None
    try:
        collection = db[COLLECTION]
        rows = list(collection.find({"chat_id": str(chat_id)}, {"count": 1}))
        total = sum(int(row.get("count", 0) or 0) for row in rows)
        collection.delete_many({"chat_id": str(chat_id)})
        return total
    except Exception:
        return None


def get_all_summary(db):
    """Return a safe preview of all groups' monthly records before global clear."""
    if db is None:
        return None
    try:
        counter_rows = list(
            db[COLLECTION].find({}, {"chat_id": 1, "count": 1})
        )
        legacy_rows = list(
            db[LEGACY_COLLECTION].find({}, {"chat_id": 1})
        )
        group_ids = {
            str(row.get("chat_id"))
            for row in counter_rows + legacy_rows
            if row.get("chat_id") is not None
        }
        submission_count = sum(
            max(0, int(row.get("count", 0) or 0))
            for row in counter_rows
        ) + len(legacy_rows)
        return {
            "groups": len(group_ids),
            "submissions": submission_count,
            "counter_documents": len(counter_rows),
            "legacy_documents": len(legacy_rows),
        }
    except Exception:
        return None


def clear_all_records(db):
    """Clear Monthly Total counters for every group, preserving migration markers.

    The one-time migration markers must remain so old daily reply mappings can
    never repopulate a manually cleared month after the next bot restart.
    """
    summary = get_all_summary(db)
    if summary is None:
        return None
    try:
        db[COLLECTION].delete_many({})
        # Remove any unmigrated legacy records too; never touch monthly_total_meta.
        db[LEGACY_COLLECTION].delete_many({})
        return summary
    except Exception:
        return None


def _render_custom_text(get_text, key, fallback, **fmt):
    """Resolve a /setmsg value with safe placeholder substitution."""
    if get_text is not None:
        try:
            return str(get_text(key, **fmt))
        except Exception:
            pass

    raw = str(fallback or "")
    if not fmt:
        return raw

    return re.sub(
        r"\{(\w+)\}",
        lambda match: str(fmt[match.group(1)])
        if match.group(1) in fmt else match.group(0),
        raw,
    )


async def _send_custom_message(
    target, key, fallback, *, send_custom=None, get_text=None,
    reply_markup=None, **fmt
):
    if send_custom is not None:
        await send_custom(
            target, key, reply_markup=reply_markup, **fmt
        )
        return
    await target.reply_text(
        _render_custom_text(get_text, key, fallback, **fmt),
        reply_markup=reply_markup,
    )


async def _edit_custom_message(
    query, key, fallback, *, edit_custom=None, get_text=None,
    reply_markup=None, **fmt
):
    if edit_custom is not None:
        await edit_custom(
            query, key, reply_markup=reply_markup, **fmt
        )
        return
    await query.edit_message_text(
        _render_custom_text(get_text, key, fallback, **fmt),
        reply_markup=reply_markup,
    )


def _monthly_button(
    button_factory, key, fallback_label, callback_data, style
):
    if button_factory is not None:
        return button_factory(key, callback_data, style)
    return __import__("telegram").InlineKeyboardButton(
        fallback_label,
        callback_data=callback_data,
        style=style,
    )


async def total_command(
    update, context, *, get_db, get_text=None, send_custom=None,
    button_factory=None
):
    message, chat = update.effective_message, update.effective_chat
    if message is None or chat is None:
        return
    if chat.type not in {"group", "supergroup"}:
        await _send_custom_message(
            message,
            "monthly_total_group_only",
            "Monthly Total ကို Group Chat ထဲတွင်သာ အသုံးပြုနိုင်ပါသည်။",
            send_custom=send_custom,
            get_text=get_text,
        )
        return

    totals = get_totals(get_db(), chat.id)
    if totals is None:
        await _send_custom_message(
            message,
            "monthly_total_db_error",
            "❌ Monthly Total Data ကို Database မှ ဖတ်၍မရပါ။",
            send_custom=send_custom,
            get_text=get_text,
        )
        return

    rows = [
        _render_custom_text(
            get_text,
            "monthly_total_row",
            "{khaifa}   -   {count}",
            khaifa=item["khaifa"],
            count=item["count"],
        )
        for item in totals
    ]
    empty = ""
    if not rows:
        empty = _render_custom_text(
            get_text,
            "monthly_total_empty",
            EMPTY,
        )
    warning = _render_custom_text(
        get_text,
        "monthly_total_warning",
        WARNING,
    )

    button = _monthly_button(
        button_factory,
        "monthly_total_clear_button",
        "Monthly Clear Data",
        "monthly_clear_start",
        "danger",
    )
    from telegram import InlineKeyboardMarkup
    await _send_custom_message(
        message,
        "monthly_total_message",
        "📊 Monthly Total\n\n{totals}{empty}\n\n{warning}",
        send_custom=send_custom,
        get_text=get_text,
        reply_markup=InlineKeyboardMarkup([[button]]),
        totals="\n".join(rows),
        empty=empty,
        warning=warning,
    )


async def clear_callback(
    update, context, *, get_db, is_group_admin, get_text=None,
    edit_custom=None, button_factory=None
):
    query = update.callback_query
    if query is None or query.message is None:
        if query:
            await query.answer()
        return
    chat, user = query.message.chat, query.from_user

    def _alert(key, fallback):
        return _render_custom_text(get_text, key, fallback)

    if chat.type not in {"group", "supergroup"} or user is None:
        await query.answer(
            _alert(
                "monthly_total_group_only",
                "Group ထဲတွင်သာ အသုံးပြုနိုင်ပါသည်။",
            ),
            show_alert=True,
        )
        return

    chat_id, data = int(chat.id), query.data or ""

    if data == "monthly_clear_start":
        if not await is_group_admin(context, user.id, chat_id):
            await query.answer(
                _alert(
                    "monthly_total_admin_only",
                    "🔒 Group Owner/Admin သာ Monthly Clear Data ကို အသုံးပြုနိုင်ပါသည်။",
                ),
                show_alert=True,
            )
            return
        totals = get_totals(get_db(), chat_id)
        if totals is None:
            await query.answer(
                _alert(
                    "monthly_total_db_error",
                    "❌ Database မှ ဖတ်၍မရပါ။",
                ),
                show_alert=True,
            )
            return
        if not totals:
            await query.answer(
                _alert(
                    "monthly_total_no_data",
                    "Monthly Total data မရှိသေးပါ။",
                ),
                show_alert=True,
            )
            return

        token = secrets.token_urlsafe(6)
        context.chat_data["monthly_clear_pending"] = {
            "token": token,
            "user_id": int(user.id),
            "created_at": datetime.now().timestamp(),
        }
        from telegram import InlineKeyboardMarkup
        confirm = _monthly_button(
            button_factory,
            "monthly_total_confirm_button",
            "Confirm",
            f"monthly_clear_confirm_{token}",
            "success",
        )
        cancel = _monthly_button(
            button_factory,
            "monthly_total_cancel_button",
            "Cancel",
            f"monthly_clear_cancel_{token}",
            "danger",
        )
        keyboard = InlineKeyboardMarkup([[confirm, cancel]])
        count = sum(int(item.get("count", 0)) for item in totals)
        await query.answer()
        await _edit_custom_message(
            query,
            "monthly_total_confirm_prompt",
            "⚠️ ဤ Group ၏ Monthly Total Data အားလုံးကို Database မှ ရှင်းလင်းမည်။\n\nဆက်လုပ်မည်လား?",
            edit_custom=edit_custom,
            get_text=get_text,
            reply_markup=keyboard,
            group_name=getattr(chat, "title", None) or str(chat_id),
            count=count,
        )
        return

    match = re.fullmatch(
        r"monthly_clear_(confirm|cancel)_([A-Za-z0-9_-]{8,20})",
        data,
    )
    if not match:
        await query.answer()
        return

    action, token = match.groups()
    pending = context.chat_data.get("monthly_clear_pending") or {}
    created = pending.get("created_at")
    expired = (
        not isinstance(created, (int, float))
        or datetime.now().timestamp() - created > CLEAR_TTL_SECONDS
    )
    if (
        expired
        or pending.get("token") != token
        or int(pending.get("user_id", 0)) != int(user.id)
    ):
        context.chat_data.pop("monthly_clear_pending", None)
        await query.answer(
            _alert(
                "monthly_total_expired",
                "⌛ Confirmation သက်တမ်းကုန်သွားပါပြီ။",
            ),
            show_alert=True,
        )
        return

    if not await is_group_admin(context, user.id, chat_id):
        await query.answer(
            _alert(
                "monthly_total_admin_only",
                "🔒 Group Owner/Admin သာ အသုံးပြုနိုင်ပါသည်။",
            ),
            show_alert=True,
        )
        return

    if action == "cancel":
        context.chat_data.pop("monthly_clear_pending", None)
        await query.answer()
        await _edit_custom_message(
            query,
            "monthly_total_cancelled",
            "↩️ Monthly Clear Data ကို ပယ်ဖျက်လိုက်ပါပြီ။ Data များ မပြောင်းလဲပါ။",
            edit_custom=edit_custom,
            get_text=get_text,
        )
        return

    count = clear_records(get_db(), chat_id)
    if count is None:
        context.chat_data.pop("monthly_clear_pending", None)
        await query.answer()
        await _edit_custom_message(
            query,
            "monthly_total_clear_failed",
            "❌ Database error — Monthly Data များကို မဖျက်နိုင်ပါ။",
            edit_custom=edit_custom,
            get_text=get_text,
        )
        return

    context.chat_data.pop("monthly_clear_pending", None)
    if context.application.persistence:
        await context.application.persistence.flush()
    await query.answer()
    await _edit_custom_message(
        query,
        "monthly_total_cleared",
        "✅ Monthly Total Data ရှင်းလင်းပြီးပါပြီ။\n🗑️ Record {count} ခု ဖျက်ပြီးပါပြီ။",
        edit_custom=edit_custom,
        get_text=get_text,
        count=count,
    )
