import os
import sys
import unittest
from datetime import datetime as RealDateTime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("BOT_TOKEN", "123456:TEST_TOKEN_FOR_DAILY_AUTO_CLEAR_TEST")

import main as bot


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
