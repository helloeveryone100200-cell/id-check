import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# The bot entry point lives in this directory rather than the repository root.
BOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BOT_DIR))
import main as bot  # noqa: E402


class DepositReportParserTests(unittest.TestCase):
    def test_extracts_phone_and_id_without_showdata_fields(self):
        phone, identity = bot._extract_phone_id_values(
            "Phone number - 0980477682\nID - 00256694164"
        )
        self.assertEqual(phone, "0980477682")
        self.assertEqual(identity, "00256694164")

    def test_parser_is_case_insensitive_and_accepts_colon(self):
        phone, identity = bot._extract_phone_id_values(
            "phone number: 0912345678\nid no.: 000123"
        )
        self.assertEqual((phone, identity), ("0912345678", "000123"))

    def test_missing_or_placeholder_fields_are_not_values(self):
        self.assertEqual(
            bot._extract_phone_id_values("Phone number - 0912345678\nID -"),
            ("0912345678", None),
        )
        self.assertEqual(
            bot._extract_phone_id_values("Phone number - N/A\nID - 123"),
            (None, "123"),
        )

    def test_unrelated_text_is_ignored(self):
        self.assertEqual(bot._extract_phone_id_values("hello world"), (None, None))


class DepositReportStorageTests(unittest.TestCase):
    def setUp(self):
        self.mongo_load = patch.object(bot, "mg_load_phone_id_report_entries", return_value=[])
        self.mongo_save = patch.object(bot, "mg_save_phone_id_report_entries")
        self.mongo_load.start()
        self.mongo_save.start()
        self.addCleanup(self.mongo_load.stop)
        self.addCleanup(self.mongo_save.stop)

    def test_upsert_preserves_leading_zeroes_and_group_isolation(self):
        data = {}
        bot._upsert_phone_id_report_entry(
            data, "-1001", "2026-11-07", 41, "0980477682", "00256694164"
        )
        bot._upsert_phone_id_report_entry(
            data, "-1002", "2026-11-07", 41, "0911111111", "0007"
        )
        self.assertEqual(
            data["phone_id_reports"]["-1001"]["2026-11-07"][0]["phone_number"],
            "0980477682",
        )
        self.assertEqual(
            data["phone_id_reports"]["-1001"]["2026-11-07"][0]["id_number"],
            "00256694164",
        )
        self.assertEqual(
            data["phone_id_reports"]["-1002"]["2026-11-07"][0]["id_number"],
            "0007",
        )

    def test_incomplete_pair_is_not_stored(self):
        data = {}
        bot._upsert_phone_id_report_entry(
            data, "-1001", "2026-11-07", 41, "0980477682", None
        )
        self.assertNotIn("phone_id_reports", data)

    def test_upsert_replaces_same_source_message(self):
        data = {
            "phone_id_reports": {
                "-1001": {
                    "2026-11-07": [
                        {"source_message_id": 41, "phone_number": "old", "id_number": "old"},
                        {"source_message_id": 42, "phone_number": "keep", "id_number": "keep"},
                    ]
                }
            }
        }
        bot._upsert_phone_id_report_entry(
            data, "-1001", "2026-11-07", 41, "new", "0009"
        )
        entries = data["phone_id_reports"]["-1001"]["2026-11-07"]
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [e for e in entries if e["source_message_id"] == 41][0]["phone_number"],
            "new",
        )

    def test_edit_cleanup_removes_source_from_previous_day(self):
        data = {
            "phone_id_reports": {
                "-1001": {
                    "2026-11-06": [
                        {"source_message_id": 41, "phone_number": "old", "id_number": "old"}
                    ]
                }
            }
        }
        bot._remove_phone_id_report_source_from_other_dates(
            data, "-1001", "2026-11-07", 41
        )
        self.assertNotIn("-1001", data.get("phone_id_reports", {}))


    def test_mongo_entries_are_primary_when_available(self):
        mongo_entry = {
            "source_message_id": 1,
            "phone_number": "0999000000",
            "id_number": "0001",
        }
        with patch.object(
            bot, "mg_load_phone_id_report_entries", return_value=[mongo_entry]
        ):
            data = {
                "phone_id_reports": {
                    "-1001": {
                        "2026-11-07": [
                            {"source_message_id": 2, "phone_number": "old", "id_number": "old"}
                        ]
                    }
                }
            }
            entries = bot._get_phone_id_report_entries(data, "-1001", "2026-11-07")
        self.assertEqual(entries, [mongo_entry])

    def test_pickle_is_used_when_mongo_has_no_records(self):
        data = {
            "phone_id_reports": {
                "-1001": {
                    "2026-11-07": [
                        {"source_message_id": 2, "phone_number": "0900000000", "id_number": "0002"}
                    ]
                }
            }
        }
        with patch.object(bot, "mg_load_phone_id_report_entries", return_value=[]):
            entries = bot._get_phone_id_report_entries(data, "-1001", "2026-11-07")
        self.assertEqual(entries[0]["phone_number"], "0900000000")

    def test_group_clear_tombstone_hides_stale_mongo_after_restart(self):
        data = {"phone_id_reports": {"-1001": {"2026-11-07": []}}}
        with patch.object(bot, "mg_delete_phone_id_report_entries"):
            bot._delete_phone_id_report_entries(data, "-1001", "2026-11-07")
        with patch.object(
            bot, "mg_load_phone_id_report_entries",
            return_value=[{"source_message_id": 1, "phone_number": "old", "id_number": "old"}],
        ):
            self.assertEqual(
                bot._get_phone_id_report_entries(data, "-1001", "2026-11-07"), []
            )

    def test_global_clear_tombstone_hides_mongo_but_allows_new_group_data(self):
        data = {}
        class Collection:
            def delete_many(self, query):
                return SimpleNamespace(deleted_count=0)
        class Database:
            def __getitem__(self, name):
                return Collection()
        bot._delete_phone_id_reports_for_date(data, "2026-11-07", db=Database())
        stale = [{"source_message_id": 1, "phone_number": "old", "id_number": "old"}]
        with patch.object(bot, "mg_load_phone_id_report_entries", return_value=stale):
            self.assertEqual(
                bot._get_phone_id_report_entries(data, "-1001", "2026-11-07"), []
            )
            bot._save_phone_id_report_entries(
                data, "-1002", "2026-11-07",
                [{"source_message_id": 2, "phone_number": "new", "id_number": "new"}],
            )
            self.assertEqual(
                bot._get_phone_id_report_entries(data, "-1002", "2026-11-07")[0]["phone_number"],
                "new",
            )
            self.assertEqual(
                bot._get_phone_id_report_entries(data, "-1001", "2026-11-07"), []
            )

    def test_delete_for_date_preserves_other_reporting_days(self):
        class Collection:
            def delete_many(self, query):
                self.query = query
                return SimpleNamespace(deleted_count=1)

        collection = Collection()

        class Database:
            def __getitem__(self, name):
                self.name = name
                return collection

        data = {
            "phone_id_reports": {
                "-1001": {
                    "2026-11-06": [{"source_message_id": 1}],
                    "2026-11-07": [{"source_message_id": 2}],
                },
                "-1002": {
                    "2026-11-06": [{"source_message_id": 3}],
                },
            }
        }
        removed = bot._delete_phone_id_reports_for_date(
            data, "2026-11-06", db=Database()
        )
        self.assertEqual(removed, 1)
        self.assertNotIn("2026-11-06", data["phone_id_reports"]["-1001"])
        self.assertIn("2026-11-07", data["phone_id_reports"]["-1001"])
        self.assertNotIn("-1002", data["phone_id_reports"])


class DepositReportMessageFlowTests(unittest.IsolatedAsyncioTestCase):
    async def _process(self, text=None, caption=None, message_id=91):
        message = SimpleNamespace(
            text=text,
            caption=caption,
            message_id=message_id,
        )
        chat = SimpleNamespace(id=-100123, type="supergroup")
        update = SimpleNamespace(message=message, effective_chat=chat)
        app = SimpleNamespace(bot_data={}, persistence=None)
        context = SimpleNamespace(application=app)
        with patch.object(bot, "save_chat_id", new=AsyncMock()), \
             patch.object(bot, "mg_load_phone_id_report_entries", return_value=[]), \
             patch.object(bot, "mg_save_phone_id_report_entries"):
            await bot.extract_and_save_data(update, context)
        return app.bot_data

    async def test_text_message_saves_pair_without_khaifa_or_date(self):
        data = await self._process(
            text="Phone number - 0980477682\nID - 00256694164"
        )
        entry = data["phone_id_reports"]["-100123"][bot.get_data_key()][0]
        self.assertEqual(entry["phone_number"], "0980477682")
        self.assertEqual(entry["id_number"], "00256694164")

    async def test_caption_saves_pair_without_khaifa_or_date(self):
        data = await self._process(
            caption="Phone number - 0977000000\nID - 000123"
        )
        entry = data["phone_id_reports"]["-100123"][bot.get_data_key()][0]
        self.assertEqual(entry["phone_number"], "0977000000")
        self.assertEqual(entry["id_number"], "000123")

    async def test_unrelated_message_does_not_create_empty_report(self):
        data = await self._process(text="ordinary chat message")
        self.assertNotIn("phone_id_reports", data)



class DepositReportClearTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirmed_clear_deletes_showdata_and_deposit_report(self):
        query = SimpleNamespace(
            data="cleardata_confirm",
            message=SimpleNamespace(chat_id=-100123),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        persistence = SimpleNamespace(flush=AsyncMock())
        app = SimpleNamespace(
            bot_data={"group_data": {"-100123": {bot.get_data_key(): ["row"]}}},
            persistence=persistence,
        )
        context = SimpleNamespace(application=app)
        with (
            patch.object(bot, "mg_delete_group_data") as delete_showdata,
            patch.object(bot, "_delete_phone_id_report_entries") as delete_deposit,
            patch.object(bot, "_edit_custom", new=AsyncMock()),
            patch.object(bot, "plus_counters", {}),
            patch.object(bot, "plus_counted_msgs", {}),
            patch.object(bot, "data_msg_map", {}),
            patch.object(bot, "save_plus_data"),
            patch.object(bot, "save_data_msg_map"),
            patch.object(bot, "clear_monthly_active_records"),
            patch.object(bot, "get_mongo_db", return_value=None),
        ):
            await bot.cleardata_callback(update, context)
        delete_showdata.assert_called_once_with("-100123", bot.get_data_key())
        delete_deposit.assert_called_once_with(
            app.bot_data, "-100123", bot.get_data_key()
        )
        persistence.flush.assert_awaited_once()

    async def test_cancelled_clear_does_not_delete_either_dataset(self):
        query = SimpleNamespace(data="cleardata_cancel", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={}, persistence=None)
        )
        with (
            patch.object(bot, "mg_delete_group_data") as delete_showdata,
            patch.object(bot, "_delete_phone_id_report_entries") as delete_deposit,
            patch.object(bot, "_edit_custom", new=AsyncMock()),
        ):
            await bot.cleardata_callback(update, context)
        delete_showdata.assert_not_called()
        delete_deposit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
