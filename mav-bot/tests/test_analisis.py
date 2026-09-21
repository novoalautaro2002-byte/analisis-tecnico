"""Tests de la reconstruccion de guerras de tasas."""

import unittest

from motor.analisis import SEGUNDOS_BOT, clasificar, fusionar, reacciones
from motor.libro import parsear_libro
from tests.test_libro import armar_html

IDENT = 900


def lib(filas, ident=IDENT):
    return parsear_libro(armar_html(ident, filas))


def of(id, ag, tasa, hora, propia=False):
    return {"id": id, "ag": ag, "tasa": tasa, "hora": hora, "propia": propia}


class TestFusionar(unittest.TestCase):
    def test_une_sin_repetir(self):
        a = lib([of(1, "442", "27,00", "10:00:00")])
        b = lib([of(1, "442", "27,00", "10:00:00"), of(2, "999", "26,99", "10:01:00")])
        h = fusionar([a, b])
        self.assertEqual([o.id for o in h.ofertas], [1, 2])

    def test_detecta_ofertas_dadas_de_baja(self):
        # La 1 estaba y en la captura final no esta: la bajaron.
        a = lib([of(1, "442", "27,00", "10:00:00")])
        b = lib([of(2, "999", "26,00", "10:05:00")])
        h = fusionar([a, b])
        self.assertEqual(h.retiradas, {1})
        self.assertEqual([o.id for o in h.vigentes()], [2])

    def test_conserva_la_marca_de_propia(self):
        # Al cerrar la subasta el link de baja desaparece; vale la captura
        # anterior, donde todavia estaba.
        viva = lib([of(1, "442", "27,00", "10:00:00", propia=True)])
        cerrada = lib([of(1, "442", "27,00", "10:00:00"),
                       of(2, "999", "26,00", "10:05:00")])
        h = fusionar([viva, cerrada])
        self.assertTrue(next(o for o in h.ofertas if o.id == 1).propia)

    def test_ordena_las_capturas_solo(self):
        tarde = lib([of(1, "442", "27,00", "10:00:00"), of(2, "999", "26,00", "11:00:00")])
        temprano = lib([of(1, "442", "27,00", "10:00:00")])
        h = fusionar([tarde, temprano])   # llegan al reves a proposito
        self.assertEqual(h.retiradas, frozenset())

    def test_rechaza_capturas_de_subastas_distintas(self):
        with self.assertRaises(ValueError):
            fusionar([lib([], ident=900), lib([], ident=901)])

    def test_rechaza_lista_vacia(self):
        with self.assertRaises(ValueError):
            fusionar([])


class TestReacciones(unittest.TestCase):
    def test_mide_demora_y_recorte(self):
        h = fusionar([lib([
            of(1, "999", "27,00", "10:00:00"),
            of(2, "442", "26,99", "10:00:35"),
        ])])
        rs = reacciones(h, "442")
        self.assertEqual(len(rs), 1)
        self.assertAlmostEqual(rs[0].demora_s, 35.0)
        self.assertEqual(str(rs[0].recorte), "0.01")

    def test_ignora_ofertas_que_no_mejoran(self):
        # Una oferta mas alta que la punta ajena no es una contestacion. Sin
        # este filtro salian "reacciones" de miles de segundos y recorte
        # negativo entre ofertas que ni convivieron.
        h = fusionar([lib([
            of(1, "442", "10,00", "10:51:52"),
            of(2, "406", "25,45", "11:40:55"),
        ])])
        self.assertEqual(reacciones(h, "406"), [])

    def test_no_cuenta_la_primera_oferta(self):
        h = fusionar([lib([of(1, "442", "27,00", "10:00:00")])])
        self.assertEqual(reacciones(h, "442"), [])


class TestClasificar(unittest.TestCase):
    def test_pocas_muestras_no_alcanzan(self):
        veredicto, _ = clasificar([3.0])
        self.assertEqual(veredicto, "desconocido")

    def test_rapido_y_regular_es_bot(self):
        veredicto, _ = clasificar([2.0, 3.0, 2.5, 3.0])
        self.assertEqual(veredicto, "bot")

    def test_lento_es_humano(self):
        veredicto, _ = clasificar([90.0, 120.0, 65.0])
        self.assertEqual(veredicto, "humano")

    def test_rapido_pero_irregular_es_humano(self):
        # Alguien pegado a la pantalla contesta rapido a veces, pero no siempre.
        veredicto, _ = clasificar([1.0, 40.0, 3.0, 55.0])
        self.assertEqual(veredicto, "humano")

    def test_el_umbral_de_bot_es_el_declarado(self):
        veredicto, _ = clasificar([SEGUNDOS_BOT] * 4)
        self.assertEqual(veredicto, "bot")


class TestRecotizacionesEnElMismoId(unittest.TestCase):
    """El libro solo guarda ofertas vivas: recotizar reescribe la oferta.

    Si se deduplica por id, una guerra de cincuenta pasos queda reducida a un
    solo movimiento.
    """

    def test_conserva_cada_paso_de_la_guerra(self):
        capturas = [
            lib([of(9, "442", "26,00", "11:00:00", propia=True)]),
            lib([of(9, "442", "25,98", "11:00:40", propia=True)]),
            lib([of(9, "442", "25,96", "11:01:20", propia=True)]),
        ]
        h = fusionar(capturas)
        self.assertEqual([str(o.tasa) for o in h.ofertas], ["26.00", "25.98", "25.96"])
        self.assertEqual(h.retiradas, frozenset())

    def test_mide_las_respuestas_de_una_guerra_larga(self):
        capturas = [
            lib([of(1, "406", "26,00", "11:00:00"),
                 of(2, "442", "25,99", "11:00:30", propia=True)]),
            lib([of(1, "406", "25,98", "11:01:00"),
                 of(2, "442", "25,97", "11:01:30", propia=True)]),
        ]
        rs = reacciones(fusionar(capturas), "442")
        self.assertEqual(len(rs), 2)
        self.assertEqual([r.demora_s for r in rs], [30.0, 30.0])


if __name__ == "__main__":
    unittest.main()
