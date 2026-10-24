"""Security regressions for patient rebooking links (no external services)."""
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import rebooking_tokens as links

TZ = ZoneInfo("America/Santiago")


class FakeCursor:
    def __init__(self, row):
        self.row = row
    def fetchone(self):
        return self.row


class FakeDb:
    def __init__(self, start, state="CONFIRMED", changes=0, redeemed=None):
        self.row = dict(order_id="PSI-TEST", state=state, starts_at=start,
                        revoked_at=None, redeemed_at=redeemed, changes=changes)
        self.record = None
    def execute(self, sql, params=None):
        if sql.startswith("INSERT"):
            if self.record is None:
                self.record = {"token_sha256": params[1]}
            return FakeCursor(None)
        if "SELECT token_sha256" in sql:
            return FakeCursor(self.record)
        if "WHERE l.token_sha256" in sql:
            return FakeCursor(self.row if self.record and
                              params[0] == self.record["token_sha256"] else None)
        raise AssertionError(sql)


class RebookingTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 25, 10, tzinfo=TZ)
        self.db = FakeDb(self.now + timedelta(hours=48))

    def test_retry_link_same_token(self):
        with patch.dict(os.environ, {"PSICOLOGIA_REBOOKING_TOKEN_SECRET": "X" * 64}):
            first = links.issue(self.db, "PSI-TEST")
            second = links.issue(self.db, "PSI-TEST")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, self.db.record["token_sha256"])

    def test_requires_secret(self):
        with patch.dict(os.environ, {"PSICOLOGIA_REBOOKING_TOKEN_SECRET": "short"}):
            with self.assertRaises(RuntimeError):
                links.issue(self.db, "PSI-TEST")

    def test_valid_and_expired(self):
        with patch.dict(os.environ, {"PSICOLOGIA_REBOOKING_TOKEN_SECRET": "X" * 64}):
            token = links.issue(self.db, "PSI-TEST")
        self.assertEqual(links.check(self.db, token, now=self.now)[1], "eligible")
        self.assertEqual(links.check(self.db, token, now=self.now + timedelta(hours=25))[1], "too_late")

    def test_one_use(self):
        with patch.dict(os.environ, {"PSICOLOGIA_REBOOKING_TOKEN_SECRET": "X" * 64}):
            token = links.issue(self.db, "PSI-TEST")
        self.db.row["changes"] = 1
        self.assertEqual(links.check(self.db, token, now=self.now)[1], "used")
        self.db.row["changes"] = 0
        self.db.row["redeemed_at"] = self.now
        self.assertEqual(links.check(self.db, token, now=self.now)[1], "used")

    def test_rejects_incorrect_token(self):
        with patch.dict(os.environ, {"PSICOLOGIA_REBOOKING_TOKEN_SECRET": "X" * 64}):
            links.issue(self.db, "PSI-TEST")
        self.assertEqual(links.check(self.db, "not-a-link", now=self.now)[1], "invalid")


if __name__ == "__main__":
    unittest.main()
