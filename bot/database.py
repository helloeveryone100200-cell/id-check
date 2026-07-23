"""
database.py — MongoDB helper functions for the Telegram submission bot.
"""

import logging
import os
from datetime import datetime, timezone

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure

logger = logging.getLogger(__name__)

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
        # Quick connectivity check
        _client.admin.command("ping")
        db_name = os.getenv("MONGO_DB_NAME", "telegram_bot")
        _db = _client[db_name]
        logger.info("Connected to MongoDB (db: %s)", db_name)
    except (ConnectionFailure, OperationFailure) as exc:
        logger.error("Failed to connect to MongoDB: %s", exc)
        _client = None
        _db = None

    return _db


# ---------------------------------------------------------------------------
# submissions collection
# ---------------------------------------------------------------------------

def _submissions(db):
    return db["submissions"]


def check_duplicate(db, phone_number: str, whatsapp_number: str | None = None, id_number: str | None = None):
    """
    Check whether any field already exists in the submissions collection.

    Required : phone_number
    Optional : whatsapp_number, id_number (only checked when provided)

    Returns a dict with keys:
      - 'found'   (bool)      — whether a duplicate was detected
      - 'doc'     (dict|None) — the first matching document
      - 'field'   (str|None)  — which field triggered the duplicate
    """
    coll = _submissions(db)

    queries = [("phone_number", {"phone_number": phone_number})]
    if whatsapp_number:
        queries.append(("whatsapp_number", {"whatsapp_number": whatsapp_number}))
    if id_number:
        queries.append(("id_number", {"id_number": id_number}))

    for field, query in queries:
        doc = coll.find_one(query)
        if doc:
            return {"found": True, "doc": doc, "field": field}

    return {"found": False, "doc": None, "field": None}


def save_submission(
    db,
    *,
    telegram_id: int,
    telegram_username: str,
    username: str,
    phone_number: str,
    whatsapp_number: str | None = None,
    id_number: str | None = None,
) -> bool:
    """Insert a new submission. Returns True on success."""
    coll = _submissions(db)
    doc = {
        "telegram_id": telegram_id,
        "telegram_username": telegram_username,
        "username": username.lower(),
        "phone_number": phone_number,
        "created_at": datetime.now(timezone.utc),
    }
    if whatsapp_number:
        doc["whatsapp_number"] = whatsapp_number
    if id_number:
        doc["id_number"] = id_number.lower()

    try:
        coll.insert_one(doc)
        return True
    except Exception as exc:
        logger.error("Failed to save submission: %s", exc)
        return False


# ---------------------------------------------------------------------------
# bot_settings collection
# ---------------------------------------------------------------------------

def _settings(db):
    return db["bot_settings"]


DEFAULT_DUPLICATE_MSG = (
    "⚠️ <b>Duplicate detected!</b>\n\n"
    "Hey {user_mention}, this data was previously submitted by <b>{original_user}</b>.\n"
    "Duplicate field: <b>{matched_field}</b>"
)

DEFAULT_START_MSG = (
    "👋 Hello, <b>{name}</b>!\n\n"
    "I'm a <b>duplicate submission checker bot</b>.\n\n"
    "📋 <b>How it works:</b>\n"
    "Send a message in the group with this format:\n\n"
    "<code>Username - your_username\n"
    "Phone number - 09xxxxxxxxx\n"
    "Whatsapp number - 09xxxxxxxxx\n"
    "ID - (optional)</code>\n\n"
    "I will automatically check for duplicates and notify if any are found."
)


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
