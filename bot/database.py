"""
database.py — MongoDB helper functions for the Telegram submission bot.
"""

import logging
import os
import re as _re
from datetime import datetime, timezone

from pymongo import MongoClient, ASCENDING
from pymongo.errors import ConnectionFailure, OperationFailure

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Matches every Unicode emoji / pictograph / variation-selector block.
_RE_EMOJI = _re.compile(
    "["
    "\U0001F600-\U0001F64F"   # emoticons
    "\U0001F300-\U0001F5FF"   # symbols & pictographs
    "\U0001F680-\U0001F6FF"   # transport & map
    "\U0001F1E0-\U0001F1FF"   # regional-indicator (flags)
    "\U0001F900-\U0001F9FF"   # supplemental symbols & pictographs
    "\U0001FA00-\U0001FA6F"   # chess / other symbols
    "\U0001FA70-\U0001FAFF"   # food / drink / misc
    "\U00002300-\U000023FF"   # misc technical
    "\U00002600-\U000027BF"   # misc symbols
    "\U0000FE00-\U0000FE0F"   # variation selectors (VS-1 … VS-16)
    "\U0001F004"              # mahjong tile
    "\U0001F0CF"              # joker
    "\u200D"                  # zero-width joiner
    "\u20E3"                  # combining enclosing keycap
    "]+",
    flags=_re.UNICODE,
)


def strip_emoji(text: str) -> str:
    """Remove all emoji and Unicode pictographic characters from *text*."""
    return _RE_EMOJI.sub("", text).strip()


def normalize_phone(number: str) -> str:
    """Normalise a phone/WhatsApp number for storage and lookup.

    Steps:
      1. Strip all emoji (e.g. ``09🌟12345`` → ``0912345``)
      2. Remove formatting chars: spaces, dashes, dots, parentheses
    """
    return _re.sub(r"[\s\-\.\(\)]", "", strip_emoji(number))


def normalize_id(id_number: str) -> str:
    """Normalise an ID number for storage and lookup.

    Steps:
      1. Strip all emoji
      2. Collapse surrounding whitespace
      3. Lowercase (IDs like ``12ABC`` == ``12abc``)
    """
    return strip_emoji(id_number).lower()

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

_client: MongoClient | None = None
_db = None


def get_db():
    """Return a database handle, initialising the client on first call."""
    global _client, _db

    if _db is not None:
        return _db

    mongo_uri = os.getenv("MONGO_URI", "")
    if not mongo_uri:
        logger.warning(
            "MONGO_URI is not set. Database operations will fail until it is provided."
        )
        return None

    try:
        _client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
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
    coll.create_index([("phone_number", ASCENDING)], name="idx_phone")
    coll.create_index([("whatsapp_number", ASCENDING)], sparse=True, name="idx_whatsapp")
    coll.create_index([("id_number", ASCENDING)], sparse=True, name="idx_id")
    coll.create_index([("username", ASCENDING)], sparse=True, name="idx_username")
    coll.create_index([("created_at", ASCENDING)], name="idx_created_at")
    logger.info("MongoDB indexes ensured")


def _submissions(db):
    return db["submissions"]


def check_duplicate(
    db,
    phone_number: str,
    whatsapp_number: str | None = None,
    id_number: str | None = None,
    username: str | None = None,
):
    """
    Check whether any field already exists in the submissions collection.
    Returns a dict with keys:
      - 'found'   (bool)       — whether any duplicate was detected
      - 'doc'     (dict|None)  — the first matching document
      - 'matches' (list)       — list of {"field": str, "value": str}
    """
    coll = _submissions(db)

    norm_phone    = normalize_phone(phone_number)
    norm_whatsapp = normalize_phone(whatsapp_number) if whatsapp_number else None
    norm_id       = normalize_id(id_number) if id_number else None
    norm_username = username.strip().lower() if username and username.strip() else None

    queries = [("phone_number", {"phone_number": norm_phone}, norm_phone)]
    if norm_whatsapp:
        queries.append(("whatsapp_number", {"whatsapp_number": norm_whatsapp}, norm_whatsapp))
    if norm_id:
        queries.append(("id_number", {"id_number": norm_id}, norm_id))
    if norm_username:
        queries.append(("username", {"username": norm_username}, norm_username))

    matches = []
    first_doc = None
    for field_name, query, value in queries:
        doc = coll.find_one(query)
        if doc:
            matches.append({"field": field_name, "value": value})
            if first_doc is None:
                first_doc = doc

    return {
        "found": bool(matches),
        "doc": first_doc,
        "matches": matches,
    }


def save_submission(
    db,
    telegram_id: int,
    telegram_username: str,
    username: str,
    phone_number: str,
    whatsapp_number: str | None,
    id_number: str | None,
) -> bool:
    """Save a new submission. Returns True on success."""
    coll = _submissions(db)
    now = datetime.now(timezone.utc)
    # Build doc without None/empty optional fields to save MongoDB space.
    doc: dict = {
        "telegram_id":       telegram_id,
        "telegram_username": telegram_username,
        "phone_number":      normalize_phone(phone_number),
        "check_count":       0,
        "created_at":        now,
        "updated_at":        now,
    }
    if username:
        doc["username"] = username.lower()
    if whatsapp_number:
        doc["whatsapp_number"] = normalize_phone(whatsapp_number)
    if id_number:
        doc["id_number"] = normalize_id(id_number)
    try:
        coll.insert_one(doc)
        return True
    except Exception as exc:
        logger.error("Failed to insert submission: %s", exc)
        return False


def find_by_id(db, id_number: str) -> dict | None:
    """Read-only lookup by id_number. Returns the document or None."""
    return _submissions(db).find_one({"id_number": normalize_id(id_number)})


def find_by_phone(db, phone_number: str) -> dict | None:
    """Read-only lookup by phone_number. Returns the document or None."""
    return _submissions(db).find_one({"phone_number": normalize_phone(phone_number)})


def update_submitter(
    db,
    doc_id,
    new_telegram_id: int,
    new_telegram_username: str,
    new_phone_number: str,
    new_username: str,
    new_id_number: str | None,
) -> None:
    """Replace submitter fields on an existing document and increment check_count."""
    coll = _submissions(db)
    now = datetime.now(timezone.utc)
    set_fields: dict = {
        "telegram_id":       new_telegram_id,
        "telegram_username": new_telegram_username,
        "phone_number":      normalize_phone(new_phone_number) if new_phone_number else "",
        "updated_at":        now,
    }
    if new_username:
        set_fields["username"] = new_username.lower()
    if new_id_number:
        set_fields["id_number"] = normalize_id(new_id_number)
    try:
        coll.update_one(
            {"_id": doc_id},
            {"$set": set_fields, "$inc": {"check_count": 1}},
        )
    except Exception as exc:
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

# Placeholders available in the duplicate message template:
#   {duplicate_fields} — auto-built: shows which field(s) were duplicated
#                        (ID only / phone only / both, with their values)
#   {original_user}    — previous submitter (@username or display name)
#   {date}             — date the previous record was registered (DD/MM/YY)
#   {count}            — running total of how many times this record was checked

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
    coll = _settings(db)
    doc = coll.find_one({"_id": key})
    if doc and doc.get("value"):
        return doc["value"]
    return default


def _set_setting(db, key: str, message: str) -> bool:
    coll = _settings(db)
    try:
        coll.update_one(
            {"_id": key},
            {"$set": {"value": message}},
            upsert=True,
        )
        return True
    except Exception as exc:
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
    """Delete a custom setting so the default is used again."""
    coll = _settings(db)
    try:
        coll.delete_one({"_id": key})
        return True
    except Exception as exc:
        logger.error("Failed to reset %s: %s", key, exc)
        return False


# ---------------------------------------------------------------------------
# Field animated-emoji settings
# ---------------------------------------------------------------------------

# Short alias → internal field name
FIELD_ALIASES = {
    "phone":    "phone_number",
    "whatsapp": "whatsapp_number",
    "id":       "id_number",
    "username": "username",
}

# Default plain emojis used when no custom animated emoji is configured
FIELD_EMOJI_DEFAULTS: dict[str, dict] = {
    "phone_number":    {"fallback": "📞", "emoji_id": None},
    "whatsapp_number": {"fallback": "💬", "emoji_id": None},
    "id_number":       {"fallback": "🪪", "emoji_id": None},
    "username":        {"fallback": "👤", "emoji_id": None},
}


def get_field_emojis(db) -> dict:
    """Return {field_name: {fallback, emoji_id}} for all four fields."""
    coll = _settings(db)
    result = {}
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
    """Save an animated emoji ID + fallback for one field."""
    coll = _settings(db)
    try:
        coll.update_one(
            {"_id": f"emoji_{field}"},
            {"$set": {"emoji_id": emoji_id, "fallback": fallback}},
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.error("Failed to set emoji for %s: %s", field, exc)
        return False


def reset_field_emoji(db, field: str) -> bool:
    """Remove custom emoji for a field so the default plain emoji is used."""
    coll = _settings(db)
    try:
        coll.delete_one({"_id": f"emoji_{field}"})
        return True
    except Exception as exc:
        logger.error("Failed to reset emoji for %s: %s", field, exc)
        return False


# ---------------------------------------------------------------------------
# Start inline-button settings
# ---------------------------------------------------------------------------

def get_start_buttons(db) -> list:
    """Return list of custom start buttons: [{"text": str, "url": str}, ...]"""
    coll = _settings(db)
    doc = coll.find_one({"_id": "start_buttons"})
    if doc and isinstance(doc.get("buttons"), list):
        return doc["buttons"]
    return []


def add_start_button(db, text: str, url: str) -> bool:
    """Append a new button to the custom start-button list."""
    coll = _settings(db)
    try:
        coll.update_one(
            {"_id": "start_buttons"},
            {"$push": {"buttons": {"text": text, "url": url}}},
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.error("Failed to add start button: %s", exc)
        return False


def remove_start_button(db, index: int) -> bool:
    """Remove button at 1-based index. Returns False if out of range."""
    buttons = get_start_buttons(db)
    idx = index - 1
    if idx < 0 or idx >= len(buttons):
        return False
    buttons.pop(idx)
    coll = _settings(db)
    try:
        coll.update_one(
            {"_id": "start_buttons"},
            {"$set": {"buttons": buttons}},
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.error("Failed to remove start button: %s", exc)
        return False


def reset_start_buttons(db) -> bool:
    """Remove all custom start buttons (revert to defaults only)."""
    coll = _settings(db)
    try:
        coll.delete_one({"_id": "start_buttons"})
        return True
    except Exception as exc:
        logger.error("Failed to reset start buttons: %s", exc)
        return False
