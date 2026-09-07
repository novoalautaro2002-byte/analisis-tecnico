"""Tests del nucleo: montos, fechas, cuotas e idempotencia.

El parseo de montos es el punto donde un error no se nota y corrompe todo:
un separador mal interpretado convierte 1.234,56 en 123456. Por eso es lo
que mas casos tiene.
"""

from datetime import date
from decimal import Decimal

import pytest

from finanzas.modelo import (
    Moneda,
    Movimiento,
    extraer_cuota,
    normalizar_texto,
    parse_fecha,
    parse_monto,
)


class TestParseMonto:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("1.234,56", "1234.56"),      # formato argentino
            ("1,234.56", "1234.56"),      # formato US, aparece en tramos USD
            ("1234,56", "1234.56"),
            ("123,45", "123.45"),
            ("0,00", "0"),
            ("1.234.567,89", "1234567.89"),
            ("$ 1.234,56", "1234.56"),
            ("1.234", "1234"),            # sin decimales: son miles
            ("12345", "12345"),
        ],
    )
    def test_formatos(self, texto, esperado):
        assert parse_monto(texto) == Decimal(esperado)

    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("-123,45", "-123.45"),
            ("123,45-", "-123.45"),       # signo al final, comun en extractos
            ("(123,45)", "-123.45"),      # parentesis = negativo
            ("-1.234,56", "-1234.56"),
        ],
    )
    def test_negativos(self, texto, esperado):
        assert parse_monto(texto) == Decimal(esperado)

    def test_precision_exacta(self):
        # El motivo de usar Decimal: con float esto no da cero.
        total = sum((parse_monto("0,10") for _ in range(10)), Decimal(0))
        assert total == Decimal("1.00")

    @pytest.mark.parametrize("basura", ["", "   ", "abc", None])
    def test_rechaza_basura(self, basura):
        with pytest.raises(ValueError):
            parse_monto(basura)


class TestParseFecha:
    @pytest.mark.parametrize(
        "texto,esperado",
        [
            ("05/03/25", date(2025, 3, 5)),
            ("05/03/2025", date(2025, 3, 5)),
            ("05-03-2025", date(2025, 3, 5)),
            ("05-MAR-25", date(2025, 3, 5)),
            ("05-SET-25", date(2025, 9, 5)),   # 'SET' se usa en Argentina
        ],
    )
    def test_formatos(self, texto, esperado):
        assert parse_fecha(texto) == esperado

    def test_dia_mes_con_anio_por_defecto(self):
        # Los detalles de consumo traen solo dia y mes; el anio va en el encabezado.
        assert parse_fecha("05 MAR", anio_defecto=2025) == date(2025, 3, 5)

    def test_sin_anio_y_sin_defecto_falla(self):
        with pytest.raises(ValueError):
            parse_fecha("05 MAR")

    def test_siempre_dia_primero(self):
        # 03/05 es 3 de mayo, no 5 de marzo. Nunca interpretacion US.
        assert parse_fecha("03/05/25") == date(2025, 5, 3)


class TestCuotas:
    @pytest.mark.parametrize(
        "descripcion,esperado",
        [
            ("MERCADOLIBRE CUOTA 03/06", (3, 6)),
            ("FRAVEGA C.02/12", (2, 12)),
            ("GARBARINO CUOT 1/3", (1, 3)),
            ("COMPRA 05/06", (5, 6)),
        ],
    )
    def test_detecta(self, descripcion, esperado):
        assert extraer_cuota(descripcion) == esperado

    @pytest.mark.parametrize(
        "descripcion",
        [
            "SUPERMERCADO COTO",
            "COMPRA 1/1",          # una sola cuota no es un plan
            "PAGO 13/12",          # actual > total: no es una cuota
        ],
    )
    def test_no_falsos_positivos(self, descripcion):
        assert extraer_cuota(descripcion) is None

    def test_las_cuotas_de_una_compra_comparten_grupo(self):
        base = dict(cuenta="visa_1234", monto=Decimal("-5000"), moneda=Moneda.ARS)
        c3 = Movimiento(fecha=date(2025, 3, 5), descripcion="MELI CUOTA 03/06", **base)
        c4 = Movimiento(fecha=date(2025, 4, 5), descripcion="MELI CUOTA 04/06", **base)
        assert c3.grupo_cuotas == c4.grupo_cuotas
        assert c3.id != c4.id  # pero son movimientos distintos

    def test_compras_distintas_no_comparten_grupo(self):
        base = dict(cuenta="visa_1234", monto=Decimal("-5000"), moneda=Moneda.ARS)
        a = Movimiento(fecha=date(2025, 3, 5), descripcion="MELI CUOTA 03/06", **base)
        b = Movimiento(fecha=date(2025, 3, 5), descripcion="FRAVEGA CUOTA 03/06", **base)
        assert a.grupo_cuotas != b.grupo_cuotas


class TestIdentidad:
    def _mov(self, **kw):
        base = dict(
            fecha=date(2025, 3, 5), cuenta="galicia_ca_ars_123456",
            descripcion="SUPERMERCADO COTO SA", monto=Decimal("-12345.67"),
        )
        return Movimiento(**{**base, **kw})

    def test_el_mismo_movimiento_da_el_mismo_id(self):
        # Es lo que hace idempotente reingerir el mismo PDF.
        assert self._mov().id == self._mov().id

    def test_no_depende_del_archivo_de_origen(self):
        # El mismo movimiento visto en dos resumenes con periodos solapados
        # tiene que colapsar en uno solo.
        a = self._mov(fuente="pdf:marzo.pdf#p1")
        b = self._mov(fuente="pdf:abril.pdf#p2")
        assert a.id == b.id

    def test_ignora_diferencias_de_formato_en_la_descripcion(self):
        a = self._mov(descripcion="SUPERMERCADO COTO SA")
        b = self._mov(descripcion="supermercado  cotó,  s.a.")
        assert a.id == b.id

    @pytest.mark.parametrize(
        "campo,valor",
        [
            ("monto", Decimal("-12345.68")),
            ("fecha", date(2025, 3, 6)),
            ("cuenta", "otra_cuenta"),
            ("descripcion", "OTRO COMERCIO"),
        ],
    )
    def test_cualquier_campo_significativo_cambia_el_id(self, campo, valor):
        assert self._mov().id != self._mov(**{campo: valor}).id

    def test_centavos_exactos(self):
        assert self._mov(monto=Decimal("-12345.67")).centavos == -1234567


class TestNormalizarTexto:
    def test_saca_acentos_y_puntuacion(self):
        assert normalizar_texto("Almacén  José, S.A.") == "ALMACEN JOSE S A"

    def test_es_idempotente(self):
        una = normalizar_texto("Almacén  José, S.A.")
        assert normalizar_texto(una) == una
