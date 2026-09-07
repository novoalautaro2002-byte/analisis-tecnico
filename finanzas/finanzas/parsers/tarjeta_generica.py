"""Parser generico de resumenes tabulares.

Es el ultimo recurso: no conoce ningun emisor, reconoce la forma que casi
todos los resumenes argentinos comparten, que es una linea con

    <fecha>  <descripcion>  <monto>

Sirve para tener algo funcionando el primer dia y para ver que sale de un
emisor nuevo antes de escribirle un parser propio. Un parser especifico
siempre le gana, porque entiende columnas, impuestos y totales, cosas que
este no puede saber.
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..modelo import Moneda, Movimiento, Tipo, normalizar_texto, parse_fecha, parse_monto
from .base import Parser, ResultadoParseo, registrar

# Un monto: digitos con separadores y exactamente dos decimales. El guion o
# el parentesis de cierre al final marcan negativo en muchos extractos.
_MONTO = r"[-(]?\$?\s*\d[\d.,]*[.,]\d{2}\s*\)?-?"
_RE_MONTO = re.compile(rf"(?<![\w.,]){_MONTO}(?![\d])")

# Una fecha al principio de la linea: 05/03/25, 05-03-2025, 05-MAR-25, 05 MAR.
_RE_FECHA_INICIO = re.compile(
    r"^\s*(\d{1,2}[/\-.]\d{1,2}(?:[/\-.]\d{2,4})?"
    r"|\d{1,2}[/\-. ][A-Za-z]{3}(?:[/\-. ]\d{2,4})?)\b"
)

_RE_ANIO = re.compile(r"\b(20\d{2})\b")

# Palabras que marcan que la linea es un total, un subtotal o un encabezado,
# no un movimiento. Sumarlas seria contar dos veces.
_NO_ES_MOVIMIENTO = (
    "TOTAL", "SUBTOTAL", "SALDO", "PAGO MINIMO", "LIMITE", "PROXIMO VENCIMIENTO",
    "VENCIMIENTO", "CIERRE", "PERIODO", "HOJA", "PAGINA", "CBU", "CUIT",
    "ESTIMADO", "SUMA DE", "CONSUMOS DEL PERIODO",
)

# Marcas de que el importe entra a favor tuyo dentro de un resumen de tarjeta.
_ES_CREDITO = (
    "SU PAGO", "PAGO RECIBIDO", "PAGO EN PESOS", "HABER", "CREDITO",
    "DEVOLUCION", "BONIFICACION", "REINTEGRO", "A FAVOR", "ANULACION",
)

_MARCAS_TARJETA = (
    "TARJETA", "VISA", "MASTERCARD", "AMERICAN EXPRESS", "AMEX", "CABAL",
    "RESUMEN DE CUENTA", "ESTADO DE CUENTA",
)

_MARCAS_USD = ("U$S", "USD", "DOLARES", "DOLAR", "EN DOLARES")


@registrar
class TarjetaGenerica(Parser):
    nombre = "generico-tabular"
    especificidad = 0

    def reconoce(self, doc) -> bool:
        """Acepta cualquier documento con suficientes lineas con forma de movimiento.

        El umbral evita que agarre una factura o una carta del banco que
        casualmente tenga una fecha y un numero.
        """
        if doc.esta_vacio:
            return False
        candidatas = sum(1 for ln in doc.lineas if _parece_movimiento(ln))
        return candidatas >= 3

    def parsear(self, doc) -> ResultadoParseo:
        res = ResultadoParseo()
        anio = _anio_del_documento(doc)
        texto_norm = normalizar_texto(doc.texto[:4000])
        es_tarjeta = any(m in texto_norm for m in _MARCAS_TARJETA)
        cuenta = _nombre_de_cuenta(doc)

        if es_tarjeta:
            res.advertencias.append(
                "Detectado como resumen de tarjeta: los consumos se guardan como "
                "egresos (negativos) y los pagos como ingresos. Verifica el signo "
                "contra el total impreso antes de confiar en los numeros."
            )

        for pagina in doc.paginas:
            for linea in pagina.lineas:
                if not _parece_movimiento(linea):
                    continue
                mov = self._linea_a_movimiento(linea, anio, cuenta, es_tarjeta, doc, pagina.numero)
                if mov is None:
                    res.sin_parsear.append(linea)
                else:
                    res.movimientos.append(mov)

        if not res.movimientos:
            res.advertencias.append(
                "No se reconocio ningun movimiento. Corre 'finanzas diagnostico' "
                "sobre este archivo para ver que texto extrae realmente."
            )
        return res

    def _linea_a_movimiento(
        self, linea: str, anio: int | None, cuenta: str, es_tarjeta: bool, doc, pagina: int
    ) -> Movimiento | None:
        m_fecha = _RE_FECHA_INICIO.match(linea)
        if not m_fecha:
            return None

        montos = list(_RE_MONTO.finditer(linea))
        if not montos:
            return None

        try:
            fecha = parse_fecha(m_fecha.group(1), anio_defecto=anio)
        except ValueError:
            return None

        # El importe es el ultimo numero de la linea. Cuando hay dos columnas
        # (pesos y dolares), la que tiene un valor distinto de cero manda.
        elegido, moneda = _elegir_monto(linea, montos)
        if elegido is None:
            return None

        try:
            monto = parse_monto(elegido.group(0))
        except ValueError:
            return None

        descripcion = linea[m_fecha.end() : montos[0].start()].strip(" .-\t")
        if not descripcion:
            descripcion = linea[m_fecha.end() :].strip(" .-\t")
        if not descripcion:
            return None

        desc_norm = normalizar_texto(descripcion)

        if es_tarjeta:
            # En un resumen de tarjeta los consumos se listan sin signo pero
            # son egresos. Los pagos y devoluciones van al reves.
            es_credito = any(marca in desc_norm for marca in _ES_CREDITO)
            monto = abs(monto) if es_credito else -abs(monto)

        return Movimiento(
            fecha=fecha,
            cuenta=cuenta,
            descripcion=descripcion,
            monto=monto,
            moneda=moneda,
            tipo=_inferir_tipo(desc_norm, es_tarjeta),
            fuente=f"pdf:{doc.ruta.name}#p{pagina}",
        )


# --------------------------------------------------------------------------


def _parece_movimiento(linea: str) -> bool:
    if not _RE_FECHA_INICIO.match(linea):
        return False
    if not _RE_MONTO.search(linea):
        return False
    norm = normalizar_texto(linea)
    return not any(p in norm for p in _NO_ES_MOVIMIENTO)


def _elegir_monto(linea: str, montos: list[re.Match]) -> tuple[re.Match | None, Moneda]:
    """De los importes de la linea elige el que corresponde y su moneda.

    Muchos resumenes traen una columna en pesos y otra en dolares, y dejan en
    cero la que no aplica. Se toma la ultima distinta de cero.
    """
    moneda = Moneda.USD if any(m in linea.upper() for m in _MARCAS_USD) else Moneda.ARS

    no_cero = []
    for m in montos:
        try:
            if parse_monto(m.group(0)) != 0:
                no_cero.append(m)
        except ValueError:
            continue

    if not no_cero:
        return None, moneda

    # Con dos columnas y una sola con valor, la posicion delata la moneda:
    # la columna de dolares va siempre a la derecha de la de pesos.
    if len(montos) >= 2 and len(no_cero) == 1 and no_cero[0] is montos[-1]:
        moneda = Moneda.USD

    return no_cero[-1], moneda


def _anio_del_documento(doc) -> int | None:
    """El anio del periodo, para las lineas que traen solo dia y mes."""
    anios = _RE_ANIO.findall(doc.texto[:3000])
    if not anios:
        anios = _RE_ANIO.findall(doc.texto)
    if not anios:
        return None
    # El mas frecuente en el encabezado, no el primero: los resumenes suelen
    # mencionar el anio de vencimiento y el del periodo en la misma pagina.
    return int(max(set(anios), key=anios.count))


def _nombre_de_cuenta(doc) -> str:
    """Identificador de cuenta derivado del archivo.

    Deliberadamente conservador: es preferible una cuenta mal nombrada, que
    se renombra con un comando, a mezclar dos cuentas distintas en una sola.
    """
    tallo = re.sub(r"[^a-z0-9]+", "_", doc.ruta.stem.lower()).strip("_")
    return tallo or "sin_identificar"


def _inferir_tipo(desc_norm: str, es_tarjeta: bool) -> Tipo:
    if any(p in desc_norm for p in ("IVA", "PERCEPCION", "IMPUESTO", "LEY 25413", "SELLOS")):
        return Tipo.IMPUESTO
    if any(p in desc_norm for p in ("INTERES", "PUNITORIO", "FINANCIACION")):
        return Tipo.INTERES
    if any(p in desc_norm for p in ("COMISION", "MANTENIMIENTO", "SEGURO DE VIDA", "CARGO")):
        return Tipo.COMISION
    if any(p in desc_norm for p in ("TRANSFERENCIA", "TRANSF", "DEBIN", "CVU", "CBU")):
        return Tipo.TRANSFERENCIA
    if any(p in desc_norm for p in _ES_CREDITO):
        return Tipo.CREDITO
    return Tipo.COMPRA if es_tarjeta else Tipo.DESCONOCIDO
