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


class TestNoComputables:
    """Impuestos y pago de tarjeta quedan fuera de todo total de gastos.

    El usuario paga los consumos en dolares con dolares, y el banco le
    devuelve la percepcion del 30%: contarla seria inflar el gasto con plata
    que vuelve. El pago del resumen tampoco es un ingreso, es la liquidacion
    de consumos ya contados uno por uno.
    """

    def test_se_guardan_igual(self, con):
        # Siguen en la base: el total impreso del resumen los incluye y sin
        # ellos la verificacion contra ese total no cerraria.
        from finanzas.modelo import Tipo
        m = mov("2025-03-25", "-1234", "IVA RG 4240")
        m.tipo = Tipo.IMPUESTO
        db.guardar_movimientos(con, [m])
        assert len(db.leer_movimientos(con)) == 1

    def test_no_suman_en_gastos_ni_ingresos(self, con):
        from finanzas import panel
        from finanzas.modelo import Tipo

        compra = mov("2025-03-05", "-1000", "SUPERMERCADO")
        impuesto = mov("2025-03-25", "-199075.86", "DB.RG 5617 30%")
        impuesto.tipo = Tipo.IMPUESTO
        pago = mov("2025-03-22", "115849.76", "SU PAGO EN PESOS")
        pago.tipo = Tipo.PAGO_TARJETA
        db.guardar_movimientos(con, [compra, impuesto, pago])

        d = panel.construir_datos(con)
        mes = [m for m in d["por_mes"] if m["mes"] == "2025-03"][0]
        assert mes["egresos"] == 1000.0    # sin el impuesto
        assert mes["ingresos"] == 0.0      # el pago no es ingreso

    def test_quedan_visibles_aparte(self, con):
        from finanzas import panel
        from finanzas.modelo import Tipo

        impuesto = mov("2025-03-25", "-1234", "IVA RG 4240")
        impuesto.tipo = Tipo.IMPUESTO
        db.guardar_movimientos(con, [impuesto])

        d = panel.construir_datos(con)
        assert [e["tipo"] for e in d["excluido"]] == ["impuesto"]
        assert d["excluido"][0]["total"] == -1234.0


class TestClasificacion:
    def test_el_pago_del_resumen_no_es_credito(self, con):
        from finanzas.parsers.tarjeta_generica import _inferir_tipo
        from finanzas.modelo import Tipo, normalizar_texto
        assert _inferir_tipo(normalizar_texto("SU PAGO EN PESOS"), True) == Tipo.PAGO_TARJETA

    def test_percepciones_de_galicia_son_impuesto(self, con):
        from finanzas.parsers.tarjeta_generica import _inferir_tipo
        from finanzas.modelo import Tipo, normalizar_texto
        for desc in ("DB.RG 5617 30%", "PERCEP.AFIP RG 4815 30%", "IIBB PERCEP-CABA",
                     "IVA RG 4240 21%", "DEV.IMP. RG 5617 30%", "DEV PER RG 4815 30%"):
            assert _inferir_tipo(normalizar_texto(desc), True) == Tipo.IMPUESTO, desc
