"""Tests del ciclo sin navegador.

Se stubea la sesion: lo que importa es que la secuencia sea la correcta y que
un POST no se reintente nunca.
"""

import unittest
from decimal import Decimal

from motor.ciclo_http import Ciclo, Resultado
from motor.config import ConfigSubasta
from motor.sesion import ErrorDePlataforma, SesionCaida
from tests.test_formulario import html_cheques, html_subasta
from tests.test_libro import armar_html

IDENT = 1556714


def pantalla(filas, ident=IDENT):
    """Una pantalla con libro y formulario, como la real."""
    return armar_html(ident, filas) + html_subasta(ident)


def mia(tasa, hora="10:00:00", id=1):
    return {"id": id, "ag": "442", "tasa": tasa, "hora": hora, "propia": True}


def ajena(tasa, hora="10:00:00", id=2, ag="999"):
    return {"id": id, "ag": ag, "tasa": tasa, "hora": hora, "propia": False}


class SesionFalsa:
    def __init__(self, paginas, cheques=None):
        self.paginas = list(paginas)
        self._cheques = cheques if cheques is not None else html_cheques()
        self.posts = []
        self.lecturas = 0

    def subasta(self, ident):
        self.lecturas += 1
        return self.paginas[min(self.lecturas - 1, len(self.paginas) - 1)]

    def cheques(self, ident):
        return self._cheques

    def postear(self, programa, pares, referer_ident=None):
        self.posts.append(dict(pares))
        return ""


class SesionQueSeCae(SesionFalsa):
    def subasta(self, ident):
        raise SesionCaida("la sesion vencio")


class SesionConRuido(SesionFalsa):
    def subasta(self, ident):
        raise ErrorDePlataforma("timeout")


class Log:
    def __init__(self):
        self.eventos = []

    def __call__(self, evento, mostrar=None, **datos):
        self.eventos.append(evento)


def config(**kw):
    base = dict(ident=IDENT, piso=Decimal("25.00"), intervalo_min_s=0.0)
    base.update(kw)
    return ConfigSubasta(**base)


class TestSombra(unittest.TestCase):
    def test_no_postea_nunca(self):
        s = SesionFalsa([pantalla([mia("27,00"), ajena("26,99")])])
        c = Ciclo(config(), s, vivo=False, log=Log())
        paso = c.tick()
        self.assertIs(paso.resultado, Resultado.HARIA)
        self.assertEqual(paso.tasa, "26,98")
        self.assertEqual(s.posts, [])

    def test_arma_el_payload_igual_para_ejercitar_la_verificacion(self):
        # En sombra tambien se arma: es donde corre el control de que no se
        # invento ningun campo, y queremos que se ejercite antes del modo vivo.
        s = SesionFalsa([pantalla([mia("27,00"), ajena("26,99")])])
        log = Log()
        Ciclo(config(), s, vivo=False, log=log).tick()
        self.assertIn("sombra", log.eventos)

    def test_no_mueve_si_ya_va_ganando(self):
        s = SesionFalsa([pantalla([mia("26,00"), ajena("26,50")])])
        paso = Ciclo(config(), s, vivo=False, log=Log()).tick()
        self.assertIs(paso.resultado, Resultado.MIRANDO)


class TestVivo(unittest.TestCase):
    def test_postea_una_sola_vez_y_confirma(self):
        antes = pantalla([mia("27,00"), ajena("26,99")])
        despues = pantalla([mia("26,98", id=1), ajena("26,99")])
        s = SesionFalsa([antes, despues, despues])
        paso = Ciclo(config(), s, vivo=True, log=Log()).tick()
        self.assertIs(paso.resultado, Resultado.COTIZO)
        self.assertEqual(len(s.posts), 1)
        self.assertEqual(s.posts[0]["tasa"], "26,98")
        self.assertEqual(s.posts[0]["action"], "altaCompra")

    def test_el_post_lleva_los_comitentes_del_iframe(self):
        antes = pantalla([mia("27,00"), ajena("26,99")])
        despues = pantalla([mia("26,98", id=1), ajena("26,99")])
        s = SesionFalsa([antes, despues, despues])
        Ciclo(config(), s, vivo=True, log=Log()).tick()
        enviado = s.posts[0]
        self.assertEqual(enviado["comitcpr02579750"], "51414")
        self.assertEqual(enviado["cuitcpr02579750"], "30-11111111-1")

    def test_si_el_libro_no_confirma_para_y_no_reintenta(self):
        antes = pantalla([mia("27,00"), ajena("26,99")])
        # El libro vuelve sin cambios: la oferta no entro, o entro otra cosa.
        s = SesionFalsa([antes, antes, antes])
        c = Ciclo(config(), s, vivo=True, log=Log())
        paso = c.tick()
        self.assertIs(paso.resultado, Resultado.DETENIDO)
        self.assertEqual(len(s.posts), 1, "no se reintenta nunca")
        self.assertTrue(c.detenido)


class TestFallaCerrado(unittest.TestCase):
    def test_sesion_vencida_para_el_bot(self):
        # El 2FA impide re-loguearse solo, asi que esto siempre termina en parar.
        c = Ciclo(config(), SesionQueSeCae([]), vivo=True, log=Log())
        paso = c.tick()
        self.assertIs(paso.resultado, Resultado.DETENIDO)
        self.assertTrue(c.detenido)

    def test_un_error_de_red_no_para_el_bot(self):
        # Un timeout no es una sesion caida: se reintenta la lectura en la
        # vuelta siguiente, que es distinto de reintentar una orden.
        c = Ciclo(config(), SesionConRuido([]), vivo=True, log=Log())
        paso = c.tick()
        self.assertIs(paso.resultado, Resultado.MIRANDO)
        self.assertFalse(c.detenido)

    def test_pantalla_desconocida_para_el_bot(self):
        c = Ciclo(config(), SesionFalsa(["<html>otra cosa</html>"]),
                  vivo=True, log=Log())
        self.assertIs(c.tick().resultado, Resultado.DETENIDO)
        self.assertTrue(c.detenido)

    def test_sin_comitentes_no_cotiza(self):
        s = SesionFalsa([pantalla([mia("27,00"), ajena("26,99")])],
                        cheques="<html>sin nada</html>")
        c = Ciclo(config(), s, vivo=True, log=Log())
        paso = c.tick()
        self.assertIs(paso.resultado, Resultado.DETENIDO)
        self.assertEqual(s.posts, [])

    def test_kill_switch(self):
        s = SesionFalsa([pantalla([mia("27,00"), ajena("26,99")])])
        c = Ciclo(config(), s, vivo=True, log=Log())
        c.parar()
        self.assertIs(c.tick().resultado, Resultado.DETENIDO)
        self.assertEqual(s.posts, [])

    def test_cede_en_el_piso_sin_postear(self):
        s = SesionFalsa([pantalla([mia("25,50"), ajena("25,00")])])
        c = Ciclo(config(piso=Decimal("25.00")), s, vivo=True, log=Log())
        self.assertIs(c.tick().resultado, Resultado.CEDIDO)
        self.assertEqual(s.posts, [])


if __name__ == "__main__":
    unittest.main()
