"""Tests native PostgreSQL archive metadata with fabricated table names."""
import unittest
from psc_native_pg_encrypted import EXPECTED_TABLES, missing_archive_tables, safe_connection


class NativeArchiveTests(unittest.TestCase):
    def test_table_schema_and_data_entries_are_required(self):
        toc = "".join(
            f"123; 1259 9876 TABLE public {table} owner\\n"
            f"456; 0 9876 TABLE DATA public {table} owner\\n"
            for table in sorted(EXPECTED_TABLES)
        )
        self.assertEqual(missing_archive_tables(toc), [])
        self.assertEqual(missing_archive_tables(toc.replace("TABLE DATA public psychology_google_oauth", "COMMENT public psychology_google_oauth")), ["psychology_google_oauth"])

    def test_comment_cannot_supply_table_entry(self):
        self.assertEqual(missing_archive_tables("; TABLE public psychology_google_oauth example"), sorted(EXPECTED_TABLES))

    def test_url_secret_not_in_program_args(self):
        args, env = safe_connection("postgresql://user:demo%40pass@db.internal:5432/psc_test?sslmode=require")
        self.assertNotIn("demo@pass", " ".join(args))
        self.assertEqual(env.get("PGPASSWORD"), "demo@pass")
        self.assertEqual(env.get("PGSSLMODE"), "require")


if __name__ == "__main__":
    unittest.main()
