"""Modelo canonico de movimiento.

Todo lo que entra al sistema, venga de un PDF o de la API de Mercado Pago,
termina siendo un Movimiento. Los parsers no hablan entre si ni conocen la
base: producen Movimientos y nada mas.

Convencion de signo: negativo es egreso (plata que sale), positivo es ingreso.
Un resumen de tarjeta lista consumos como positivos; el parser los invierte.

Convencion de moneda: se guarda siempre el monto en su moneda original. La
conversion a una moneda de referencia es una vista, nunca un dato persistido.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum


class Tipo(str, Enum):
    COMPRA = "compra"
    TRANSFERENCIA = "transferencia"
    DEBITO = "debito"
    CREDITO = "credito"
    IMPUESTO = "impuesto"
    INTERES = "interes"
    COMISION = "comision"
    PAGO_TARJETA = "pago_tarjeta"
    AJUSTE = "ajuste"
    DESCONOCIDO = "desconocido"


class Moneda(str, Enum):
    ARS = "ARS"
    USD = "USD"


# Tipos que NO son gasto propio y quedan fuera de todo total de gastos.
#
# IMPUESTO: las percepciones sobre consumos en dolares se devuelven al pagar
#   en dolares (la linea DEV.IMP. del resumen). Contarlas seria inflar el
#   gasto con plata que vuelve.
# PAGO_TARJETA: el pago del resumen no es un ingreso, es la liquidacion de
#   consumos que ya se contaron uno por uno. Sumarlo los contaria dos veces.
#
# Se siguen guardando: el total impreso del resumen los incluye, y sin ellos
# la verificacion contra ese total no cierra.
TIPOS_NO_COMPUTABLES = frozenset({"impuesto", "pago_tarjeta"})


def es_gasto_computable(tipo: "Tipo | str") -> bool:
    valor = tipo.value if isinstance(tipo, Tipo) else str(tipo)
    return valor not in TIPOS_NO_COMPUTABLES


# --------------------------------------------------------------------------
# Normalizacion de texto
# --------------------------------------------------------------------------

_ESPACIOS = re.compile(r"\s+")
_NO_ALFANUM = re.compile(r"[^A-Z0-9 ]")
# Variante que conserva la barra y el punto: los patrones de cuota los
# necesitan ("CUOTA 03/06"), y la normalizacion fuerte se los come.
_NO_ALFANUM_LIGERO = re.compile(r"[^A-Z0-9/. ]")


def _sin_acentos(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def normalizar_ligero(s: str) -> str:
    """Mayusculas y sin acentos, pero conserva '/' y '.'."""
    return _ESPACIOS.sub(" ", _NO_ALFANUM_LIGERO.sub(" ", _sin_acentos(s).upper())).strip()


def clave_texto(s: str) -> str:
    """Normalizacion sin espacios, solo para la clave de identidad.

    Sin esto 'S.A.' y 'SA' producen ids distintos para el mismo comercio y el
    mismo movimiento aparece dos veces al cruzar dos resumenes.
    """
    return normalizar_texto(s).replace(" ", "")


def normalizar_texto(s: str) -> str:
    """Mayusculas, sin acentos, sin puntuacion, espacios colapsados.

    Se usa para la clave de deduplicacion y para hacer matching de reglas de
    categoria. No se guarda: la descripcion original siempre se preserva.
    """
    return _ESPACIOS.sub(" ", _NO_ALFANUM.sub(" ", _sin_acentos(s).upper())).strip()


# --------------------------------------------------------------------------
# Parseo de montos en formato argentino
# --------------------------------------------------------------------------

_LIMPIAR_MONTO = re.compile(r"[^\d,.\-()]")


def parse_monto(texto: str) -> Decimal:
    """Convierte un monto de resumen argentino a Decimal.

    Maneja los formatos que aparecen en la practica:
      '1.234,56'  -> 1234.56   (formato AR, coma decimal)
      '1,234.56'  -> 1234.56   (formato US, aparece en tramos en USD)
      '1234,56'   -> 1234.56
      '1.234'     -> 1234      (sin decimales)
      '123,45-'   -> -123.45   (signo al final, comun en extractos)
      '(123,45)'  -> -123.45   (parentesis = negativo)

    La regla general: cuando hay dos separadores distintos, el ultimo es el
    decimal. Cuando hay uno solo, es decimal unicamente si deja exactamente
    dos digitos a la derecha y aparece una sola vez.
    """
    if texto is None:
        raise ValueError("monto vacio")

    crudo = str(texto).strip()
    if not crudo:
        raise ValueError("monto vacio")

    negativo = False
    if "(" in crudo and ")" in crudo:
        negativo = True
    limpio = _LIMPIAR_MONTO.sub("", crudo).replace("(", "").replace(")", "")

    if limpio.endswith("-"):
        negativo = True
        limpio = limpio[:-1]
    if limpio.startswith("-"):
        negativo = not negativo
        limpio = limpio[1:]

    limpio = limpio.strip()
    if not limpio:
        raise ValueError(f"monto sin digitos: {texto!r}")

    tiene_punto = "." in limpio
    tiene_coma = "," in limpio

    if tiene_punto and tiene_coma:
        # El separador mas a la derecha es el decimal.
        decimal_sep = "," if limpio.rfind(",") > limpio.rfind(".") else "."
        miles_sep = "." if decimal_sep == "," else ","
        limpio = limpio.replace(miles_sep, "").replace(decimal_sep, ".")
    elif tiene_coma or tiene_punto:
        sep = "," if tiene_coma else "."
        partes = limpio.split(sep)
        # Un unico separador con dos decimales a la derecha: es decimal.
        if len(partes) == 2 and len(partes[1]) == 2:
            limpio = partes[0] + "." + partes[1]
        else:
            limpio = limpio.replace(sep, "")

    try:
        valor = Decimal(limpio)
    except InvalidOperation as exc:
        raise ValueError(f"no se pudo parsear el monto {texto!r}") from exc

    return -valor if negativo else valor


# --------------------------------------------------------------------------
# Parseo de fechas
# --------------------------------------------------------------------------

_MESES = {
    "ENE": 1, "FEB": 2, "MAR": 3, "ABR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AGO": 8, "SEP": 9, "SET": 9, "OCT": 10, "NOV": 11, "DIC": 12,
}

_F_NUMERICA = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})$")
_F_MES_TEXTO = re.compile(r"^(\d{1,2})[/\-. ]([A-Z]{3})[/\-. ]?(\d{2,4})?$")


def parse_fecha(texto: str, anio_defecto: int | None = None) -> date:
    """Convierte una fecha de resumen a date. Siempre dia/mes/anio.

    Acepta '05/03/25', '05-03-2025', '05-MAR-25' y '05 MAR' (con anio por
    defecto, que es lo que aparece en los detalles de consumo de tarjeta,
    donde el anio esta en el encabezado del periodo y no en cada linea).
    """
    crudo = normalizar_texto(str(texto)).replace(" ", "/")
    if not crudo:
        raise ValueError("fecha vacia")

    m = _F_NUMERICA.match(crudo)
    if m:
        dia, mes, anio = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return date(_expandir_anio(anio), mes, dia)

    m = _F_MES_TEXTO.match(crudo)
    if m:
        dia = int(m.group(1))
        mes = _MESES.get(m.group(2))
        if mes is None:
            raise ValueError(f"mes desconocido en {texto!r}")
        if m.group(3):
            anio = _expandir_anio(int(m.group(3)))
        elif anio_defecto is not None:
            anio = anio_defecto
        else:
            raise ValueError(f"fecha sin anio y sin anio por defecto: {texto!r}")
        return date(anio, mes, dia)

    raise ValueError(f"formato de fecha no reconocido: {texto!r}")


def _expandir_anio(anio: int) -> int:
    """'25' -> 2025. Los resumenes usan dos digitos casi siempre."""
    if anio >= 100:
        return anio
    return 2000 + anio if anio < 70 else 1900 + anio


# --------------------------------------------------------------------------
# Cuotas
# --------------------------------------------------------------------------

_PATRONES_CUOTA = [
    re.compile(r"\bCUOTA\s*(\d{1,2})\s*[/DE]{1,2}\s*(\d{1,2})\b"),
    re.compile(r"\bC\s*\.?\s*(\d{1,2})\s*/\s*(\d{1,2})\b"),
    re.compile(r"\bCUOT\s*(\d{1,2})\s*/\s*(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\s*$"),
]


def extraer_cuota(descripcion: str) -> tuple[int, int] | None:
    """Devuelve (cuota_actual, cuota_total) si la descripcion la declara.

    Una compra en 6 cuotas no son 6 compras: es un evento con un cronograma.
    Detectarla aca es lo que despues permite ver cuanto hay comprometido
    hacia adelante.
    """
    texto = normalizar_ligero(descripcion)
    for patron in _PATRONES_CUOTA:
        m = patron.search(texto)
        if not m:
            continue
        actual, total = int(m.group(1)), int(m.group(2))
        if 1 <= actual <= total <= 99 and total > 1:
            return actual, total
    return None


# --------------------------------------------------------------------------
# Movimiento
# --------------------------------------------------------------------------


@dataclass
class Movimiento:
    """Un movimiento ya normalizado, listo para persistir."""

    fecha: date
    cuenta: str
    descripcion: str
    monto: Decimal
    moneda: Moneda = Moneda.ARS
    tipo: Tipo = Tipo.DESCONOCIDO
    fuente: str = ""
    fecha_operacion: date | None = None
    cuota_actual: int | None = None
    cuota_total: int | None = None
    categoria: str | None = None
    comercio: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.monto, Decimal):
            self.monto = Decimal(str(self.monto))
        if isinstance(self.moneda, str):
            self.moneda = Moneda(self.moneda)
        if isinstance(self.tipo, str):
            self.tipo = Tipo(self.tipo)
        if self.cuota_actual is None and self.cuota_total is None:
            cuota = extraer_cuota(self.descripcion)
            if cuota:
                self.cuota_actual, self.cuota_total = cuota
        if self.comercio is None:
            self.comercio = self.descripcion_normalizada

    @property
    def descripcion_normalizada(self) -> str:
        return normalizar_texto(self.descripcion)

    @property
    def centavos(self) -> int:
        """El monto en la unidad minima. Es asi como se persiste: entero exacto."""
        return int((self.monto * 100).to_integral_value())

    @property
    def id(self) -> str:
        """Hash determinista del movimiento.

        Reingerir el mismo PDF, o dos resumenes con periodos que se solapan,
        produce exactamente el mismo id, asi que la insercion es idempotente
        sin necesidad de comparar nada.

        Deliberadamente NO incluye el archivo de origen: el mismo movimiento
        visto desde dos archivos distintos tiene que colapsar en uno.
        """
        crudo = "|".join([
            self.cuenta,
            self.fecha.isoformat(),
            str(self.centavos),
            self.moneda.value,
            clave_texto(self.descripcion),
        ])
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:32]

    @property
    def grupo_cuotas(self) -> str | None:
        """Identifica todas las cuotas de una misma compra.

        Se arma sin el numero de cuota ni la fecha, que son justamente lo que
        cambia entre una cuota y la siguiente.
        """
        if not self.cuota_total:
            return None
        base = normalizar_ligero(self.descripcion)
        for patron in _PATRONES_CUOTA:
            base = patron.sub("", base)
        base = normalizar_texto(base).replace(" ", "")
        crudo = f"{self.cuenta}|{base}|{self.centavos}|{self.cuota_total}"
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:16]

    def __repr__(self) -> str:  # pragma: no cover - solo para depurar
        cuota = f" [{self.cuota_actual}/{self.cuota_total}]" if self.cuota_total else ""
        return (
            f"<Movimiento {self.fecha} {self.cuenta} "
            f"{self.monto:,.2f} {self.moneda.value} {self.descripcion[:40]!r}{cuota}>"
        )
