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


def check_duplicate(db, username: str, phone_number: str, whatsapp_number: str, id_number: str | None = None):
    """
    Check whether any field already exists in the submissions collection.

    Returns a dict with keys:
      - 'found'   (bool)      — whether a duplicate was detected
      - 'doc'     (dict|None) — the first matching document
      - 'field'   (str|None)  — which field triggered the duplicate
    """
    coll = _submissions(db)

    queries = [
        ("username", {"username": username}),
        ("phone_number", {"phone_number": phone_number}),
        ("whatsapp_number", {"whatsapp_number": whatsapp_number}),
    ]
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
    whatsapp_number: str,
    id_number: str | None = None,
) -> bool:
    """Insert a new submission. Returns True on success."""
    coll = _submissions(db)
    doc = {
        "telegram_id": telegram_id,
        "telegram_username": telegram_username,
        "username": username.lower(),
        "phone_number": phone_number,
        "whatsapp_number": whatsapp_number,
        "created_at": datetime.now(timezone.utc),
    }
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


def get_duplicate_msg(db) -> str:
    """Return the custom duplicate warning message, or the default."""
    coll = _settings(db)
    doc = coll.find_one({"_id": "duplicate_msg"})
    if doc and doc.get("value"):
        return doc["value"]
    return DEFAULT_DUPLICATE_MSG


def set_duplicate_msg(db, message: str) -> bool:
    """Upsert the custom duplicate warning message."""
    coll = _settings(db)
    try:
        coll.update_one(
            {"_id": "duplicate_msg"},
            {"$set": {"value": message}},
            upsert=True,
        )
        return True
    except Exception as exc:
        logger.error("Failed to set duplicate_msg: %s", exc)
        return False
