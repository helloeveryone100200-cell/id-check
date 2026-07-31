"""
database.py — MongoDB helper functions for the Telegram submission bot.

Changes vs original:
  - get_db(): live-ping reconnect guard added (stale connection detection)
  - update_submitter(): all fields keyword-only + optional to prevent cross-
    record data corruption when ID doc ≠ phone doc
  - find_by_whatsapp(): new read-only lookup
  - find_by_username(): new read-only lookup
  - normalize_username(): new helper (strips leading @, lowercases)
  - check_duplicate(): still present and now actually used by the bot
"""

from __future__ import annotations

import logging
import os
import re as _re
from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, OperationFailure, PyMongoError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Text normalisation helpers
# ---------------------------------------------------------------------------

_RE_EMOJI = _re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002300-\U000023FF"
    "\U00002600-\U000027BF"
    "\U0000FE00-\U0000FE0F"
    "\U0001F004"
    "\U0001F0CF"
    "\u200D"
    "\u20E3"
    "]+",
    flags=_re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Remove all emoji and Unicode pictographic characters from *text*."""
    return _RE_EMOJI.sub("", text).strip()


def normalize_phone(number: str) -> str:
    """Normalise a phone/WhatsApp number: strip emoji then remove spaces/dashes/dots/parens."""
    return _re.sub(r"[\s\-\.\(\)]", "", strip_emoji(number))


def normalize_id(id_number: str) -> str:
    """Normalise an ID number: strip emoji, collapse whitespace, lowercase."""
    return strip_emoji(id_number).lower()


def normalize_username(username: str) -> str:
    """Strip leading @ and lowercase so @Foo == foo == @foo."""
    return username.lstrip("@").strip().lower()


# ---------------------------------------------------------------------------
# Connection — lazy singleton with live-ping reconnect guard
# ---------------------------------------------------------------------------

_client: MongoClient | None = None
_db = None


def get_db():
    """Return a database handle, initialising the client on first call.

    If the connection was established earlier but has since dropped, a fast
    ping detects the failure and triggers a re-connection attempt so the bot
    recovers from transient network errors without a restart.
    """
    global _client, _db

    if _db is not None:
        try:
            _client.admin.command("ping", maxTimeMS=1_000)
            return _db
        except Exception:
            logger.warning("MongoDB connection lost — attempting to reconnect…")
            _client = None
            _db = None

    mongo_uri = os.getenv("MONGO_URI", "")
    if not mongo_uri:
        logger.warning(
            "MONGO_URI is not set. Database operations will fail until it is provided."
        )
        return None

    try:
        _client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5_000)
        _client.admin.command("ping")
        db_name = os.getenv("MONGO_DB_NAME", "telegram_bot")
        _db = _client[db_name]
        _ensure_indexes(_db)
        logger.info("Connected to MongoDB (db: %s)", db_name)
    except (ConnectionFailure, OperationFailure) as exc:
        logger.error("Failed to connect to MongoDB: %s", exc)
        _client = None
        _db = None

    return _db


# ---------------------------------------------------------------------------
# submissions collection
# ---------------------------------------------------------------------------

def _ensure_indexes(db) -> None:
    """Create indexes on frequently queried fields (idempotent)."""
    coll = db["submissions"]
    coll.create_index([("phone_number",    ASCENDING)],              name="idx_phone")
    coll.create_index([("whatsapp_number", ASCENDING)], sparse=True, name="idx_whatsapp")
    coll.create_index([("id_number",       ASCENDING)], sparse=True, name="idx_id")
    coll.create_index([("username",        ASCENDING)], sparse=True, name="idx_username")
    coll.create_index([("created_at",      ASCENDING)],              name="idx_created_at")
    logger.info("MongoDB indexes ensured")


def _submissions(db):
    return db["submissions"]


# ── Read-only lookups ────────────────────────────────────────────────────────

def find_by_id(db, id_number: str) -> dict | None:
    """Read-only lookup by id_number. Returns the document or None."""
    return _submissions(db).find_one({"id_number": normalize_id(id_number)})


def find_by_phone(db, phone_number: str) -> dict | None:
    """Read-only lookup by phone_number. Returns the document or None."""
    return _submissions(db).find_one({"phone_number": normalize_phone(phone_number)})


def find_by_whatsapp(db, whatsapp_number: str) -> dict | None:
    """Read-only lookup by whatsapp_number. Returns the document or None."""
    return _submissions(db).find_one({"whatsapp_number": normalize_phone(whatsapp_number)})


def find_by_username(db, username: str) -> dict | None:
    """Read-only lookup by username (case-insensitive, leading @ stripped)."""
    return _submissions(db).find_one({"username": normalize_username(username)})


def check_duplicate(
    db,
    phone_number: str,
    whatsapp_number: str | None = None,
    id_number: str | None = None,
    username: str | None = None,
) -> dict:
    """Check whether any of the supplied fields already exist.

    Returns:
        {
          "found":   bool,
          "doc":     first matching document | None,
          "matches": [{"field": str, "value": str}, ...]
        }
    """
    coll = _submissions(db)

    norm_phone    = normalize_phone(phone_number)
    norm_whatsapp = normalize_phone(whatsapp_number) if whatsapp_number else None
    norm_id       = normalize_id(id_number)          if id_number       else None
    norm_username = normalize_username(username)      if username and username.strip() else None

    candidates = [("phone_number", {"phone_number": norm_phone}, norm_phone)]
    if norm_whatsapp:
        candidates.append(("whatsapp_number", {"whatsapp_number": norm_whatsapp}, norm_whatsapp))
    if norm_id:
        candidates.append(("id_number", {"id_number": norm_id}, norm_id))
    if norm_username:
        candidates.append(("username", {"username": norm_username}, norm_username))

    matches: list[dict] = []
    first_doc = None
    for field_name, query, value in candidates:
        doc = coll.find_one(query)
        if doc:
            matches.append({"field": field_name, "value": value})
            if first_doc is None:
                first_doc = doc

    return {"found": bool(matches), "doc": first_doc, "matches": matches}


# ── Write operations ─────────────────────────────────────────────────────────

def save_submission(
    db,
    telegram_id: int,
    telegram_username: str,
    username: str,
    phone_number: str,
    whatsapp_number: str | None,
    id_number: str | None,
) -> bool:
    """Persist a new (non-duplicate) submission. Returns True on success."""
    coll = _submissions(db)
    now  = datetime.now(timezone.utc)
    doc: dict = {
        "telegram_id":       telegram_id,
        "telegram_username": telegram_username,
        "phone_number":      normalize_phone(phone_number),
        "check_count":       0,
        "created_at":        now,
        "updated_at":        now,
    }
    if username:
        doc["username"] = normalize_username(username)
    if whatsapp_number:
        doc["whatsapp_number"] = normalize_phone(whatsapp_number)
    if id_number:
        doc["id_number"] = normalize_id(id_number)
    try:
        coll.insert_one(doc)
        return True
    except PyMongoError as exc:
        logger.error("Failed to insert submission: %s", exc)
        return False


def update_submitter(
    db,
    doc_id,
    *,
    new_telegram_id: int,
    new_telegram_username: str,
    new_phone_number:    str | None = None,
    new_whatsapp_number: str | None = None,
    new_username:        str | None = None,
    new_id_number:       str | None = None,
) -> None:
    """Update submitter identity on a matched document and increment check_count.

    All content fields are keyword-only and optional so callers never
    accidentally overwrite a field that belongs to a *different* matched record.
    Only fields explicitly provided (non-None) are written.
    """
    coll = _submissions(db)
    now  = datetime.now(timezone.utc)

    set_fields: dict = {
        "telegram_id":       new_telegram_id,
        "telegram_username": new_telegram_username,
        "updated_at":        now,
    }
    if new_phone_number is not None:
        set_fields["phone_number"] = normalize_phone(new_phone_number)
    if new_whatsapp_number is not None:
        set_fields["whatsapp_number"] = normalize_phone(new_whatsapp_number)
    if new_username is not None:
        set_fields["username"] = normalize_username(new_username)
    if new_id_number is not None:
        set_fields["id_number"] = normalize_id(new_id_number)

    try:
        coll.update_one(
            {"_id": doc_id},
            {"$set": set_fields, "$inc": {"check_count": 1}},
        )
    except PyMongoError as exc:
        logger.error("Failed to update submitter for doc %s: %s", doc_id, exc)


# ---------------------------------------------------------------------------
# settings collection
# ---------------------------------------------------------------------------

DEFAULT_DUPLICATE_MSG = (
    "⚠️ Duplicate submission detected!\n"
    "{duplicate_fields}\n\n"
    "Previously by <b>{original_user}</b> on <b>{date}</b>\n"
    "🔢 Check count: {count}"
)

DEFAULT_START_MSG = (
    "👋 Welcome, {name}!\n\n"
    "🤖 <b>What I do:</b>\n"
    "I monitor group messages and automatically flag duplicate submissions "
    "(phone numbers, WhatsApp numbers, IDs, usernames).\n\n"
    "📋 <b>Group submission format:</b>\n"
    "<code>Phone number - 09xxxxxxxxx</code>\n"
    "<code>Whatsapp number - 09xxxxxxxxx</code> (optional)\n"
    "<code>ID - A123456</code> (optional)\n"
    "<code>Username - @yourname</code> (optional)"
)


def _settings(db):
    return db["settings"]


def _get_setting(db, key: str, default: str) -> str:
    doc = _settings(db).find_one({"_id": key})
    if doc and doc.get("value"):
        return doc["value"]
    return default


def _set_setting(db, key: str, message: str) -> bool:
    try:
        _settings(db).update_one(
            {"_id": key},
            {"$set": {"value": message}},
            upsert=True,
        )
        return True
    except PyMongoError as exc:
        logger.error("Failed to set %s: %s", key, exc)
        return False


def get_duplicate_msg(db) -> str:
    return _get_setting(db, "duplicate_msg", DEFAULT_DUPLICATE_MSG)


def set_duplicate_msg(db, message: str) -> bool:
    return _set_setting(db, "duplicate_msg", message)


def get_start_msg(db) -> str:
    return _get_setting(db, "start_msg", DEFAULT_START_MSG)


def set_start_msg(db, message: str) -> bool:
    return _set_setting(db, "start_msg", message)


def reset_setting(db, key: str) -> bool:
    """Delete a custom setting so the built-in default is used again."""
    try:
        _settings(db).delete_one({"_id": key})
        return True
    except PyMongoError as exc:
        logger.error("Failed to reset %s: %s", key, exc)
        return False


# ---------------------------------------------------------------------------
# Field animated-emoji settings
# ---------------------------------------------------------------------------

FIELD_ALIASES: dict[str, str] = {
    "phone":    "phone_number",
    "whatsapp": "whatsapp_number",
    "id":       "id_number",
    "username": "username",
}

FIELD_EMOJI_DEFAULTS: dict[str, dict] = {
    "phone_number":    {"fallback": "📞", "emoji_id": None},
    "whatsapp_number": {"fallback": "💬", "emoji_id": None},
    "id_number":       {"fallback": "🪪", "emoji_id": None},
    "username":        {"fallback": "👤", "emoji_id": None},
}


def get_field_emojis(db) -> dict:
    """Return {field_name: {fallback, emoji_id}} for all four fields."""
    coll = _settings(db)
    result: dict = {}
    for field, default in FIELD_EMOJI_DEFAULTS.items():
        doc = coll.find_one({"_id": f"emoji_{field}"})
        if doc:
            result[field] = {
                "fallback": doc.get("fallback", default["fallback"]),
                "emoji_id": doc.get("emoji_id"),
            }
        else:
            result[field] = default.copy()
    return result


def set_field_emoji(db, field: str, emoji_id: str, fallback: str) -> bool:
    """Save an animated-emoji ID + fallback character for one field."""
    try:
        _settings(db).update_one(
            {"_id": f"emoji_{field}"},
            {"$set": {"emoji_id": emoji_id, "fallback": fallback}},
            upsert=True,
        )
        return True
    except PyMongoError as exc:
        logger.error("Failed to set emoji for %s: %s", field, exc)
        return False


def reset_field_emoji(db, field: str) -> bool:
    """Remove the custom emoji for a field so the plain default is used."""
    try:
        _settings(db).delete_one({"_id": f"emoji_{field}"})
        return True
    except PyMongoError as exc:
        logger.error("Failed to reset emoji for %s: %s", field, exc)
        return False


# ---------------------------------------------------------------------------
# Start inline-button settings
# ---------------------------------------------------------------------------

def get_start_buttons(db) -> list:
    """Return custom start buttons: [{"text": str, "url": str}, ...]"""
    doc = _settings(db).find_one({"_id": "start_buttons"})
    if doc and isinstance(doc.get("buttons"), list):
        return doc["buttons"]
    return []


def add_start_button(db, text: str, url: str) -> bool:
    """Append a new button to the custom start-button list."""
    try:
        _settings(db).update_one(
            {"_id": "start_buttons"},
            {"$push": {"buttons": {"text": text, "url": url}}},
            upsert=True,
        )
        return True
    except PyMongoError as exc:
        logger.error("Failed to add start button: %s", exc)
        return False


def remove_start_button(db, index: int) -> bool:
    """Remove button at 1-based *index*. Returns False when out of range."""
    buttons = get_start_buttons(db)
    idx = index - 1
    if idx < 0 or idx >= len(buttons):
        return False
    buttons.pop(idx)
    try:
        _settings(db).update_one(
            {"_id": "start_buttons"},
            {"$set": {"buttons": buttons}},
            upsert=True,
        )
        return True
    except PyMongoError as exc:
        logger.error("Failed to remove start button: %s", exc)
        return False


def reset_start_buttons(db) -> bool:
    """Remove all custom start buttons (revert to defaults only)."""
    try:
        _settings(db).delete_one({"_id": "start_buttons"})
        return True
    except PyMongoError as exc:
        logger.error("Failed to reset start buttons: %s", exc)
        return False
