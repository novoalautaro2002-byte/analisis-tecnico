"""Tests de la unica capa que habla con el navegador.

Se stubea Playwright: lo que importa es que encuentre el frame correcto y que
pare cuando no entiende lo que lee. La decision y el gate ya estan probados
aparte.
"""

import sys
import types
import unittest
from pathlib import Path

# bot.py se niega a importar sin Playwright. Como acá solo se prueban funciones
# que reciben objetos ya construidos, alcanza con un doble.
if "playwright" not in sys.modules:
    falso = types.ModuleType("playwright")
    api = types.ModuleType("playwright.sync_api")
    api.sync_playwright = lambda: None
    falso.sync_api = api
    sys.modules["playwright"] = falso
    sys.modules["playwright.sync_api"] = api

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from motor import pantalla  # noqa: E402
from tests.test_libro import armar_html  # noqa: E402


class Marco:
    def __init__(self, url, html=""):
        self.url = url
        self._html = html

    def content(self):
        return self._html


class MarcoRoto(Marco):
    def content(self):
        raise RuntimeError("el frame se estaba recargando")


class Pagina:
    def __init__(self, marcos):
        self.frames = marcos


class Contexto:
    def __init__(self, paginas):
        self.pages = paginas


class Navegador:
    def __init__(self, contextos):
        self.contexts = contextos


def navegador_con(*marcos):
    return Navegador([Contexto([Pagina(list(marcos))])])


BASE = "https://trading.mav-sa.com.ar/cgi-bin/wspd_cgi.sh/WService=wsbroker1/"


class Log:
    def __init__(self):
        self.eventos = []

    def __call__(self, evento, mostrar=None, **datos):
        self.eventos.append(evento)


class TestBuscarMarco(unittest.TestCase):
    def test_encuentra_la_subasta_pedida(self):
        nav = navegador_con(
            Marco(BASE + "k-cabeceranew.r"),
            Marco(BASE + "cpd-versubasta.r?ident=1556714"),
        )
        _, marco = pantalla.buscar_marco(nav, 1556714)
        self.assertIsNotNone(marco)
        self.assertIn("ident=1556714", marco.url)

    def test_ignora_otra_subasta(self):
        # Si hay dos subastas abiertas, el bot solo toca la suya.
        nav = navegador_con(Marco(BASE + "cpd-versubasta.r?ident=999999"))
        _, marco = pantalla.buscar_marco(nav, 1556714)
        self.assertIsNone(marco)

    def test_ignora_otras_pantallas(self):
        nav = navegador_con(Marco(BASE + "cpd-subastas-listado.r?ident=1556714"))
        _, marco = pantalla.buscar_marco(nav, 1556714)
        self.assertIsNone(marco)

    def test_sin_nada_abierto(self):
        self.assertEqual(pantalla.buscar_marco(Navegador([]), 1556714), (None, None))


class TestLeerLibro(unittest.TestCase):
    def test_lee_bien(self):
        html = armar_html(1556714, [
            {"id": 1, "ag": "442", "tasa": "26,00", "hora": "11:00:00", "propia": True},
        ])
        libro = pantalla.leer_libro(Marco("x", html), Log())
        self.assertEqual(libro.ident, 1556714)

    def test_pantalla_desconocida_devuelve_none(self):
        # Si MAV cambia la pantalla, el bot tiene que parar, no improvisar.
        log = Log()
        self.assertIsNone(pantalla.leer_libro(Marco("x", "<html>otra cosa</html>"), log))
        self.assertIn("libro_ilegible", log.eventos)

    def test_frame_recargandose_devuelve_none(self):
        log = Log()
        self.assertIsNone(pantalla.leer_libro(MarcoRoto("x"), log))
        self.assertIn("lectura_fallida", log.eventos)


class TestSelectores(unittest.TestCase):
    """Los dos unicos elementos que el bot toca en la pantalla."""

    def test_existen_en_el_html_real(self):
        ruta = (Path(__file__).parent / "fixtures" / "versubasta_controles.html")
        html = ruta.read_text(encoding="utf-8")
        self.assertIn('name="tasa"', html)
        self.assertIn('value="Modificar Tasa Cpr."', html)
        # Los selectores del bot tienen que coincidir con eso, literal.
        self.assertEqual(pantalla.SEL_TASA, 'input[name="tasa"]')
        self.assertEqual(pantalla.SEL_BOTON, 'input[value="Modificar Tasa Cpr."]')


if __name__ == "__main__":
    unittest.main()
