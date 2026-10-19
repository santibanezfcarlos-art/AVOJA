"""Offline TTLock worker tests. Does not access external services."""
import tempfile
import unittest
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

from ttlock_worker import run_daily_once, start_worker
from ttlock_daily import AttemptLedger

CHILE = ZoneInfo("America/Santiago")


class FakeClient:
    lock_id = 123
    def __init__(self, fail=False):
        self.fail = fail
        self.created = 0

    def authenticate(self):
        return "mock-token"

    def details(self, token):
        return {"keyboardPwdVersion": 4}

    def generate(self, token, start, end, version):
        self.created += 1
        if self.fail:
            raise RuntimeError("simulated uncertain response")
        return "123456", 9001


class FakeMailer:
    def __init__(self, fail=False):
        self.fail = fail
        self.messages = []

    def send_message(self, to, subject, message, idempotency_key=None):
        self.messages.append((to, subject, message, idempotency_key))
        if self.fail:
            raise RuntimeError("simulated mail failure")


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = str(Path(self.temp.name) / "tests.sqlite3")
        self.now = datetime(2026, 10, 19, 22, 1, tzinfo=CHILE)

    @patch.dict("os.environ", {"TTLOCK_EMAIL_TO": "example@example.org"})
    def test_success_and_no_duplicates(self):
        client, mailer = FakeClient(), FakeMailer()
        self.assertEqual(run_daily_once(self.db, now=self.now, client=client, mailer=mailer), "emailed")
        self.assertEqual(run_daily_once(self.db, now=self.now, client=client, mailer=mailer), "already_claimed")
        self.assertEqual(client.created, 1)
        self.assertEqual(len(mailer.messages), 1)
        self.assertIn("#123456#", mailer.messages[0][2])

    @patch.dict("os.environ", {"TTLOCK_EMAIL_TO": "example@example.org"})
    def test_email_failure_does_not_create_second_code(self):
        client, mailer = FakeClient(), FakeMailer(fail=True)
        self.assertEqual(run_daily_once(self.db, now=self.now, client=client, mailer=mailer), "email_failed")
        self.assertEqual(run_daily_once(self.db, now=self.now, client=client, mailer=mailer), "already_claimed")
        self.assertEqual(client.created, 1)

    @patch.dict("os.environ", {"TTLOCK_EMAIL_TO": "example@example.org"})
    def test_ambiguous_ttlock_response_prevents_retry(self):
        client = FakeClient(fail=True)
        self.assertEqual(run_daily_once(self.db, now=self.now, client=client, mailer=FakeMailer()), "uncertain")
        self.assertEqual(run_daily_once(self.db, now=self.now, client=client, mailer=FakeMailer()), "already_claimed")
        self.assertEqual(client.created, 1)

    @patch.dict("os.environ", {"TTLOCK_ENABLED": "false"})
    def test_scheduler_off_by_default(self):
        self.assertIsNone(start_worker(self.db))


if __name__ == "__main__":
    unittest.main()
