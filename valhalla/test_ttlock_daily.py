"""Offline tests only; no Render/TTLock/Resend requests."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from ttlock_daily import AttemptLedger, TTLockClient, email_content, validity_window, timestamp_ms, TTLockError

CHILE = ZoneInfo("America/Santiago")


class Tests(unittest.TestCase):
    def test_standard_window(self):
        start, end = validity_window(datetime(2026,10,19,22,0,tzinfo=CHILE))
        self.assertEqual((start.hour, end.hour, (end-start).days), (22,22,1))
        self.assertEqual((end.timestamp()-start.timestamp())/3600,24)

    def test_dst_windows_keep_local_22(self):
        for month, day in [(4,4),(9,5)]:
            start,end = validity_window(datetime(2027,month,day,22,tzinfo=CHILE))
            self.assertEqual((start.hour,end.hour),(22,22))
            self.assertEqual(end.date().toordinal()-start.date().toordinal(),1)

    def test_email_format(self):
        start,_ = validity_window(datetime(2026,10,19,22,tzinfo=CHILE))
        sub,body = email_content("6422798",start)
        self.assertEqual(sub,"Código de acceso 20 de octubre")
        self.assertIn("#6422798#",body)

    def test_claim_is_unique(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = AttemptLedger(str(Path(temp)/"codes.sqlite3"))
            ledger.initialize()
            self.assertTrue(ledger.claim("2026-10-19"))
            self.assertFalse(ledger.claim("2026-10-19"))
            ledger.mark("2026-10-19","uncertain",error_category="network")
            self.assertFalse(ledger.claim("2026-10-19"))

    def test_mocked_api(self):
        class Response:
            def __init__(self, payload): self.payload=payload
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self): return json.dumps(self.payload).encode()
        calls=[]
        def transport(request, timeout=15):
            from urllib.parse import parse_qs
            fields = parse_qs(request.data.decode())
            calls.append((request.full_url,fields))
            if request.full_url.endswith("/oauth2/token"):
                return Response({"access_token":"FAKE"})
            if request.full_url.endswith("/v3/lock/detail"):
                return Response({"errcode":0,"lockId":3199138,"keyboardPwdVersion":4})
            return Response({"errcode":0,"keyboardPwd":"123456","keyboardPwdId":42})
        client=TTLockClient("id","secret","user","a"*32,3199138,transport)
        token=client.authenticate()
        detail=client.details(token)
        start,end=validity_window(datetime(2026,10,19,22,tzinfo=CHILE))
        self.assertEqual(client.generate(token,start,end,detail["keyboardPwdVersion"]),("123456",42))
        self.assertEqual(len(calls),3)


if __name__=="__main__":
    unittest.main()
