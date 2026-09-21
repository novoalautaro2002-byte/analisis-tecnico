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
from motor.vigilante import (FALLAS_TOLERADAS, VENTANA_CONFIRMACION_S, Fase,
                             Vigilante)
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
    def __init__(self, paginas=None, estado="Activa", tasa_cpr=None,
                 demora_en_verse=0, acepta=True):
        self.paginas = paginas if paginas is not None else {}
        self.estado = estado
        self.tasa_cpr = tasa_cpr        # lo que el tablero dice de la punta
        self.demora_en_verse = demora_en_verse
        self.acepta = acepta
        self.pendiente = None
        self.posts = []
        self.lecturas = 0
        self.tableros = 0

    def subasta(self, ident):
        self.lecturas += 1
        self._quizas_mostrar()
        return self.paginas.get(ident, pantalla([mia("27,00"), ajena("26,99")],
                                                ident))

    def cheques(self, ident):
        return html_cheques()

    def _fila(self, ident):
        return {"ident": str(ident), "estado": self.estado,
                "segmento": "Avalado", "tasa-cpr": self.tasa_cpr,
                "agente-cpr": "442", "agente-vdr": "442",
                "tiempo-minimo": "15:00:00", "hora-cierre": "15:03:00",
                "cantidad-cheques": "3"}

    def estado_subasta(self, ident):
        return self._fila(ident) if self.estado else None

    def tablero(self):
        self.tableros += 1
        if not self.estado:
            return {}
        idents = set(self.paginas) | {IDENT}
        return {i: self._fila(i) for i in idents}

    def postear(self, programa, pares, referer_ident=None):
        """Acepta la oferta y la refleja en el libro, como haria MAV.

        Con `demora_en_verse` se simula lo que rompia al bot: la plataforma
        toma la oferta pero todavia contesta el libro viejo unas lecturas mas.
        Que la sesion falsa no hiciera esto es la razon por la que los tests no
        vieron el corte.
        """
        campos = dict(pares)
        self.posts.append(campos)
        self.pendiente = (int(campos["ident"]), campos["tasa"],
                          self.demora_en_verse)
        self._quizas_mostrar()
        return ""

    def _quizas_mostrar(self):
        if self.pendiente is None:
            return
        ident, tasa, faltan = self.pendiente
        if faltan > 0:
            self.pendiente = (ident, tasa, faltan - 1)
            return
        self.pendiente = None
        if self.acepta:
            self.paginas[ident] = pantalla(
                [mia(tasa), ajena("26,99")], ident)
            self.tasa_cpr = tasa


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

    def test_un_pedido_de_tablero_alcanza_para_todas(self):
        s = SesionFalsa(tasa_cpr="26,99")
        m = Mesa(s, Log())
        for ident in (100, 200, 300):
            m.sumar(config(ident=ident), vivo=False)
        for _ in range(3):
            m.tick(1000.0)
        self.assertEqual(s.tableros, 1, "un tablero por vuelta, no uno por subasta")

    def test_si_el_tablero_falla_nadie_se_frena(self):
        class SinTablero(SesionFalsa):
            def tablero(self):
                raise ErrorDePlataforma("500")

        s = SinTablero()
        m = Mesa(s, Log())
        m.sumar(config(ident=IDENT), vivo=False)
        m.tick(1000.0)
        self.assertEqual(s.lecturas, 1)
        self.assertFalse(m.activos[0].terminado)


class TestConfirmacion(unittest.TestCase):
    """El bot 'se cortaba solo apenas cargaba una tasa'.

    Causa: releia el libro pegado al POST, MAV todavia contestaba el libro
    viejo, y el control posterior lo leia como 'no entro lo que queria'.
    """

    def andando(self, **kw):
        s = SesionFalsa(**kw)
        v = Vigilante(config(), s, vivo=True, log=Log())
        return s, v

    def test_una_demora_de_la_plataforma_ya_no_lo_frena(self):
        s, v = self.andando(demora_en_verse=3)
        v.tick(1000.0)
        self.assertEqual(len(s.posts), 1)
        self.assertIs(v.fase, Fase.CONFIRMANDO)
        for i in range(1, 8):
            v.tick(1000.0 + i * 0.5)
        self.assertIs(v.fase, Fase.MIRANDO)
        self.assertEqual(len(s.posts), 1, "confirmar no puede repetir la orden")

    def test_no_decide_nada_nuevo_mientras_confirma(self):
        s, v = self.andando(demora_en_verse=2)
        v.tick(1000.0)
        v.tick(1000.5)
        self.assertEqual(len(s.posts), 1)
        self.assertIs(v.fase, Fase.CONFIRMANDO)

    def test_si_nunca_aparece_frena(self):
        # Fail closed: no saber que entro es lo unico que no se arregla
        # mirando de nuevo.
        s, v = self.andando(acepta=False)
        v.tick(1000.0)
        for i in range(1, 20):
            v.tick(1000.0 + i * 0.5)
        self.assertIs(v.fase, Fase.DETENIDO)
        self.assertEqual(len(s.posts), 1)

    def test_aguanta_toda_la_ventana_antes_de_frenar(self):
        s, v = self.andando(acepta=False)
        v.tick(1000.0)
        v.tick(1000.0 + VENTANA_CONFIRMACION_S - 1)
        self.assertIs(v.fase, Fase.CONFIRMANDO)
        v.tick(1000.0 + VENTANA_CONFIRMACION_S + 1)
        self.assertIs(v.fase, Fase.DETENIDO)

    def test_un_post_que_fallo_tambien_se_resuelve_mirando(self):
        # Si el POST tira error no sabemos si entro. No se reintenta la orden:
        # se mira el libro, que es quien tiene la respuesta.
        class PostCaido(SesionFalsa):
            def postear(self, programa, pares, referer_ident=None):
                super().postear(programa, pares, referer_ident)
                raise ErrorDePlataforma("502")

        s = PostCaido(demora_en_verse=2)
        v = Vigilante(config(), s, vivo=True, log=Log())
        v.tick(1000.0)
        self.assertIs(v.fase, Fase.CONFIRMANDO)
        for i in range(1, 8):
            v.tick(1000.0 + i * 0.5)
        self.assertIs(v.fase, Fase.MIRANDO, "entro igual; el libro lo dice")
        self.assertEqual(len(s.posts), 1)

    def test_sigue_peleando_despues_de_confirmar(self):
        # Lo otro que se pedia: que no se pare sola tras una sola jugada.
        s = SesionFalsa()
        v = Vigilante(config(), s, vivo=True, log=Log())
        ahora = 1000.0
        for _ in range(60):
            v.tick(ahora)
            if v.fase is Fase.MIRANDO and len(s.posts) >= 2:
                break
            # Un rival que se mete abajo de cada oferta del bot.
            if v.fase is Fase.MIRANDO:
                ultima = Decimal(s.posts[-1]["tasa"].replace(",", "."))
                s.paginas[IDENT] = pantalla([
                    mia(s.posts[-1]["tasa"]),
                    ajena(f"{ultima - Decimal('0.01'):f}".replace(".", ","), id=9),
                ])
            ahora += 0.5
        self.assertGreaterEqual(len(s.posts), 2)
        self.assertEqual(s.posts[-1]["tasa"], "26,96")
        self.assertFalse(v.terminado)


class TestRadar(unittest.TestCase):
    """El tablero ahorra lecturas, pero nunca decide una oferta.

    La subasta de prueba tiene la mia en 27,00 y una ajena en 26,99: la mejor
    punta es 26,99, que es lo que el tablero tiene que estar diciendo.
    """

    def vigilante(self, ganando=True, **kw):
        """Por defecto, con la punta propia: ahi la decision es estable.

        Si la mia fuera la peor, el bot cotizaria — y despues de tocar el libro
        nunca se saltea una lectura, asi que no habria atajo que probar.
        """
        filas = [mia("26,98"), ajena("26,99")] if ganando \
            else [mia("27,00"), ajena("26,99")]
        punta = "26,98" if ganando else "26,99"
        s = SesionFalsa(paginas={IDENT: pantalla(filas)}, tasa_cpr=punta)
        v = Vigilante(config(**kw), s, vivo=False, log=Log())
        return s, v

    def _radar(self, v, s, ahora_s):
        v.recibir_ficha(s._fila(v.cfg.ident), ahora_s)

    def test_el_atajo_no_se_usa_antes_de_validarlo(self):
        # Primero hay que ver una vez que el tablero coincide con el libro.
        # Usarlo antes es creerle a algo que nunca se contrastó.
        s, v = self.vigilante()
        self._radar(v, s, 1000.0)
        v.tick(1000.0)
        self.assertFalse(v.radar_probado, "todavía no hay con qué comparar")
        self._radar(v, s, 1002.0)
        v.tick(1002.0)
        self.assertTrue(v.radar_probado)
        self.assertEqual(s.lecturas, 2)

    def test_no_relee_si_la_punta_no_se_movio(self):
        s, v = self.vigilante()
        for i in range(2):              # las dos que cuesta validarlo
            self._radar(v, s, 1000.0 + i * 2)
            v.tick(1000.0 + i * 2)
        self.assertEqual(s.lecturas, 2)
        self._radar(v, s, 1004.0)
        v.tick(1004.0)
        self.assertEqual(s.lecturas, 2, "el tablero dice que nada cambio")

    def test_relee_apenas_se_mueve_la_punta(self):
        s, v = self.vigilante()
        self._radar(v, s, 1000.0)
        v.tick(1000.0)
        # Alguien se metio abajo: el libro y el tablero se mueven juntos.
        s.paginas[IDENT] = pantalla([mia("26,98"), ajena("26,97", id=3)])
        s.tasa_cpr = "26,97"
        self._radar(v, s, 1002.0)
        v.tick(1002.0)
        self.assertEqual(s.lecturas, 2)

    def test_relee_igual_cada_tanto(self):
        # Un tablero congelado no puede dejar ciego al bot.
        from motor.vigilante import RELECTURA_S
        s, v = self.vigilante()
        self._radar(v, s, 1000.0)
        v.tick(1000.0)
        self._radar(v, s, 1000.0 + RELECTURA_S + 1)
        v.tick(1000.0 + RELECTURA_S + 1)
        self.assertEqual(s.lecturas, 2)

    def test_si_el_tablero_miente_se_apaga(self):
        s, v = self.vigilante()
        s.tasa_cpr = "11,11"            # no es la punta del libro
        self._radar(v, s, 1000.0)
        v.tick(1000.0)                  # primera lectura: no hay con qué comparar
        self._radar(v, s, 1002.0)
        v.tick(1002.0)                  # el libro no se movió; ahora sí vale
        self.assertFalse(v.radar_confiable)
        self._radar(v, s, 1004.0)
        v.tick(1004.0)
        self.assertEqual(s.lecturas, 3, "sin radar se relee siempre")

    def test_el_tablero_atrasado_no_cuenta_como_mentira(self):
        """El tablero es una foto de hace un par de segundos.

        En plena guerra difiere del libro todo el tiempo, y no porque mienta:
        el libro se movió en el medio. Contrastarlo igual apagaba el atajo en
        la primera recotización, justo donde tiene que servir.
        """
        s, v = self.vigilante()
        self._radar(v, s, 1000.0)
        v.tick(1000.0)
        # Alguien mejora. El bot lee el libro nuevo, pero el tablero que tiene
        # en la mano todavía es el de antes.
        s.paginas[IDENT] = pantalla([mia("26,98"), ajena("26,97", id=3)])
        v.tick(1002.0)                  # sin refrescar la ficha a propósito
        self.assertTrue(v.radar_confiable)

    def test_sin_punta_en_el_tablero_no_hay_atajo(self):
        s, v = self.vigilante()
        s.tasa_cpr = None
        self._radar(v, s, 1000.0)
        v.tick(1000.0)
        self._radar(v, s, 1002.0)
        v.tick(1002.0)
        self.assertEqual(s.lecturas, 2)

    def test_aguantar_una_vuelta_no_se_vuelve_permanente(self):
        # "Aguanto esta vuelta" sale de un dado, no del libro: si el atajo lo
        # congelara, el bot se quedaria callado con un rival abajo.
        s, v = self.vigilante(ganando=False, prob_respuesta=0.5)
        v.azar.seed(0)
        for i in range(6):
            ahora = 1000.0 + i * 2
            self._radar(v, s, ahora)
            v.tick(ahora)
        self.assertEqual(s.lecturas, 6, "tiene que volver a tirar el dado")

    def test_el_estado_del_tablero_cierra_la_subasta(self):
        s, v = self.vigilante()
        s.estado = "Negociada"
        self._radar(v, s, 1000.0)
        self.assertIs(v.fase, Fase.CERRADA)
        self.assertEqual(s.lecturas, 0, "ni siquiera hizo falta abrirla")


if __name__ == "__main__":
    unittest.main()
