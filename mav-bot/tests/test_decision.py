"""Tests del motor de decision."""

import random
import unittest
from decimal import Decimal

from motor.config import ConfigSubasta
from motor.decision import Accion, SubastaEquivocada, decidir
from motor.libro import parsear_libro
from tests.test_libro import armar_html

IDENT = 900


def libro(*filas):
    return parsear_libro(armar_html(IDENT, list(filas)))


def mia(tasa, hora="10:00:00", id=1):
    return {"id": id, "ag": "442", "tasa": tasa, "hora": hora, "propia": True}


def ajena(tasa, hora="10:00:00", id=2, ag="999"):
    return {"id": id, "ag": ag, "tasa": tasa, "hora": hora, "propia": False}


def config(**kw):
    base = dict(ident=IDENT, piso=Decimal("25.00"))
    base.update(kw)
    return ConfigSubasta(**base)


class TestSinAccion(unittest.TestCase):
    def test_sin_oferta_propia_no_hace_nada(self):
        # La whitelist es fisica: sin una oferta cargada a mano, no hay nada
        # que el bot pueda modificar.
        d = decidir(libro(ajena("26,00")), config(), random.Random(0))
        self.assertIs(d.accion, Accion.SIN_OFERTA)

    def test_solo_en_el_libro(self):
        d = decidir(libro(mia("27,00")), config(), random.Random(0))
        self.assertIs(d.accion, Accion.NADA)

    def test_ya_voy_ganando(self):
        d = decidir(libro(mia("26,00"), ajena("26,50")), config(), random.Random(0))
        self.assertIs(d.accion, Accion.NADA)


class TestEmpate(unittest.TestCase):
    def test_empate_con_prioridad_no_gasta_un_centavo(self):
        # Si entre antes, la posicion ya es mia: bajar seria regalar plata.
        d = decidir(
            libro(mia("26,00", "10:00:00"), ajena("26,00", "10:05:00")),
            config(), random.Random(0),
        )
        self.assertIs(d.accion, Accion.NADA)
        self.assertIn("entre antes", d.motivo)

    def test_empate_sin_prioridad_obliga_a_bajar(self):
        d = decidir(
            libro(mia("26,00", "10:05:00"), ajena("26,00", "10:00:00")),
            config(), random.Random(0),
        )
        self.assertIs(d.accion, Accion.RECOTIZAR)
        self.assertEqual(d.tasa, Decimal("25.99"))


class TestRecotizar(unittest.TestCase):
    def test_baja_un_centavo_abajo_del_rival(self):
        d = decidir(libro(mia("27,00"), ajena("26,99")), config(), random.Random(0))
        self.assertIs(d.accion, Accion.RECOTIZAR)
        self.assertEqual(d.tasa, Decimal("26.98"))

    def test_toma_la_mejor_ajena_no_la_primera(self):
        d = decidir(
            libro(mia("27,00"), ajena("26,90", id=2), ajena("26,50", id=3, ag="777")),
            config(), random.Random(0),
        )
        self.assertEqual(d.tasa, Decimal("26.49"))
        self.assertEqual(d.rival.agente, "777")

    def test_decremento_aleatorio_dentro_del_rango(self):
        cfg = config(decremento_min=Decimal("0.01"), decremento_max=Decimal("0.05"))
        vistos = {
            decidir(libro(mia("27,00"), ajena("26,00")), cfg, random.Random(s)).tasa
            for s in range(200)
        }
        self.assertGreater(len(vistos), 1, "el decremento tiene que variar")
        self.assertEqual(min(vistos), Decimal("25.95"))
        self.assertEqual(max(vistos), Decimal("25.99"))

    def test_la_tasa_siempre_baja(self):
        for semilla in range(100):
            d = decidir(
                libro(mia("27,00"), ajena("26,50")),
                config(decremento_min=Decimal("0.01"), decremento_max=Decimal("0.10")),
                random.Random(semilla),
            )
            self.assertLess(d.tasa, Decimal("27.00"))


class TestPiso(unittest.TestCase):
    def test_cede_si_ni_el_centavo_minimo_alcanza(self):
        d = decidir(
            libro(mia("25,50"), ajena("25,00")),
            config(piso=Decimal("25.00")), random.Random(0),
        )
        self.assertIs(d.accion, Accion.CEDER)

    def test_el_piso_recorta_el_decremento_en_vez_de_ceder(self):
        # Con rival en 26,00 y piso 25,99 todavia se puede ganar. Que el azar
        # haya elegido un decremento grande no puede costarnos el cheque.
        cfg = config(piso=Decimal("25.99"),
                     decremento_min=Decimal("0.05"), decremento_max=Decimal("0.05"))
        d = decidir(libro(mia("27,00"), ajena("26,00")), cfg, random.Random(0))
        self.assertIs(d.accion, Accion.RECOTIZAR)
        self.assertEqual(d.tasa, Decimal("25.99"))

    def test_nunca_perfora_el_piso(self):
        cfg = config(piso=Decimal("26.00"),
                     decremento_min=Decimal("0.01"), decremento_max=Decimal("0.50"))
        for semilla in range(200):
            d = decidir(libro(mia("27,00"), ajena("26,80")), cfg, random.Random(semilla))
            if d.accion is Accion.RECOTIZAR:
                self.assertGreaterEqual(d.tasa, cfg.piso)


class TestAguantar(unittest.TestCase):
    def test_a_veces_no_contesta(self):
        cfg = config(prob_respuesta=0.5)
        acciones = [
            decidir(libro(mia("27,00"), ajena("26,00")), cfg, random.Random(s)).accion
            for s in range(200)
        ]
        self.assertIn(Accion.NADA, acciones)
        self.assertIn(Accion.RECOTIZAR, acciones)

    def test_con_probabilidad_uno_siempre_contesta(self):
        cfg = config(prob_respuesta=1.0)
        for semilla in range(50):
            d = decidir(libro(mia("27,00"), ajena("26,00")), cfg, random.Random(semilla))
            self.assertIs(d.accion, Accion.RECOTIZAR)


class TestFallaCerrado(unittest.TestCase):
    def test_libro_de_otra_subasta(self):
        otro = parsear_libro(armar_html(999, [mia("27,00")]))
        with self.assertRaises(SubastaEquivocada):
            decidir(otro, config(), random.Random(0))


if __name__ == "__main__":
    unittest.main()
