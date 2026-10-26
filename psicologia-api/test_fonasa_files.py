"""Pruebas del filtro local de archivos, sin datos de pacientes."""
import unittest
from fonasa_files import inspeccionar_comprobante, ComprobanteInvalido

class ComprobantesTests(unittest.TestCase):
    def test_pdf_simulado(self):
        result = inspeccionar_comprobante("bono.pdf", b"%PDF-1.7\ncontenido de prueba")
        self.assertEqual(result["mime"], "application/pdf")
        self.assertEqual(len(result["sha256"]), 64)

    def test_no_confia_en_extension(self):
        with self.assertRaises(ComprobanteInvalido):
            inspeccionar_comprobante("bono.pdf", b"texto que no es pdf")

    def test_evitar_archivos_dañinos_o_grandes(self):
        for name, data in [("../bono.pdf", b"%PDF-test"), ("bono.exe", b"MZ"), ("bono.png", b"nada"), ("bono.pdf", b"%PDF-" + b"x" * (5 * 1024 * 1024))]:
            with self.subTest(name=name):
                with self.assertRaises(ComprobanteInvalido):
                    inspeccionar_comprobante(name, data)

if __name__ == "__main__":
    unittest.main()
