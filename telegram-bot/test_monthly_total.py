import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import monthly_total as mt


class _Result:
    def __init__(self, matched_count=0, modified_count=0, deleted_count=0):
        self.matched_count = matched_count
        self.modified_count = modified_count
        self.deleted_count = deleted_count


class _Collection:
    def __init__(self):
        self.rows = {}
        self.indexes = []

    def create_index(self, *args, **kwargs):
        self.indexes.append(args[0] if args else None)
        return f"index-{len(self.indexes)}"

    @staticmethod
    def _matches(row, query):
        for key, expected in query.items():
            actual = row.get(key)
            if isinstance(expected, dict):
                if "$ne" in expected:
                    excluded = expected["$ne"]
                    if actual == excluded or (
                        isinstance(actual, list) and excluded in actual
                    ):
                        return False
                if "$gt" in expected and not (actual is not None and actual > expected["$gt"]):
                    return False
            elif isinstance(actual, list):
                if expected not in actual:
                    return False
            elif actual != expected:
                return False
        return True

    def find_one(self, query, projection=None):
        for row in self.rows.values():
            if self._matches(row, query):
                return dict(row)
        return None

    def find(self, query=None, projection=None):
        query = query or {}
        return [
            dict(row) for row in self.rows.values()
            if self._matches(row, query)
        ]

    def update_one(self, query, update, upsert=False):
        row = None
        key = query.get("_id")
        if key is not None:
            candidate = self.rows.get(key)
            if candidate is not None and self._matches(candidate, query):
                row = candidate
        else:
            for candidate in self.rows.values():
                if self._matches(candidate, query):
                    row = candidate
                    break

        inserted = False
        if row is None and upsert and key is not None:
            row = {"_id": key}
            self.rows[key] = row
            inserted = True
        if row is None:
            return _Result()

        if inserted:
            row.update(update.get("$setOnInsert", {}))
        row.update(update.get("$set", {}))
        for field, amount in update.get("$inc", {}).items():
            row[field] = row.get(field, 0) + amount
        for field, value in update.get("$addToSet", {}).items():
            values = row.setdefault(field, [])
            if value not in values:
                values.append(value)
        for field, value in update.get("$pull", {}).items():
            row[field] = [item for item in row.get(field, []) if item != value]
        return _Result(matched_count=1, modified_count=1)

    def update_many(self, query, update):
        matched = modified = 0
        for row in self.rows.values():
            if not self._matches(row, query):
                continue
            matched += 1
            before = dict(row)
            row.update(update.get("$set", {}))
            modified += int(row != before)
        return _Result(matched_count=matched, modified_count=modified)

    def delete_many(self, query):
        keys = [
            key for key, row in self.rows.items()
            if self._matches(row, query)
        ]
        for key in keys:
            del self.rows[key]
        return _Result(deleted_count=len(keys))

    def delete_one(self, query):
        for key, row in list(self.rows.items()):
            if self._matches(row, query):
                del self.rows[key]
                return _Result(deleted_count=1)
        return _Result()


class _DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())


class MonthlyTotalTests(unittest.TestCase):
    def setUp(self):
        self.db = _DB()
        mt._index_ready = False

    def test_dash_and_empty_values_are_excluded(self):
        for value in ("-", " — ", "–", "", "   ", "N/A", None):
            self.assertEqual(mt.normalize_khaifa(value), (None, None))

    def test_unicode_label_is_preserved_and_normalized(self):
        label, key = mt.normalize_khaifa("  文泰  ")
        self.assertEqual(label, "文泰")
        self.assertEqual(key, "文泰")

    def test_storage_is_one_document_per_group_and_khaifa(self):
        for message_id in range(100, 150):
            self.assertIsNotNone(
                mt.save_record(self.db, "-1001", message_id, "文泰")
            )
        mt.save_record(self.db, "-1001", 200, "清风️")
        mt.save_record(self.db, "-1002", 100, "文泰")

        rows = self.db[mt.COLLECTION].rows
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [
                {"khaifa": "文泰", "count": 50},
                {"khaifa": "清风️", "count": 1},
            ],
        )

    def test_save_is_idempotent_and_group_scoped(self):
        first = mt.save_record(self.db, "-1001", 10, "文泰")
        again = mt.save_record(self.db, "-1001", 10, "文泰")
        other_group = mt.save_record(self.db, "-1002", 10, "文泰")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other_group)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "文泰", "count": 1}],
        )

    def test_same_message_id_cannot_increment_a_different_khaifa(self):
        first = mt.save_record(self.db, "-1001", 10, "文泰")
        again = mt.save_record(self.db, "-1001", 10, "清风")
        self.assertEqual(first, again)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "文泰", "count": 1}],
        )

    def test_emoji_presentation_selector_does_not_split_same_label(self):
        mt.save_record(self.db, "-1001", 20, "清风️")
        mt.save_record(self.db, "-1001", 21, "清风")
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "清风", "count": 2}],
        )

    def test_minus_reverses_only_the_linked_active_record_once(self):
        first = mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "文泰")
        self.assertTrue(mt.remove_record(self.db, "-1001", first))
        self.assertTrue(mt.remove_record(self.db, "-1001", first))
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "文泰", "count": 1}],
        )

    def test_daily_clear_forgets_reversal_ids_but_preserves_month_total(self):
        old_id = mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "文泰")
        self.assertEqual(mt.clear_active_records(self.db, "-1001"), 1)

        # A delayed '-' from the previous daily window must not decrement.
        mt.remove_record(self.db, "-1001", old_id)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "文泰", "count": 2}],
        )

        new_id = mt.save_record(self.db, "-1001", 12, "文泰")
        mt.remove_record(self.db, "-1001", new_id)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "文泰", "count": 2}],
        )

    def test_monthly_clear_does_not_allow_old_minus_to_touch_new_month(self):
        old_id = mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "文泰")
        self.assertEqual(mt.clear_records(self.db, "-1001"), 2)

        new_id = mt.save_record(self.db, "-1001", 12, "文泰")
        mt.remove_record(self.db, "-1001", old_id)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "文泰", "count": 1}],
        )
        mt.remove_record(self.db, "-1001", new_id)
        self.assertEqual(mt.get_totals(self.db, "-1001"), [])

    def test_clear_only_removes_the_selected_group_and_returns_submission_count(self):
        mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "文泰")
        mt.save_record(self.db, "-1001", 12, "清风")
        mt.save_record(self.db, "-1002", 10, "文泰")

        self.assertEqual(mt.clear_records(self.db, "-1001"), 3)
        self.assertEqual(mt.get_totals(self.db, "-1001"), [])
        self.assertEqual(
            mt.get_totals(self.db, "-1002"),
            [{"khaifa": "文泰", "count": 1}],
        )

    def test_legacy_migration_preserves_month_totals_and_active_minus(self):
        legacy = self.db[mt.LEGACY_COLLECTION]
        legacy.rows = {
            "-1001:500": {
                "_id": "-1001:500", "chat_id": "-1001",
                "message_id": 500, "khaifa": "文泰", "khaifa_key": "文泰",
            },
            "-1001:501": {
                "_id": "-1001:501", "chat_id": "-1001",
                "message_id": 501, "khaifa": "文泰", "khaifa_key": "文泰",
            },
            "-1001:502": {
                "_id": "-1001:502", "chat_id": "-1001",
                "message_id": 502, "khaifa": "清风️", "khaifa_key": "清风",
            },
        }
        mapping = {
            (-1001, 900): {
                "chat_id": "-1001", "entry": "1/11    文泰    client",
                "monthly_record_id": "-1001:500",
            },
            (-1001, 901): {
                "chat_id": "-1001", "entry": "1/11    文泰    client",
                "monthly_record_id": "-1001:501",
            },
        }

        self.assertEqual(mt.backfill_active_records(self.db, mapping), 3)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [
                {"khaifa": "文泰", "count": 2},
                {"khaifa": "清风️", "count": 1},
            ],
        )
        self.assertEqual(legacy.rows, {})
        self.assertEqual(
            mapping[(-1001, 900)]["monthly_key"],
            "文泰",
        )
        self.assertTrue(
            mt.remove_record(self.db, "-1001", mapping[(-1001, 900)]["monthly_record_id"])
        )
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [
                {"khaifa": "文泰", "count": 1},
                {"khaifa": "清风️", "count": 1},
            ],
        )
        self.assertEqual(mt.backfill_active_records(self.db, mapping), 0)

    def test_initial_backfill_runs_once_for_older_active_maps(self):
        mapping = {
            (-1001, 500): {
                "chat_id": "-1001",
                "entry": "1/11    文泰    client-one",
            },
            (-1001, 502): {
                "chat_id": "-1001",
                "entry": "1/11    清风️    client-two",
            },
            (-1001, 504): {
                "chat_id": "-1001",
                "entry": "1/11    -    excluded",
            },
        }
        self.assertEqual(mt.backfill_active_records(self.db, mapping), 2)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [
                {"khaifa": "文泰", "count": 1},
                {"khaifa": "清风️", "count": 1},
            ],
        )
        self.assertEqual(
            mapping[(-1001, 500)]["monthly_record_id"],
            "-1001:500",
        )
        self.assertEqual(mt.backfill_active_records(self.db, mapping), 0)

    def test_previously_cleared_month_is_not_repopulated_from_old_map(self):
        self.db[mt.META_COLLECTION].rows["-1001"] = {
            "_id": "-1001",
            "initial_backfill_done": True,
        }
        mapping = {
            (-1001, 500): {
                "chat_id": "-1001",
                "entry": "1/11    文泰    old-client",
                "monthly_record_id": "-1001:500",
            }
        }
        self.assertEqual(mt.backfill_active_records(self.db, mapping), 0)
        self.assertEqual(mt.get_totals(self.db, "-1001"), [])
        self.assertTrue(mt.remove_record(self.db, "-1001", "-1001:500"))
        self.assertEqual(mt.get_totals(self.db, "-1001"), [])


    def test_total_command_uses_custom_report_row_and_inline_button(self):
        import asyncio

        from telegram import InlineKeyboardButton

        class Chat:
            id = -1001
            type = "supergroup"

        class Message:
            def __init__(self):
                self.sent = None

            async def reply_text(self, text, reply_markup=None):
                self.sent = {"text": text, "reply_markup": reply_markup}

        class Update:
            effective_chat = Chat()

            def __init__(self):
                self.effective_message = Message()

        class Context:
            pass

        async def scenario():
            mt.save_record(self.db, "-1001", 501, "文泰")
            update = Update()
            context = Context()
            captured = {}

            async def send_custom(target, key, reply_markup=None, **fmt):
                captured.update({
                    "target": target,
                    "key": key,
                    "reply_markup": reply_markup,
                    "fmt": fmt,
                })

            def get_text(key, **fmt):
                templates = {
                    "monthly_total_row": "{khaifa} :: {count}",
                    "monthly_total_warning": "custom warning",
                    "monthly_total_empty": "custom empty",
                }
                template = templates.get(key, key)
                return template.format(**fmt) if fmt else template

            await mt.total_command(
                update,
                context,
                get_db=lambda: self.db,
                get_text=get_text,
                send_custom=send_custom,
            )
            self.assertEqual(captured["key"], "monthly_total_message")
            self.assertEqual(captured["fmt"]["totals"], "文泰 :: 1")
            self.assertEqual(captured["fmt"]["empty"], "")
            self.assertEqual(captured["fmt"]["warning"], "custom warning")
            button = captured["reply_markup"].inline_keyboard[0][0]
            self.assertIsInstance(button, InlineKeyboardButton)
            self.assertEqual(button.text, "Monthly Clear Data")
            self.assertEqual(button.callback_data, "monthly_clear_start")

        asyncio.run(scenario())

    def test_monthly_clear_confirmation_uses_custom_prompt_and_button_keys(self):
        import asyncio

        from telegram import InlineKeyboardButton

        class Chat:
            id = -1001
            type = "supergroup"
            title = "Sample Group"

        class User:
            id = 12345

        class Message:
            chat = Chat()

        class Query:
            data = "monthly_clear_start"
            message = Message()
            from_user = User()

            def __init__(self):
                self.answered = False

            async def answer(self, *args, **kwargs):
                self.answered = True

        class Update:
            callback_query = Query()

        class Persistence:
            pass

        class Application:
            persistence = None

        class Context:
            chat_data = {}
            application = Application()

        async def scenario():
            mt.save_record(self.db, "-1001", 601, "文泰")
            mt.save_record(self.db, "-1001", 602, "文泰")
            update = Update()
            context = Context()
            captured = {}
            button_keys = []

            async def is_group_admin(_context, _user_id, _chat_id):
                return True

            async def edit_custom(query, key, reply_markup=None, **fmt):
                captured.update({
                    "key": key,
                    "reply_markup": reply_markup,
                    "fmt": fmt,
                })

            def get_text(key, **fmt):
                return key

            def button_factory(key, callback_data, style):
                button_keys.append((key, callback_data, style))
                return InlineKeyboardButton(
                    key,
                    callback_data=callback_data,
                    style=style,
                )

            await mt.clear_callback(
                update,
                context,
                get_db=lambda: self.db,
                is_group_admin=is_group_admin,
                get_text=get_text,
                edit_custom=edit_custom,
                button_factory=button_factory,
            )
            self.assertTrue(update.callback_query.answered)
            self.assertEqual(captured["key"], "monthly_total_confirm_prompt")
            self.assertEqual(captured["fmt"]["group_name"], "Sample Group")
            self.assertEqual(captured["fmt"]["count"], 2)
            self.assertEqual(
                [item[0] for item in button_keys],
                [
                    "monthly_total_confirm_button",
                    "monthly_total_cancel_button",
                ],
            )
            buttons = captured["reply_markup"].inline_keyboard[0]
            self.assertTrue(all(isinstance(button, InlineKeyboardButton) for button in buttons))
            self.assertTrue(buttons[0].callback_data.startswith("monthly_clear_confirm_"))
            self.assertTrue(buttons[1].callback_data.startswith("monthly_clear_cancel_"))

        asyncio.run(scenario())

    def test_monthly_auto_clear_runs_once_per_month(self):
        mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "文泰")
        mt.save_record(self.db, "-1002", 12, "清风️")

        first = mt.clear_all_records_for_month(self.db, "2026-11")
        self.assertEqual(first["groups"], 2)
        self.assertEqual(first["submissions"], 3)
        self.assertFalse(first["already_cleared"])
        self.assertEqual(mt.get_totals(self.db, "-1001"), [])
        self.assertEqual(mt.get_totals(self.db, "-1002"), [])

        # New-month submissions made after the scheduled clear must remain.
        mt.save_record(self.db, "-1001", 13, "新月")
        second = mt.clear_all_records_for_month(self.db, "2026-11")
        self.assertTrue(second["already_cleared"])
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "新月", "count": 1}],
        )

    def test_monthly_auto_clear_rejects_invalid_month_key(self):
        self.assertIsNone(mt.clear_all_records_for_month(self.db, "2026-13"))
        self.assertIsNone(mt.clear_all_records_for_month(self.db, "not-a-month"))

    def test_global_clear_counts_and_removes_all_group_monthly_data_only(self):
        mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "文泰")
        mt.save_record(self.db, "-1002", 12, "清风️")
        legacy = self.db[mt.LEGACY_COLLECTION]
        legacy.rows["legacy-1"] = {
            "_id": "legacy-1", "chat_id": "-1003",
            "khaifa": "老记录", "khaifa_key": "老记录",
        }
        meta = self.db[mt.META_COLLECTION]
        meta.rows["-1001"] = {
            "_id": "-1001",
            "initial_backfill_done": True,
            "storage_version": mt.STORAGE_VERSION,
        }

        preview = mt.get_all_summary(self.db)
        self.assertEqual(preview["groups"], 3)
        self.assertEqual(preview["submissions"], 4)
        self.assertEqual(preview["counter_documents"], 2)
        self.assertEqual(preview["legacy_documents"], 1)

        result = mt.clear_all_records(self.db)
        self.assertEqual(result, preview)
        self.assertEqual(mt.get_totals(self.db, "-1001"), [])
        self.assertEqual(mt.get_totals(self.db, "-1002"), [])
        self.assertEqual(self.db[mt.LEGACY_COLLECTION].rows, {})
        # Keep migration markers to prevent old daily mappings from recreating
        # totals on the next restart.
        self.assertIn("-1001", meta.rows)


if __name__ == "__main__":
    unittest.main()
