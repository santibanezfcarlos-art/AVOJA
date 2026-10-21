"""The Valhalla access design is deterministic and has a fallback logo."""
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo
from ttlock_email_html import access_email_html

class HTMLTests(unittest.TestCase):
    def test_code_date_security_and_logo(self):
        sent=access_email_html("6422798",datetime(2026,10,21,22,tzinfo=ZoneInfo("America/Santiago")))
        self.assertIn("#6422798#",sent)
        self.assertIn("22 de octubre de 2026",sent)
        self.assertIn("personal e intransferible",sent)
        self.assertIn("Valhalla Office",sent)
        self.assertNotIn("AVOJA",sent)
        self.assertNotIn("Código ilustrativo",sent)
        self.assertIn("https://valhalla-office-demo.onrender.com/images/valhalla-logo.png",sent)

    def test_unsafe_code_rejected(self):
        with self.assertRaises(ValueError):
            access_email_html("<b>hello</b>",datetime(2026,10,21,22,tzinfo=ZoneInfo("America/Santiago")))

if __name__=="__main__": unittest.main()
