"""Tests de la interfaz.

Es la superficie desde la que se lanzan órdenes reales, así que lo que importa
es que sin sesión no arranque nada, que la contraseña no se filtre al estado, y
que se puedan vigilar varias subastas a la vez.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui  # noqa: E402
from motor.config import ConfigInvalida  # noqa: E402
from motor.libro import LibroIlegible  # noqa: E402
from motor.sesion import ErrorDePlataforma  # noqa: E402
from tests.test_vigilante import SesionFalsa  # noqa: E402

BUENA = {
    "ident": 1556714, "piso": "25,00",
    "decremento_min": "0,01", "decremento_max": "0,03",
    "prob": "1", "espera_min": "0", "espera_max": "0",
    "sondeo": "1", "max_recotizaciones": "60",
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.original = ui.AQUI
        ui.AQUI = self.tmp            # los logs de prueba no van al repo
        self.t = ui.Trabajador()
        self.t.eco = False

    def tearDown(self):
        ui.AQUI = self.original
        if self.t.log:
            self.t.log.cerrar()

    def con_sesion(self, agente="442"):
        self.t.agente = agente
        self.t.sesion = SesionFalsa()
        self.t._tras_ingreso(None)
        return self.t


class TestAgente(Base):
    """En MAV se opera por agente: sin ese numero no se sabe que oferta es tuya."""

    def test_viene_puesto_el_del_trader(self):
        # Es constante y no cambia nunca: tenerlo que escribir cada vez es
        # friccion sin beneficio.
        self.assertEqual(ui.Trabajador().agente, ui.AGENTE_POR_DEFECTO)

    def test_si_se_vacia_no_se_puede_sumar(self):
        self.t.sesion = SesionFalsa()
        self.t._tras_ingreso(None)
        self.t._agente("")
        with self.assertRaises(ErrorDePlataforma):
            self.t._sumar(dict(BUENA))
        self.assertEqual(self.t.mesa.activos, [])

    def test_el_agente_viaja_a_la_config(self):
        t = self.con_sesion("442")
        t._sumar(dict(BUENA))
        self.assertEqual(t.mesa.vigilantes[1556714].cfg.mi_agente, "442")

    def test_una_oferta_del_agente_propio_es_propia(self):
        from motor.libro import parsear_libro
        from tests.test_libro import armar_html
        # Sin link de baja: lo que decide es el numero de agente.
        html = armar_html(900, [
            {"id": 1, "ag": "442", "tasa": "26,00", "hora": "10:00:00",
             "propia": False},
            {"id": 2, "ag": "406", "tasa": "25,50", "hora": "10:01:00",
             "propia": False},
        ])
        libro = parsear_libro(html, "442")
        self.assertEqual([o.id for o in libro.propias], [1])
        self.assertEqual([o.id for o in libro.ajenas], [2])


class TestSinSesion(Base):
    def test_no_se_puede_sumar_una_subasta(self):
        self.t.agente = "442"
        with self.assertRaises(ErrorDePlataforma):
            self.t._sumar(dict(BUENA))
        self.assertIsNone(self.t.mesa)

    def test_no_se_puede_mirar(self):
        with self.assertRaises(ErrorDePlataforma):
            self.t._mirar(900)

    def test_el_error_llega_a_la_pantalla_y_no_tumba_el_hilo(self):
        self.t.pedir("sumar", **BUENA)
        self.t._atender()
        self.assertIn("ingres", self.t.estado["aviso"].lower())


class TestMesaDesdeLaInterfaz(Base):
    def test_suma_varias_subastas(self):
        t = self.con_sesion()
        for ident in (100, 200, 300):
            t._sumar({**BUENA, "ident": ident})
        self.assertEqual(len(t.mesa.activos), 3)

    def test_sombra_por_defecto(self):
        t = self.con_sesion()
        t._sumar(dict(BUENA))
        self.assertFalse(t.mesa.vigilantes[1556714].vivo)

    def test_vivo_solo_si_se_pide(self):
        t = self.con_sesion()
        t._sumar({**BUENA, "vivo": True})
        self.assertTrue(t.mesa.vigilantes[1556714].vivo)

    def test_config_invalida_no_suma_nada(self):
        t = self.con_sesion()
        with self.assertRaises(ConfigInvalida):
            t._sumar({**BUENA, "piso": "9999,00"})
        self.assertEqual(t.mesa.activos, [])

    def test_piso_ilegible_no_suma_nada(self):
        t = self.con_sesion()
        with self.assertRaises(LibroIlegible):
            t._sumar({**BUENA, "piso": "veinticinco"})
        self.assertEqual(t.mesa.activos, [])

    def test_sondeo_demasiado_rapido_se_rechaza(self):
        t = self.con_sesion()
        with self.assertRaises(ConfigInvalida):
            t._sumar({**BUENA, "sondeo": "0.1"})

    def test_parar_frena_todas(self):
        t = self.con_sesion()
        for ident in (100, 200):
            t._sumar({**BUENA, "ident": ident, "vivo": True})
        t._despachar("parar", {})
        self.assertEqual(t.mesa.activos, [])

    def test_sacar_deja_las_demas(self):
        t = self.con_sesion()
        for ident in (100, 200):
            t._sumar({**BUENA, "ident": ident})
        t._despachar("sacar", {"ident": 100})
        self.assertEqual([v.cfg.ident for v in t.mesa.activos], [200])


class TestEstadoQueVeLaPantalla(Base):
    def test_la_contraseña_no_se_filtra(self):
        # Se prueba contra el estado que realmente sirve el servidor. La sesion
        # se sustituye para que el test no salga a la red.
        from motor.sesion import IngresoRechazado

        class Rechaza(SesionFalsa):
            def __init__(self, *a, **kw):
                super().__init__()

            def ingresar(self, usuario, clave):
                raise IngresoRechazado("credenciales incorrectas")

        original, ui.Sesion = ui.Sesion, Rechaza
        try:
            self.t.pedir("ingresar", usuario="lautaro", clave="NOAPARECER123")
            self.t._atender()
        finally:
            ui.Sesion = original
        self.assertNotIn("NOAPARECER123", str(self.t.leer_estado()))
        self.assertIn("incorrect", self.t.estado["aviso"])

    def test_lista_las_subastas_con_su_fase(self):
        t = self.con_sesion()
        t._sumar({**BUENA, "ident": 100})
        vistas = t.leer_estado()["subastas"]
        self.assertEqual(len(vistas), 1)
        self.assertEqual(vistas[0]["ident"], 100)
        self.assertIn("fase", vistas[0])

    def test_mirar_no_suma_a_la_mesa(self):
        # "Mirar" es una lectura suelta: no pone a operar nada.
        t = self.con_sesion()
        t._mirar(1556714)
        self.assertEqual(t.mesa.activos, [])
        self.assertEqual(t.estado["mirado"]["ident"], 1556714)


if __name__ == "__main__":
    unittest.main()
