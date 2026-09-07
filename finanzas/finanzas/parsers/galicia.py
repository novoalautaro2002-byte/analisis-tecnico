"""Parser de resumenes de Banco Galicia.

Estado: reconoce el emisor y le da a la cuenta un identificador real (tipo de
cuenta, moneda y ultimos digitos), que es lo que el parser generico no puede
hacer. La lectura de las lineas todavia es la generica.

Lo que falta para que sea un parser propio de verdad: las secciones del
resumen (consumos, impuestos, pagos), el bloque de cuotas y el total impreso
para cuadrar contra el. Eso requiere ver un resumen real: los formatos varian
entre cuenta y tarjeta, y entre producto y producto.

Cuando tengas un PDF de muestra, lo unico que cambia es el metodo parsear.
El resto del sistema no se entera.
"""

from __future__ import annotations

import re

from ..modelo import Moneda, normalizar_texto
from .base import Parser, ResultadoParseo, registrar
from .tarjeta_generica import TarjetaGenerica

_MARCAS = ("BANCO GALICIA", "GALICIA", "BANCO DE GALICIA", "GALICIA MAS")

# Encabezados tipicos de los que sale el identificador de cuenta.
_RE_CUENTA = re.compile(
    r"\b(CAJA DE AHORRO|CUENTA CORRIENTE|CUENTA UNICA|CA\s*\$|CC\s*\$)"
    r"[^\d]{0,40}(\d[\d\-/.]{5,})",
)
_RE_TARJETA = re.compile(r"\b(?:VISA|MASTERCARD|AMEX|AMERICAN EXPRESS)\b[^\d]{0,30}(\d{4})\b")
_RE_ULTIMOS4 = re.compile(r"\b[X*]{4,}\s*(\d{4})\b")


@registrar
class Galicia(Parser):
    nombre = "galicia"
    especificidad = 100

    def reconoce(self, doc) -> bool:
        if doc.esta_vacio:
            return False
        cabecera = normalizar_texto(doc.texto[:3000])
        return any(marca in cabecera for marca in _MARCAS)

    def parsear(self, doc) -> ResultadoParseo:
        generico = TarjetaGenerica()
        res = generico.parsear(doc)

        cuenta = _identificar_cuenta(doc)
        for mov in res.movimientos:
            mov.cuenta = cuenta

        res.advertencias.append(
            f"Galicia: cuenta identificada como '{cuenta}'. La lectura de lineas "
            "todavia usa el parser generico; puede perder impuestos y el detalle "
            "de cuotas. Cuadra contra el total impreso antes de confiar en los numeros."
        )
        return res


def _identificar_cuenta(doc) -> str:
    """Arma un id de cuenta estable a partir del encabezado del resumen.

    Estable importa: si el id cambia entre un resumen y el siguiente, la misma
    cuenta aparece partida en dos y la conciliacion deja de cerrar.
    """
    cabecera = doc.texto[:4000]
    normal = normalizar_texto(cabecera)

    m = _RE_TARJETA.search(normal)
    if m:
        marca = re.search(r"\b(VISA|MASTERCARD|AMEX|AMERICAN EXPRESS)\b", normal)
        sello = (marca.group(1) if marca else "TARJETA").split()[0].lower()
        return f"galicia_{sello}_{m.group(1)}"

    m = _RE_ULTIMOS4.search(normal)
    if m:
        return f"galicia_tarjeta_{m.group(1)}"

    m = _RE_CUENTA.search(normal)
    if m:
        clase = "cc" if "CORRIENTE" in m.group(1) else "ca"
        digitos = re.sub(r"\D", "", m.group(2))[-6:]
        moneda = "usd" if _es_usd(normal) else "ars"
        return f"galicia_{clase}_{moneda}_{digitos}"

    return "galicia_sin_identificar"


def _es_usd(normal: str) -> bool:
    return any(m in normal for m in ("DOLARES", "USD", "U S", "MONEDA EXTRANJERA"))
