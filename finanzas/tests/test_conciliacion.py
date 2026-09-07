"""Tests de conciliacion, cuotas e idempotencia contra la base."""

from datetime import date
from decimal import Decimal

import pytest

from finanzas import db, normalizar
from finanzas.modelo import Moneda, Movimiento


@pytest.fixture
def con(tmp_path):
    c = db.conectar(tmp_path / "test.db")
    yield c
    c.close()


def mov(fecha, monto, desc="COMPRA", cuenta="cta", moneda=Moneda.ARS):
    return Movimiento(
        fecha=date.fromisoformat(fecha), cuenta=cuenta,
        descripcion=desc, monto=Decimal(monto), moneda=moneda,
    )


class TestIdempotencia:
    def test_guardar_dos_veces_no_duplica(self, con):
        movs = [mov("2025-03-05", "-100"), mov("2025-03-06", "-200")]
        assert db.guardar_movimientos(con, movs) == (2, 0)
        assert db.guardar_movimientos(con, movs) == (0, 2)
        assert len(db.leer_movimientos(con)) == 2

    def test_montos_exactos_al_leer(self, con):
        db.guardar_movimientos(con, [mov("2025-03-05", "-12345.67")])
        assert db.leer_movimientos(con)[0].monto == Decimal("-12345.67")


class TestConciliacion:
    def test_cierra_cuando_los_flujos_explican_el_saldo(self, con):
        db.guardar_saldo(con, "cta", date(2025, 2, 28), Decimal("1000"))
        db.guardar_movimientos(con, [
            mov("2025-03-05", "-300"),
            mov("2025-03-10", "-200"),
            mov("2025-03-20", "500"),
        ])
        db.guardar_saldo(con, "cta", date(2025, 3, 31), Decimal("1000"))

        c = normalizar.conciliar(con)[0]
        assert c.cuadra
        assert c.diferencia == Decimal("0")

    def test_la_diferencia_es_lo_que_falta_capturar(self, con):
        db.guardar_saldo(con, "cta", date(2025, 2, 28), Decimal("1000"))
        db.guardar_movimientos(con, [mov("2025-03-05", "-300")])
        # El saldo real bajo 500, pero solo capturamos 300: faltan 200.
        db.guardar_saldo(con, "cta", date(2025, 3, 31), Decimal("500"))

        c = normalizar.conciliar(con)[0]
        assert not c.cuadra
        assert c.diferencia == Decimal("-200")

    def test_cada_moneda_se_concilia_por_separado(self, con):
        # Sumar pesos y dolares en un mismo total no significa nada. Si se
        # mezclan, el movimiento en USD contamina la conciliacion en ARS.
        db.guardar_saldo(con, "cta", date(2025, 2, 28), Decimal("0"), "ARS")
        db.guardar_saldo(con, "cta", date(2025, 2, 28), Decimal("0"), "USD")
        db.guardar_movimientos(con, [
            mov("2025-03-05", "-1000", moneda=Moneda.ARS),
            mov("2025-03-06", "-50", desc="NETFLIX", moneda=Moneda.USD),
        ])
        db.guardar_saldo(con, "cta", date(2025, 3, 31), Decimal("-1000"), "ARS")
        db.guardar_saldo(con, "cta", date(2025, 3, 31), Decimal("-50"), "USD")

        conciliaciones = normalizar.conciliar(con)
        assert len(conciliaciones) == 2
        assert all(c.cuadra for c in conciliaciones), [
            (c.cuenta, c.moneda, c.diferencia) for c in conciliaciones
        ]

    def test_una_sola_ancla_no_produce_conciliacion(self, con):
        # Sin un punto de partida previo no hay nada contra que comparar.
        db.guardar_saldo(con, "cta", date(2025, 3, 31), Decimal("500"))
        assert normalizar.conciliar(con) == []


class TestCompromisos:
    def test_cuenta_las_cuotas_que_faltan(self, con):
        db.guardar_movimientos(con, [mov("2025-03-09", "-5000", "MELI CUOTA 03/06")])
        c = normalizar.compromisos_pendientes(con)[0]
        assert c.cuotas_restantes == 3
        assert c.total_pendiente == Decimal("15000")

    def test_la_ultima_cuota_no_deja_pendiente(self, con):
        db.guardar_movimientos(con, [mov("2025-03-09", "-5000", "MELI CUOTA 06/06")])
        assert normalizar.compromisos_pendientes(con) == []

    def test_una_compra_sin_cuotas_no_es_compromiso(self, con):
        db.guardar_movimientos(con, [mov("2025-03-09", "-5000", "SUPERMERCADO COTO")])
        assert normalizar.compromisos_pendientes(con) == []


class TestCategorias:
    def test_aplica_reglas(self, con):
        normalizar.sembrar_reglas(con)
        db.guardar_movimientos(con, [
            mov("2025-03-05", "-100", "SUPERMERCADO COTO SA"),
            mov("2025-03-06", "-200", "YPF ESTACION"),
        ])
        conteo = normalizar.categorizar(con)
        assert conteo["supermercado"] == 1
        assert conteo["combustible"] == 1

    def test_una_regla_propia_le_gana_a_las_iniciales(self, con):
        normalizar.sembrar_reglas(con)
        normalizar.agregar_regla(con, r"\bCOTO\b", "mi categoria", prioridad=1)
        db.guardar_movimientos(con, [mov("2025-03-05", "-100", "SUPERMERCADO COTO SA")])
        normalizar.categorizar(con)
        assert db.leer_movimientos(con)[0].categoria == "mi categoria"
