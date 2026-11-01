import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import monthly_total as mt


class _Result:
    def __init__(self, deleted_count=0):
        self.deleted_count = deleted_count


class _Collection:
    def __init__(self):
        self.rows = {}

    def create_index(self, *args, **kwargs):
        return "monthly-index"

    def update_one(self, query, update, upsert=False):
        key = query["_id"]
        if key not in self.rows and upsert:
            self.rows[key] = {"_id": key}
        if key not in self.rows:
            return
        self.rows[key].update(update.get("$setOnInsert", {}))
        self.rows[key].update(update.get("$set", {}))

    def find_one(self, query):
        row = self.rows.get(query["_id"])
        if row and all(row.get(k) == v for k, v in query.items() if k != "_id"):
            return dict(row)
        return None

    def aggregate(self, pipeline, maxTimeMS=None):
        match = pipeline[0]["$match"]["chat_id"]
        grouped = {}
        for row in self.rows.values():
            if row["chat_id"] != match:
                continue
            key = row["khaifa_key"]
            if key not in grouped:
                grouped[key] = {"_id": key, "label": row["khaifa"], "count": 0}
            grouped[key]["count"] += 1
        return [grouped[key] for key in sorted(grouped)]

    def find(self, query, projection=None):
        return [
            {"_id": row["_id"]}
            for row in self.rows.values()
            if row["chat_id"] == query["chat_id"]
        ]

    def delete_one(self, query):
        row = self.rows.get(query["_id"])
        if row and row["chat_id"] == query["chat_id"]:
            del self.rows[query["_id"]]
            return _Result(1)
        return _Result(0)


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

    def test_save_is_idempotent_and_group_scoped(self):
        first = mt.save_record(self.db, "-1001", 10, "文泰")
        again = mt.save_record(self.db, "-1001", 10, "文泰")
        other = mt.save_record(self.db, "-1002", 10, "文泰")
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)
        totals = mt.get_totals(self.db, "-1001")
        self.assertEqual(totals, [{"khaifa": "文泰", "count": 1}])

    def test_multiple_khaifa_values_are_counted_silently(self):
        for msg_id, value in enumerate(
            ["文泰", "文泰", "文泰", "文泰", "清风️", "清风️", "清风️"], start=100
        ):
            mt.save_record(self.db, "-1001", msg_id, value)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [
                {"khaifa": "文泰", "count": 4},
                {"khaifa": "清风️", "count": 3},
            ],
        )

    def test_emoji_presentation_selector_does_not_split_same_label(self):
        mt.save_record(self.db, "-1001", 20, "清风️")
        mt.save_record(self.db, "-1001", 21, "清风")
        totals = mt.get_totals(self.db, "-1001")
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0]["count"], 2)

    def test_minus_removes_only_the_linked_monthly_record(self):
        first = mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "文泰")
        self.assertTrue(mt.remove_record(self.db, "-1001", first))
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [{"khaifa": "文泰", "count": 1}],
        )

    def test_startup_backfill_runs_once_and_survives_month_clear(self):
        data_msg_map = {
            (-1001, 500): {
                "chat_id": "-1001",
                "entry": "1/11    文泰    client-one",
            },
            (-1001, 502): {
                "chat_id": "-1001",
                "entry": "31/10    清风️    client-two",
            },
            (-1001, 504): {
                "chat_id": "-1001",
                "entry": "1/11    -    excluded",
            },
        }
        self.assertEqual(mt.backfill_active_records(self.db, data_msg_map), 2)
        self.assertTrue(data_msg_map[(-1001, 500)]["monthly_record_id"])
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [
                {"khaifa": "文泰", "count": 1},
                {"khaifa": "清风️", "count": 1},
            ],
        )
        self.assertEqual(mt.backfill_active_records(self.db, data_msg_map), 0)

        # A manually cleared month must not be re-imported on a later restart.
        self.assertEqual(mt.clear_records(self.db, "-1001"), 2)
        self.assertEqual(mt.backfill_active_records(self.db, data_msg_map), 0)
        self.assertEqual(mt.get_totals(self.db, "-1001"), [])

    def test_clear_only_removes_the_selected_group(self):
        mt.save_record(self.db, "-1001", 10, "文泰")
        mt.save_record(self.db, "-1001", 11, "清风️")
        mt.save_record(self.db, "-1002", 10, "文泰")
        self.assertEqual(mt.clear_records(self.db, "-1001"), 2)
        self.assertEqual(
            mt.get_totals(self.db, "-1001"),
            [],
        )
        self.assertEqual(
            mt.get_totals(self.db, "-1002"),
            [{"khaifa": "文泰", "count": 1}],
        )


if __name__ == "__main__":
    unittest.main()
