"""Tests del vigilante y de la mesa.

Lo que se prueba acá es lo que estaba roto: que el ciclo no bloquee, que una
falla de red no lo frene, que reconozca el estado de la subasta, y que varias
subastas avancen sin esperarse entre sí.
"""

import time
import unittest
from decimal import Decimal

from motor.config import ConfigSubasta
from motor.mesa import Mesa
from motor.sesion import ErrorDePlataforma, SesionCaida
from motor.vigilante import FALLAS_TOLERADAS, Fase, Vigilante
from tests.test_formulario import html_cheques, html_subasta
from tests.test_libro import armar_html

IDENT = 1556714


def pantalla(filas, ident=IDENT):
    return armar_html(ident, list(filas)) + html_subasta(ident)


def mia(tasa, hora="10:00:00", id=1):
    return {"id": id, "ag": "442", "tasa": tasa, "hora": hora, "propia": True}


def ajena(tasa, hora="10:00:00", id=2, ag="999"):
    return {"id": id, "ag": ag, "tasa": tasa, "hora": hora, "propia": False}


class Log:
    def __init__(self):
        self.eventos = []

    def __call__(self, evento, mostrar=None, **datos):
        self.eventos.append(evento)


class SesionFalsa:
    def __init__(self, paginas=None, estado="Activa"):
        self.paginas = paginas if paginas is not None else {}
        self.estado = estado
        self.posts = []
        self.lecturas = 0

    def subasta(self, ident):
        self.lecturas += 1
        return self.paginas.get(ident, pantalla([mia("27,00"), ajena("26,99")],
                                                ident))

    def cheques(self, ident):
        return html_cheques()

    def estado_subasta(self, ident):
        return {"estado": self.estado, "segmento": "Avalado"} if self.estado else None

    def postear(self, programa, pares, referer_ident=None):
        self.posts.append(dict(pares))
        return ""


class SesionCaediza(SesionFalsa):
    def subasta(self, ident):
        raise ErrorDePlataforma("timeout")


def config(**kw):
    base = dict(ident=IDENT, mi_agente="442", piso=Decimal("25.00"))
    base.update(kw)
    return ConfigSubasta(**base)


class TestNoBloquea(unittest.TestCase):
    """El defecto de fondo del diseño anterior: dormía adentro del ciclo."""

    def test_el_tick_vuelve_enseguida_aunque_haya_espera_larga(self):
        cfg = config(espera_min_s=60, espera_max_s=60)
        v = Vigilante(cfg, SesionFalsa(), vivo=False, log=Log())
        arranque = time.monotonic()
        v.tick(time.monotonic())
        self.assertLess(time.monotonic() - arranque, 0.5)
        self.assertIs(v.fase, Fase.ESPERANDO)

    def test_la_espera_se_agenda_no_se_duerme(self):
        cfg = config(espera_min_s=30, espera_max_s=30)
        ahora = 1000.0
        v = Vigilante(cfg, SesionFalsa(), vivo=False, log=Log())
        v.tick(ahora)
        self.assertIsNotNone(v.cotizar_en_s)
        self.assertAlmostEqual(v.cotizar_en_s, ahora + 30, places=1)
        # Y hasta que no sea la hora, no gasta lecturas de más.
        antes = v.sesion.lecturas
        v.tick(ahora + 1)
        self.assertEqual(v.sesion.lecturas, antes)

    def test_no_repite_la_lectura_al_cotizar(self):
        # El payload se arma con el HTML que ya se leyo: un round-trip menos
        # por recotizacion.
        s = SesionFalsa()
        v = Vigilante(config(), s, vivo=False, log=Log())
        v.tick(1000.0)
        self.assertEqual(s.lecturas, 1)

    def test_cotiza_cuando_se_cumple_la_espera(self):
        cfg = config(espera_min_s=5, espera_max_s=5, prob_respuesta=1.0)
        s = SesionFalsa()
        v = Vigilante(cfg, s, vivo=True, log=Log())
        v.tick(1000.0)
        self.assertIs(v.fase, Fase.ESPERANDO)
        self.assertEqual(s.posts, [])
        v.tick(1006.0)
        self.assertEqual(len(s.posts), 1)
        self.assertEqual(s.posts[0]["tasa"], "26,98")


class TestSigueAndando(unittest.TestCase):
    """Lo pasajero no puede frenar al bot; eso era 'se para solo'."""

    def test_un_timeout_no_lo_frena(self):
        v = Vigilante(config(), SesionCaediza(), vivo=True, log=Log())
        v.tick(1000.0)
        self.assertFalse(v.terminado)
        self.assertEqual(v.fallas, 1)

    def test_reintenta_con_backoff_creciente(self):
        v = Vigilante(config(), SesionCaediza(), vivo=True, log=Log())
        esperas = []
        ahora = 1000.0
        for _ in range(3):
            v.tick(ahora)
            esperas.append(v.proxima_s - ahora)
            ahora = v.proxima_s
        self.assertEqual(esperas, sorted(esperas))
        self.assertLess(esperas[0], esperas[-1])

    def test_se_rinde_recien_tras_varias_fallas_seguidas(self):
        v = Vigilante(config(), SesionCaediza(), vivo=True, log=Log())
        ahora = 1000.0
        for _ in range(FALLAS_TOLERADAS):
            v.tick(ahora)
            ahora = v.proxima_s
        self.assertIs(v.fase, Fase.DETENIDO)

    def test_una_lectura_buena_borra_las_fallas(self):
        s = SesionFalsa()
        v = Vigilante(config(), s, vivo=False, log=Log())
        v.fallas = 3
        v.tick(1000.0)
        self.assertEqual(v.fallas, 0)

    def test_la_sesion_caida_si_lo_frena(self):
        # El 2FA impide re-loguearse solo: no hay nada que reintentar.
        class Caida(SesionFalsa):
            def subasta(self, ident):
                raise SesionCaida("la sesión venció")

        v = Vigilante(config(), Caida(), vivo=True, log=Log())
        v.tick(1000.0)
        self.assertIs(v.fase, Fase.DETENIDO)


class TestEstadoDeLaSubasta(unittest.TestCase):
    def test_no_opera_en_una_subasta_negociada(self):
        s = SesionFalsa(estado="Concertada")
        v = Vigilante(config(), s, vivo=True, log=Log())
        v.tick(1000.0)
        self.assertIs(v.fase, Fase.CERRADA)
        self.assertEqual(s.posts, [])

    def test_no_opera_en_una_desierta(self):
        v = Vigilante(config(), SesionFalsa(estado="Desierta"), vivo=True, log=Log())
        v.tick(1000.0)
        self.assertIs(v.fase, Fase.CERRADA)

    def test_en_una_activa_trabaja(self):
        v = Vigilante(config(), SesionFalsa(estado="Activa"), vivo=False, log=Log())
        v.tick(1000.0)
        self.assertFalse(v.terminado)

    def test_si_no_puede_leer_el_estado_sigue_igual(self):
        # Es informativo: que falle el dato no puede dejar al trader sin bot.
        v = Vigilante(config(), SesionFalsa(estado=None), vivo=False, log=Log())
        v.tick(1000.0)
        self.assertFalse(v.terminado)

    def test_cede_en_el_piso_y_ahi_si_termina(self):
        s = SesionFalsa({IDENT: pantalla([mia("25,50"), ajena("25,00")])})
        v = Vigilante(config(piso=Decimal("25.00")), s, vivo=True, log=Log())
        v.tick(1000.0)
        self.assertIs(v.fase, Fase.CEDIDO)
        self.assertEqual(s.posts, [])


class TestMesa(unittest.TestCase):
    def test_vigila_varias_subastas(self):
        s = SesionFalsa()
        m = Mesa(s, Log())
        for ident in (100, 200, 300):
            m.sumar(config(ident=ident), vivo=False)
        self.assertEqual(len(m.activos), 3)

    def test_atiende_una_por_vuelta(self):
        # Con el bot ganando, cada vuelta es una sola lectura y se ve claro el
        # reparto entre subastas.
        s = SesionFalsa({100: pantalla([mia("25,50"), ajena("26,00")], 100),
                         200: pantalla([mia("25,50"), ajena("26,00")], 200)})
        m = Mesa(s, Log())
        for ident in (100, 200):
            m.sumar(config(ident=ident, sondeo_s=5), vivo=False)
        ahora = 1000.0
        self.assertTrue(m.tick(ahora))
        self.assertEqual(s.lecturas, 1, "no puede atender a las dos de golpe")
        self.assertTrue(m.tick(ahora))
        self.assertEqual(s.lecturas, 2)

    def test_una_subasta_terminada_no_traba_a_las_otras(self):
        s = SesionFalsa()
        m = Mesa(s, Log())
        viva = m.sumar(config(ident=100), vivo=False)
        muerta = m.sumar(config(ident=200), vivo=False)
        muerta.parar("de prueba")
        self.assertEqual([v.cfg.ident for v in m.activos], [100])
        m.tick(1000.0)
        self.assertFalse(viva.terminado)

    def test_el_kill_switch_frena_todas(self):
        m = Mesa(SesionFalsa(), Log())
        for ident in (100, 200, 300):
            m.sumar(config(ident=ident), vivo=True)
        m.parar_todo()
        self.assertEqual(m.activos, [])

    def test_sacar_una_deja_las_demas(self):
        m = Mesa(SesionFalsa(), Log())
        m.sumar(config(ident=100), vivo=False)
        m.sumar(config(ident=200), vivo=False)
        m.sacar(100)
        self.assertEqual([v.cfg.ident for v in m.activos], [200])

    def test_no_duerme_de_mas_cuando_hay_trabajo(self):
        m = Mesa(SesionFalsa(), Log())
        m.sumar(config(ident=100, sondeo_s=1), vivo=False)
        self.assertEqual(m.dormir_hasta(1000.0), 0.0)


if __name__ == "__main__":
    unittest.main()
