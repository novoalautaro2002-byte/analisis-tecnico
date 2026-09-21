"""Tests de la conversacion con la plataforma."""

import unittest

from motor.sesion import (
    CODIFICACION,
    ErrorDePlataforma,
    Sesion,
    es_pantalla_de_login,
)


class TestCookie(unittest.TestCase):
    def test_acepta_el_volcado_entero_de_document_cookie(self):
        # Se acepta todo para que el trader copie y pegue sin buscar cual es.
        s = Sesion.desde_texto(
            "_ga=GA1.2.999; mvrcookie=ABC123; mvrusername=lautaro; otra=x")
        self.assertEqual(s.cookies["mvrcookie"], "ABC123")
        self.assertEqual(s.cookies["mvrusername"], "lautaro")
        self.assertNotIn("_ga", s.cookies)

    def test_acepta_solo_la_cookie_de_sesion(self):
        self.assertEqual(
            Sesion.desde_texto("mvrcookie=ABC123").cookies, {"mvrcookie": "ABC123"})

    def test_sin_mvrcookie_avisa_claro(self):
        for basura in ("", "mvrusername=lautaro", "hola"):
            with self.assertRaises(ErrorDePlataforma) as e:
                Sesion.desde_texto(basura)
            self.assertIn("mvrcookie", str(e.exception))

    def test_arma_la_cabecera(self):
        s = Sesion(cookies={"mvrcookie": "A", "mvrusername": "b"})
        self.assertEqual(s.cabecera_cookie, "mvrcookie=A; mvrusername=b")


class TestDeteccionDeLogin(unittest.TestCase):
    def test_reconoce_la_pantalla_de_login(self):
        self.assertTrue(es_pantalla_de_login(
            '<html><head><title>Inicio de Sesi&oacute;n</title></head>'))
        self.assertTrue(es_pantalla_de_login(
            '<form method="POST" name="form" action="validar2.r">'))

    def test_no_confunde_una_subasta(self):
        self.assertFalse(es_pantalla_de_login(
            '<html><script>var myData = [];</script>'
            '<input type="hidden" name="ident" value="900"></html>'))

    def test_solo_mira_el_principio(self):
        # La palabra puede aparecer al pie de cualquier pagina larga; el
        # formulario de login esta siempre arriba.
        self.assertFalse(es_pantalla_de_login("x" * 5000 + "validar2.r"))


class TestCodificacion(unittest.TestCase):
    def test_es_la_de_la_plataforma(self):
        # MAV emite y espera ISO-8859-1: mandar UTF-8 rompe los acentos.
        self.assertEqual(CODIFICACION, "latin-1")


if __name__ == "__main__":
    unittest.main()
