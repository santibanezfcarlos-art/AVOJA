"""Pruebas sin datos reales ni conexiones a Fonasa/Google Calendar."""
import sqlite3
import unittest
from fonasa_bonos import normalizar_folio, registrar_bono, buscar_por_reserva, BonoDuplicado

HASH = "a" * 64

class FonasaBonoTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")

    def tearDown(self):
        self.db.close()

    def test_normaliza_sin_perder_ceros(self):
        self.assertEqual(normalizar_folio("00.012-345"), "00012345")

    def test_rechaza_inyecciones_y_cadenas_vacias(self):
        for value in ("", "folio", "1234", "12345;DROP TABLE x", None):
            with self.assertRaises(ValueError):
                normalizar_folio(value)

    def test_un_bono_por_reserva(self):
        registrar_bono(self.db, folio="00012345", reservation_id="res1",
                       file_key="fonasa/uno.pdf", file_sha256=HASH)
        self.assertEqual(buscar_por_reserva(self.db, "res1")["folio"], "00012345")
        with self.assertRaises(BonoDuplicado):
            registrar_bono(self.db, folio="00012345", reservation_id="res2",
                           file_key="fonasa/dos.pdf", file_sha256=HASH)
        with self.assertRaises(BonoDuplicado):
            registrar_bono(self.db, folio="00067890", reservation_id="res1",
                           file_key="fonasa/tres.pdf", file_sha256=HASH)
        self.assertIsNone(buscar_por_reserva(self.db, "res2"))

if __name__ == "__main__":
    unittest.main()
