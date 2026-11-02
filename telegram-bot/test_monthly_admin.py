import asyncio
import os
from datetime import datetime as RealDateTime
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_MONTHLY_ADMIN_TEST")

import main as bot


class _FakeMessage:
    def __init__(self, chat_type="private"):
        self.chat = SimpleNamespace(type=chat_type)
        self.sent = []
        self.edited = []

    async def reply_text(self, text, **kwargs):
        self.sent.append((text, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edited.append((text, kwargs))


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query=None, projection=None):
        return [dict(row) for row in self.rows]

    def delete_many(self, query):
        deleted = len(self.rows)
        self.rows.clear()
        return SimpleNamespace(deleted_count=deleted)


class _FakeDB:
    def __init__(self):
        self.collections = {
            "monthly_total_counts": _FakeCollection([
                {"_id": "-1001:文泰", "chat_id": "-1001", "khaifa": "文泰", "count": 12},
                {"_id": "-1002:清风", "chat_id": "-1002", "khaifa": "清风", "count": 8},
            ]),
            "monthly_total_entries": _FakeCollection(),
            "monthly_total_meta": _FakeCollection([
                {"_id": "-1001", "initial_backfill_done": True, "storage_version": 2},
                {"_id": "-1002", "initial_backfill_done": True, "storage_version": 2},
            ]),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, _FakeCollection())


class _FakeQuery:
    def __init__(self, callback_data, user_id, chat_type="private"):
        self.data = callback_data
        self.from_user = SimpleNamespace(id=user_id)
        self.message = SimpleNamespace(
            chat=SimpleNamespace(type=chat_type),
        )
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))


def _button_texts(markup):
    return [
        button.text
        for row in markup.keyboard
        for button in row
    ]


class MonthlyAllClearAdminTests(unittest.TestCase):
    def _menu_for(self, chat_type, user_id):
        message = _FakeMessage(chat_type)
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(
                id=user_id if chat_type == "private" else -100123,
                type=chat_type,
            ),
            effective_user=SimpleNamespace(id=user_id, full_name="Test Admin"),
            message=message,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={}, persistence=None),
            chat_data={},
        )
        asyncio.run(bot.main_menu_command(update, context))
        return message.sent[-1][1]["reply_markup"]

    def test_button_is_visible_only_in_admin_private_menu(self):
        admin_id = bot.ADMIN_IDS[0]
        regular_id = max(bot.ADMIN_IDS) + 12345

        admin_pm = self._menu_for("private", admin_id)
        regular_pm = self._menu_for("private", regular_id)
        admin_group = self._menu_for("supergroup", admin_id)

        self.assertIn("Monthly All Clear Data", _button_texts(admin_pm))
        self.assertNotIn("Monthly All Clear Data", _button_texts(regular_pm))
        self.assertNotIn("Monthly All Clear Data", _button_texts(admin_group))
        self.assertIn("Monthly Total", _button_texts(admin_group))

    def test_non_admin_cannot_start_global_clear(self):
        message = _FakeMessage("private")
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=123456, type="private"),
            effective_user=SimpleNamespace(id=max(bot.ADMIN_IDS) + 12345),
            effective_message=message,
            message=message,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={}, persistence=None),
            user_data={},
        )
        with patch.object(bot, "get_mongo_db", side_effect=AssertionError("DB must not be read")):
            asyncio.run(bot.monthly_all_clear_start(update, context))
        self.assertTrue(message.sent)
        self.assertIn("ADMIN_IDS", message.sent[0][0])

    def test_admin_confirm_clears_all_monthly_collections_and_keeps_migration_meta(self):
        admin_id = bot.ADMIN_IDS[0]
        db = _FakeDB()
        message = _FakeMessage("private")
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=admin_id, type="private"),
            effective_user=SimpleNamespace(id=admin_id, full_name="Test Admin"),
            effective_message=message,
            message=message,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={}, persistence=None),
            user_data={},
        )

        with patch.object(bot, "get_mongo_db", return_value=db):
            asyncio.run(bot.monthly_all_clear_start(update, context))

            prompt, kwargs = message.sent[-1]
            self.assertIn("Group အားလုံး", prompt)
            self.assertIn("20", prompt)
            keyboard = kwargs["reply_markup"]
            buttons = [button for row in keyboard.inline_keyboard for button in row]
            confirm = next(
                button for button in buttons
                if button.callback_data.startswith("monthly_all_clear_confirm_")
            )
            cancel = next(
                button for button in buttons
                if button.callback_data.startswith("monthly_all_clear_cancel_")
            )
            self.assertEqual(
                context.user_data["monthly_all_clear_pending"]["user_id"],
                admin_id,
            )

            query = _FakeQuery(confirm.callback_data, admin_id)
            callback_update = SimpleNamespace(callback_query=query)
            asyncio.run(bot.monthly_all_clear_callback(callback_update, context))

        self.assertEqual(db["monthly_total_counts"].rows, [])
        self.assertEqual(db["monthly_total_entries"].rows, [])
        self.assertEqual(len(db["monthly_total_meta"].rows), 2)
        self.assertFalse(context.user_data.get("monthly_all_clear_pending"))
        self.assertTrue(query.edits)
        self.assertIn("Monthly All Clear Data ပြီးပါပြီ", query.edits[0][0])

    def test_cancel_keeps_monthly_data_unchanged(self):
        admin_id = bot.ADMIN_IDS[0]
        db = _FakeDB()
        message = _FakeMessage("private")
        update = SimpleNamespace(
            effective_chat=SimpleNamespace(id=admin_id, type="private"),
            effective_user=SimpleNamespace(id=admin_id),
            effective_message=message,
            message=message,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={}, persistence=None),
            user_data={},
        )

        with patch.object(bot, "get_mongo_db", return_value=db):
            asyncio.run(bot.monthly_all_clear_start(update, context))
            buttons = [
                button
                for row in message.sent[-1][1]["reply_markup"].inline_keyboard
                for button in row
            ]
            cancel = next(
                button for button in buttons
                if button.callback_data.startswith("monthly_all_clear_cancel_")
            )
            query = _FakeQuery(cancel.callback_data, admin_id)
            asyncio.run(bot.monthly_all_clear_callback(
                SimpleNamespace(callback_query=query), context
            ))

        self.assertEqual(len(db["monthly_total_counts"].rows), 2)
        self.assertTrue(query.edits)
        self.assertIn("မပြောင်းလဲပါ", query.edits[0][0])



class _FrozenDateTime:
    current = None

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


class DailyAutoClearScheduleTests(unittest.TestCase):
    def _data_key_at(self, hour, minute, second=0):
        tz = bot.get_yangon_tz()
        _FrozenDateTime.current = tz.localize(
            RealDateTime(2026, 11, 2, hour, minute, second)
        )
        with patch.object(bot, "datetime", _FrozenDateTime):
            return bot.get_data_key()

    def test_daily_data_window_changes_at_0700_yangon(self):
        self.assertEqual(self._data_key_at(6, 59, 59), "2026-11-01")
        self.assertEqual(self._data_key_at(7, 0, 0), "2026-11-02")
        self.assertEqual(self._data_key_at(23, 59, 59), "2026-11-02")

    def test_daily_auto_clear_and_default_notice_use_0700(self):
        self.assertEqual(bot.DAILY_AUTO_CLEAR_HOUR, 7)
        notice = bot.DEFAULT_MSGS["auto_clear"]
        self.assertIn("မနက် 07:00 (Yangon)", notice)
        self.assertNotIn("12:00", notice)

    def test_scheduler_uses_shared_daily_hour_and_monthly_clear_stays_at_0700(self):
        import inspect

        source = inspect.getsource(bot.post_init)
        self.assertIn("time=time(hour=DAILY_AUTO_CLEAR_HOUR", source)
        self.assertIn("name='auto_clear_daily'", source)
        self.assertIn("time=time(hour=7, minute=0, second=0, tzinfo=tz)", source)
        self.assertIn("name='monthly_total_auto_clear_daily'", source)


if __name__ == "__main__":
    unittest.main()
