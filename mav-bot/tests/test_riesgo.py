"""Tests del gate de riesgo."""

import unittest
from decimal import Decimal

from motor.config import ConfigInvalida, ConfigSubasta
from motor.decision import Accion, Decision
from motor.libro import Oferta, parsear_libro
from motor.riesgo import EstadoSesion, evaluar, verificar_despues
from tests.test_libro import armar_html

IDENT = 900
from datetime import time as _t


def config(**kw):
    base = dict(ident=IDENT, mi_agente="442", piso=Decimal("25.00"),
                intervalo_min_s=10.0)
    base.update(kw)
    return ConfigSubasta(**base)


def propia(tasa, hora=_t(10, 0, 0)):
    return Oferta(id=1, agente="442", tasa=Decimal(tasa), ingreso=hora, propia=True)


def recotizar(tasa, mia_tasa="27.00"):
    return Decision(Accion.RECOTIZAR, "test", tasa=Decimal(tasa), mia=propia(mia_tasa))


class TestHabilitacion(unittest.TestCase):
    def test_caso_normal(self):
        v = evaluar(recotizar("26.98"), config(), EstadoSesion(), 100.0, 1.0)
        self.assertTrue(v)

    def test_solo_habilita_recotizar(self):
        for accion in (Accion.NADA, Accion.CEDER, Accion.SIN_OFERTA):
            v = evaluar(Decision(accion, "test"), config(), EstadoSesion(), 100.0, 1.0)
            self.assertFalse(v)


class TestCinturones(unittest.TestCase):
    def test_kill_switch(self):
        estado = EstadoSesion()
        estado.detener()
        v = evaluar(recotizar("26.98"), config(), estado, 100.0, 1.0)
        self.assertFalse(v)
        self.assertIn("kill", v.motivo)

    def test_tasa_absurda_por_error_de_parseo(self):
        # "26,99" leido como 2699 termina en una oferta de 2698,99. El gate lo
        # corta aunque el piso y la monotonia den bien.
        v = evaluar(recotizar("2698.99", mia_tasa="2700.00"), config(),
                    EstadoSesion(), 100.0, 1.0)
        self.assertFalse(v)
        self.assertIn("banda plausible", v.motivo)

    def test_por_debajo_del_piso(self):
        v = evaluar(recotizar("24.99"), config(piso=Decimal("25.00")),
                    EstadoSesion(), 100.0, 1.0)
        self.assertFalse(v)
        self.assertIn("piso", v.motivo)

    def test_la_tasa_no_puede_subir(self):
        v = evaluar(recotizar("27.50", mia_tasa="27.00"), config(),
                    EstadoSesion(), 100.0, 1.0)
        self.assertFalse(v)
        self.assertIn("no mejora", v.motivo)

    def test_tampoco_puede_quedarse_igual(self):
        v = evaluar(recotizar("27.00", mia_tasa="27.00"), config(),
                    EstadoSesion(), 100.0, 1.0)
        self.assertFalse(v)

    def test_libro_viejo(self):
        v = evaluar(recotizar("26.98"), config(antiguedad_max_libro_s=30.0),
                    EstadoSesion(), 100.0, antiguedad_libro_s=45.0)
        self.assertFalse(v)
        self.assertIn("libro", v.motivo)

    def test_tope_de_recotizaciones(self):
        cfg = config(max_recotizaciones=3, intervalo_min_s=0.0)
        estado = EstadoSesion()
        for i in range(3):
            self.assertTrue(evaluar(recotizar("26.98"), cfg, estado, 100.0 + i, 1.0))
            estado.registrar(cfg.ident, 100.0 + i)
        v = evaluar(recotizar("26.98"), cfg, estado, 200.0, 1.0)
        self.assertFalse(v)
        self.assertIn("tope", v.motivo)

    def test_intervalo_minimo(self):
        cfg = config(intervalo_min_s=10.0)
        estado = EstadoSesion()
        estado.registrar(cfg.ident, 100.0)
        self.assertFalse(evaluar(recotizar("26.98"), cfg, estado, 105.0, 1.0))
        self.assertTrue(evaluar(recotizar("26.98"), cfg, estado, 111.0, 1.0))

    def test_el_tope_es_por_subasta(self):
        cfg_a = config(ident=900, max_recotizaciones=1, intervalo_min_s=0.0)
        cfg_b = config(ident=901, max_recotizaciones=1, intervalo_min_s=0.0)
        estado = EstadoSesion()
        estado.registrar(900, 100.0)
        self.assertFalse(evaluar(recotizar("26.98"), cfg_a, estado, 200.0, 1.0))
        self.assertTrue(evaluar(recotizar("26.98"), cfg_b, estado, 200.0, 1.0))


class TestVerificacionPosterior(unittest.TestCase):
    """Reemplaza al preview que esta plataforma no tiene."""

    def _libro(self, filas, ident=IDENT):
        return parsear_libro(armar_html(ident, filas))

    def test_confirma_la_tasa_esperada(self):
        libro = self._libro([
            {"id": 1, "ag": "442", "tasa": "26,98", "hora": "10:00:00", "propia": True},
        ])
        self.assertTrue(verificar_despues(libro, config(), Decimal("26.98")))

    def test_detecta_que_entro_otra_cosa(self):
        libro = self._libro([
            {"id": 1, "ag": "442", "tasa": "26,50", "hora": "10:00:00", "propia": True},
        ])
        v = verificar_despues(libro, config(), Decimal("26.98"))
        self.assertFalse(v)
        self.assertIn("esperaba", v.motivo)

    def test_detecta_que_la_oferta_desaparecio(self):
        libro = self._libro([
            {"id": 2, "ag": "999", "tasa": "26,00", "hora": "10:00:00", "propia": False},
        ])
        self.assertFalse(verificar_despues(libro, config(), Decimal("26.98")))

    def test_detecta_subasta_equivocada(self):
        libro = self._libro([
            {"id": 1, "ag": "442", "tasa": "26,98", "hora": "10:00:00", "propia": True},
        ], ident=999)
        self.assertFalse(verificar_despues(libro, config(), Decimal("26.98")))


class TestConfig(unittest.TestCase):
    def test_rechaza_piso_absurdo(self):
        with self.assertRaises(ConfigInvalida):
            config(piso=Decimal("2500"))

    def test_rechaza_decremento_menor_al_centavo(self):
        with self.assertRaises(ConfigInvalida):
            config(decremento_min=Decimal("0.001"))

    def test_rechaza_rango_de_decremento_dado_vuelta(self):
        with self.assertRaises(ConfigInvalida):
            config(decremento_min=Decimal("0.05"), decremento_max=Decimal("0.01"))

    def test_rechaza_probabilidad_cero(self):
        # Un bot que nunca contesta no es un bot apagado, es un bug silencioso.
        with self.assertRaises(ConfigInvalida):
            config(prob_respuesta=0.0)


if __name__ == "__main__":
    unittest.main()
