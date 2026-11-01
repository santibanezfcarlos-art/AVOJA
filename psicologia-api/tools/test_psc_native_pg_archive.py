"""Tests native PostgreSQL archive metadata with fabricated table names."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import unittest
from psc_native_pg_encrypted import EXPECTED_TABLES, missing_archive_tables, safe_connection, openssl_pass_command


class NativeArchiveTests(unittest.TestCase):
    def test_table_schema_and_data_entries_are_required(self):
        toc = "".join(
            f"123; 1259 9876 TABLE public {table} owner\n"
            f"456; 0 9876 TABLE DATA public {table} owner\n"
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

    @unittest.skipUnless(shutil.which("openssl"), "OpenSSL not installed")
    def test_private_encryption_roundtrip_on_synthetic_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "synthetic.dump"
            encrypted = Path(folder) / "synthetic.dump.enc"
            payload = b"fake-only-test-file" * 100
            source.write_bytes(payload)
            password = "long-test-passphrase-for-ci"
            openssl_pass_command(
                ["-aes-256-cbc", "-salt", "-pbkdf2", "-iter", "600000",
                 "-in", str(source), "-out", str(encrypted)],
                password,
            )
            recovered_hash = openssl_pass_command(
                ["-d", "-aes-256-cbc", "-pbkdf2", "-iter", "600000",
                 "-in", str(encrypted)],
                password, capture=True,
            )
            self.assertEqual(recovered_hash, hashlib.sha256(payload).hexdigest())
            self.assertNotEqual(encrypted.read_bytes(), payload)


if __name__ == "__main__":
    unittest.main()
