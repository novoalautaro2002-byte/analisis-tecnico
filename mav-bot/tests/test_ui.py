"""Tests del trabajador de la interfaz.

La interfaz es la superficie desde la que se lanzan ordenes reales, asi que lo
que importa es que una config invalida no arranque nada y que parar realmente
pare.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: E402
from motor.libro import parsear_libro  # noqa: E402
from tests.test_libro import armar_html  # noqa: E402

BUENA = {
    "ident": 1556714, "piso": "25,00",
    "decremento_min": "0,01", "decremento_max": "0,03",
    "prob": "1", "espera_min": "0", "espera_max": "0",
    "max_recotizaciones": "60", "intervalo_min": "10",
}


class SesionFalsa:
    def get(self, *a, **k): return ""
    def subasta(self, ident): return ""
    def cheques(self, ident): return ""


def trabajador(tmp):
    t = ui.Trabajador()
    t.eco = False
    t.sesion = SesionFalsa()      # el arranque exige sesion conectada
    ui.AQUI = tmp                 # los logs de prueba no van al repo
    return t


class TestArrancar(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.original = ui.AQUI
        self.t = trabajador(self.tmp)

    def tearDown(self):
        ui.AQUI = self.original
        if self.t.log:
            self.t.log.cerrar()

    def test_arranca_en_sombra_por_defecto(self):
        self.t._arrancar(dict(BUENA))
        self.assertTrue(self.t.estado["corriendo"])
        self.assertEqual(self.t.estado["modo"], "sombra")
        self.assertFalse(self.t.ciclo.vivo)

    def test_vivo_solo_si_se_pide(self):
        self.t._arrancar({**BUENA, "vivo": True})
        self.assertEqual(self.t.estado["modo"], "vivo")
        self.assertTrue(self.t.ciclo.vivo)

    def test_config_invalida_no_arranca_nada(self):
        from motor.config import ConfigInvalida
        with self.assertRaises(ConfigInvalida):
            self.t._arrancar({**BUENA, "piso": "9999,00"})
        self.assertFalse(self.t.estado["corriendo"])
        self.assertIsNone(self.t.ciclo)

    def test_piso_ilegible_no_arranca_nada(self):
        from motor.libro import LibroIlegible
        with self.assertRaises(LibroIlegible):
            self.t._arrancar({**BUENA, "piso": "veinticinco"})
        self.assertIsNone(self.t.ciclo)

    def test_sin_sesion_no_arranca(self):
        self.t.sesion = None
        self.t._arrancar(dict(BUENA))
        self.assertFalse(self.t.estado["corriendo"])
        self.assertIsNone(self.t.ciclo)
        self.assertIn("cookie", self.t.estado["aviso"])

    def test_parar_activa_el_kill_switch(self):
        self.t._arrancar(dict(BUENA))
        self.t._frenar("parado a mano")
        self.assertFalse(self.t.estado["corriendo"])
        self.assertTrue(self.t.ciclo.detenido)


class TestLibroJson(unittest.TestCase):
    def _libro(self, filas):
        return parsear_libro(armar_html(900, filas))

    def test_ordena_por_tasa_y_marca_la_propia(self):
        libro = self._libro([
            {"id": 1, "ag": "442", "tasa": "26,00", "hora": "10:00:00", "propia": True},
            {"id": 2, "ag": "999", "tasa": "25,50", "hora": "10:01:00", "propia": False},
        ])
        j = ui.Trabajador._libro_json(libro)
        self.assertEqual([o["tasa"] for o in j["ofertas"]], ["25,50", "26,00"])
        self.assertTrue(j["ofertas"][1]["propia"])
        self.assertEqual(j["mia"], "26,00")
        self.assertEqual(j["mejor_ajena"], "25,50")
        self.assertFalse(j["gano"])

    def test_marca_cuando_vas_ganando(self):
        libro = self._libro([
            {"id": 1, "ag": "442", "tasa": "25,00", "hora": "10:00:00", "propia": True},
            {"id": 2, "ag": "999", "tasa": "25,50", "hora": "10:01:00", "propia": False},
        ])
        self.assertTrue(ui.Trabajador._libro_json(libro)["gano"])

    def test_sin_oferta_propia_no_gana(self):
        libro = self._libro([
            {"id": 2, "ag": "999", "tasa": "25,50", "hora": "10:01:00", "propia": False},
        ])
        j = ui.Trabajador._libro_json(libro)
        self.assertIsNone(j["mia"])
        self.assertFalse(j["gano"])


if __name__ == "__main__":
    unittest.main()
