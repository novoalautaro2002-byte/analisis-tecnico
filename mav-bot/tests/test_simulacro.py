"""Tests del entorno de prueba.

Un simulacro que miente no sirve para demostrar nada: si sus páginas dejan de
parecerse a las de MAV, la demo pasa y la plataforma real falla. Así que acá se
prueba lo mismo que el bot le pide a MAV — que el libro se lea, que la ficha se
entienda, y que el POST que arma el bot sea aceptado.
"""

import sys
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import simulacro  # noqa: E402

simulacro.RUIDO = False
from motor.ficha import leer_ficha  # noqa: E402
from motor.formulario import armar_oferta  # noqa: E402
from motor.libro import parsear_libro  # noqa: E402


def mercado(mirada=True):
    """Una subasta recién abierta, con la oferta propia a mano en 27,00."""
    s = simulacro.Subasta()
    if mirada:
        s.mirar()
    return s


class TestPaginas(unittest.TestCase):
    """Lo que sirve el simulacro tiene que pasar por el parser de verdad."""

    def test_el_libro_se_lee_con_el_parser_del_bot(self):
        s = mercado()
        libro = parsear_libro(simulacro.pagina_subasta(s), "442")
        self.assertEqual(libro.ident, s.ident)
        self.assertEqual(len(libro.ofertas), 1)
        self.assertEqual(libro.mejor_propia().tasa, Decimal("27.00"))

    def test_reconoce_al_ajeno_por_agente(self):
        s = mercado()
        s.cargar("406", Decimal("26.99"), "rival")
        s.ofertas[-1]["cargada_s"] = 0.0
        libro = parsear_libro(simulacro.pagina_subasta(s), "442")
        self.assertEqual(libro.mejor_ajena().agente, "406")

    def test_la_ficha_del_listado_se_entiende(self):
        s = mercado()
        f = leer_ficha(simulacro.fila_listado(s))
        self.assertEqual(f.ident, s.ident)
        self.assertTrue(f.viva)
        self.assertEqual(f.tasa_cpr, Decimal("27.00"))
        self.assertEqual(f.agente_cpr, "442")
        self.assertEqual(f.cantidad_cheques, len(simulacro.CHEQUES))

    def test_una_oferta_recien_cargada_todavia_no_se_ve(self):
        # Es el defecto que se simula a propósito: MAV la toma pero tarda en
        # mostrarla, y el bot se cortaba ahí.
        s = mercado()
        s.cargar("406", Decimal("26.99"), "rival")
        self.assertEqual(len(s.visibles()), 1)


class TestAlta(unittest.TestCase):
    """El POST que arma el bot, contra el servidor que imita a MAV."""

    def payload(self, s, tasa="26,98"):
        return dict(armar_oferta(simulacro.pagina_subasta(s),
                                 simulacro.pagina_cheques(), tasa).pares())

    def test_el_payload_del_bot_es_aceptado(self):
        s = mercado()
        simulacro.alta(s, self.payload(s))
        self.assertEqual(len(s.ofertas), 1)
        self.assertEqual(s.ofertas[0]["tasa"], Decimal("26.98"))

    def test_el_payload_lleva_el_comitente(self):
        # Esto es lo que faltaba cuando MAV contestaba "la orden de compra no
        # ha sido ingresada".
        s = mercado()
        campos = self.payload(s)
        for c in simulacro.CHEQUES:
            self.assertEqual(campos[f"comitcpr{c}"], "51414")

    def test_sin_comitente_lo_rechaza(self):
        s = mercado()
        campos = self.payload(s)
        campos[f"comitcpr{simulacro.CHEQUES[0]}"] = ""
        salida = simulacro.alta(s, campos)
        self.assertIn("no ha sido ingresada", salida)
        self.assertEqual(s.ofertas[0]["tasa"], Decimal("27.00"), "no cargó nada")

    def test_no_acepta_altas_en_una_subasta_cerrada(self):
        s = mercado()
        s.estado = "Negociada"
        self.assertIn("no se encuentra activa", simulacro.alta(s, self.payload(s)))


class TestCierreBlando(unittest.TestCase):
    def test_cada_mejora_reinicia_la_cuenta(self):
        s = mercado()
        s.cierra_s = 0.0                       # a punto de cerrar
        s.cargar("406", Decimal("26.99"), "rival")
        self.assertGreater(s.falta_s(), simulacro.CUENTA_S - 1)


class TestElRelojNoCorreSolo(unittest.TestCase):
    """La demo se arruinaba sola: la subasta cerraba durante el login.

    El reloj arrancaba con el servidor, así que los 45 segundos se gastaban
    mientras el trader se logueaba y llenaba el formulario. Cuando el bot iba a
    mirar, ya estaba negociada.
    """

    def test_sin_que_nadie_mire_no_hay_cuenta_regresiva(self):
        s = mercado(mirada=False)
        self.assertEqual(s.falta_s(), float("inf"))
        s.latir()
        self.assertEqual(s.estado, "Activa")

    def test_el_rival_tampoco_se_mueve_antes(self):
        s = mercado(mirada=False)
        r = simulacro.Rival(s)
        r.proximo_s = 0.0
        r.latir()
        self.assertEqual(len(s.ofertas), 1, "el mercado no se mueve para nadie")

    def test_la_cuenta_arranca_al_abrir_la_subasta(self):
        s = mercado(mirada=False)
        s.mirar()
        self.assertLessEqual(s.falta_s(), simulacro.CUENTA_S)
        self.assertGreater(s.falta_s(), simulacro.CUENTA_S - 1)

    def test_la_ficha_es_legible_aunque_no_haya_arrancado(self):
        # falta_s() es infinito ahí: si se usa tal cual, revienta la fecha.
        f = leer_ficha(simulacro.fila_listado(mercado(mirada=False)))
        self.assertTrue(f.hora_cierre)


class TestRuedaNueva(unittest.TestCase):
    """Cerrada una, se abre otra sola: probar de nuevo no obliga a reiniciar."""

    def test_reabre_despues_de_la_pausa(self):
        s = mercado()
        s.cierra_s = 0.0
        s.latir()
        self.assertEqual(s.estado, "Negociada")
        s.reabre_s = 0.0                       # como si ya hubiera pasado
        self.assertTrue(s.latir())
        self.assertEqual(s.estado, "Activa")
        self.assertEqual(s.ronda, 2)

    def test_la_rueda_nueva_trae_tu_oferta_a_mano(self):
        s = mercado()
        s.cierra_s = 0.0
        s.latir()
        s.reabre_s = 0.0
        s.latir()
        self.assertEqual(len(s.ofertas), 1)
        self.assertEqual(s.ofertas[0]["tasa"], Decimal("27.00"))
        self.assertEqual(s.ofertas[0]["agente"], "442")

    def test_la_rueda_nueva_tampoco_corre_sola(self):
        s = mercado()
        s.cierra_s = 0.0
        s.latir()
        s.reabre_s = 0.0
        s.latir()
        self.assertEqual(s.falta_s(), float("inf"))


class TestRival(unittest.TestCase):
    def test_baja_un_centavo_cuando_no_tiene_la_punta(self):
        s = mercado()
        r = simulacro.Rival(s, piso=Decimal("26.00"))
        r.proximo_s = 0.0
        r.latir()
        self.assertEqual(s.ofertas[-1]["tasa"], Decimal("26.99"))

    def test_se_planta_en_su_piso(self):
        s = mercado()
        r = simulacro.Rival(s, piso=Decimal("27.00"))
        r.proximo_s = 0.0
        r.latir()
        self.assertEqual(len(s.ofertas), 1, "no puede perforar su propio piso")

    def test_no_se_pisa_a_si_mismo(self):
        s = mercado()
        s.ofertas = []
        s.cargar("406", Decimal("26.50"), "rival")
        s.ofertas[0]["cargada_s"] = 0.0
        r = simulacro.Rival(s)
        r.proximo_s = 0.0
        r.latir()
        self.assertEqual(len(s.ofertas), 1)


if __name__ == "__main__":
    unittest.main()
