"""Tests de la fila del listado.

El listado es la unica forma de saber el estado de una subasta sin abrirla, y
ademas es lo que permite vigilar N subastas con un pedido en vez de N. Lo que
importa es que nunca levante una excepcion (es informativo) y que la huella
cambie exactamente cuando cambia la punta compradora.
"""

import unittest
from decimal import Decimal

from motor.ficha import Ficha, leer_ficha

FILA = {
    "ident": "1556714", "estado": "Activa", "segmento": "Avalado",
    "tasa-cpr": "25,44", "agente-cpr": "442", "agente-vdr": "442",
    "tasa-vdr": "27,00", "tiempo-minimo": "15:00:00",
    "tiempo-minimo-ss": "54000", "hora-cierre": "15:03:00",
    "hora-cierre-ss": "54180", "cantidad-cheques": "3",
    "monto": "1.500.000,00", "ya-negociado": "no", "moneda-signo": "$",
}


class TestLectura(unittest.TestCase):
    def test_lee_lo_que_importa(self):
        f = leer_ficha(FILA)
        self.assertEqual(f.ident, 1556714)
        self.assertEqual(f.tasa_cpr, Decimal("25.44"))
        self.assertEqual(f.agente_cpr, "442")
        self.assertEqual(f.tiempo_minimo, "15:00:00")
        self.assertEqual(f.hora_cierre_ss, 54180)
        self.assertEqual(f.cantidad_cheques, 3)
        self.assertEqual(f.monto, Decimal("1500000.00"))
        self.assertFalse(f.ya_negociado)

    def test_acepta_numeros_de_verdad(self):
        # Progress manda a veces el numero y a veces la cadena con coma.
        f = leer_ficha({**FILA, "tasa-cpr": 25.44, "cantidad-cheques": 3})
        self.assertEqual(f.tasa_cpr, Decimal("25.44"))
        self.assertEqual(f.cantidad_cheques, 3)

    def test_un_campo_ilegible_no_rompe_la_fila(self):
        f = leer_ficha({**FILA, "tasa-cpr": "ayer", "cantidad-cheques": ""})
        self.assertIsNone(f.tasa_cpr)
        self.assertIsNone(f.cantidad_cheques)
        self.assertEqual(f.estado, "Activa")       # lo demas sigue sirviendo

    def test_sin_ident_no_hay_ficha(self):
        self.assertIsNone(leer_ficha({"estado": "Activa"}))
        self.assertIsNone(leer_ficha({}))


class TestEstado(unittest.TestCase):
    def test_activa_es_viva(self):
        self.assertTrue(leer_ficha(FILA).viva)

    def test_negociada_y_desierta_no(self):
        for estado in ("Negociada", "Desierta", "Anulada", "Vencida"):
            self.assertFalse(leer_ficha({**FILA, "estado": estado}).viva, estado)

    def test_sin_estado_se_asume_viva(self):
        # No entender el estado no puede ser motivo para frenar solo.
        self.assertTrue(leer_ficha({**FILA, "estado": ""}).viva)


class TestHuella(unittest.TestCase):
    """La huella decide cuando vale la pena abrir la subasta."""

    def test_no_cambia_si_no_cambia_la_punta(self):
        self.assertEqual(leer_ficha(FILA).huella,
                         leer_ficha({**FILA, "monto": "9"}).huella)

    def test_cambia_si_mejoran_la_tasa(self):
        self.assertNotEqual(leer_ficha(FILA).huella,
                            leer_ficha({**FILA, "tasa-cpr": "25,43"}).huella)

    def test_cambia_si_otro_agente_toma_la_punta(self):
        # Mismo precio, otro dueño: es un desempate que hay que ir a mirar.
        self.assertNotEqual(leer_ficha(FILA).huella,
                            leer_ficha({**FILA, "agente-cpr": "406"}).huella)

    def test_cambia_si_se_negocia(self):
        self.assertNotEqual(leer_ficha(FILA).huella,
                            leer_ficha({**FILA, "estado": "Negociada"}).huella)


if __name__ == "__main__":
    unittest.main()
