"""Lectura del libro de una subasta.

La pantalla `cpd-versubasta.r` no hay que rasparla celda por celda: las ofertas
vienen en un array de JavaScript que despues dibuja la grilla Active Widgets.

    var myData = [
    [ " 2046207","442", "10,00", "10:51:52", "<a href='#' onClick='bajaOferta(2046207)'>[X]</a>"]
    ];
    var myColumns = [ "Oferta", "Ag.", "Desc.", "Ingreso", "Baja" ];

Todo lo que necesita el bot esta ahi: id de oferta, agente, tasa, hora con
segundos, y si la oferta es propia.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time
from decimal import Decimal, InvalidOperation

# Un centavo. Es el paso minimo de tasa y la unidad a la que se cuantiza todo.
PASO = Decimal("0.01")


class LibroIlegible(Exception):
    """El HTML no tiene la forma esperada.

    Siempre se levanta en vez de adivinar: un libro mal leido es una decision
    mal tomada, y preferimos que el bot se pare.
    """


@dataclass(frozen=True)
class Oferta:
    id: int
    agente: str
    tasa: Decimal
    ingreso: time
    propia: bool


@dataclass(frozen=True)
class Libro:
    ident: int                 # numero de subasta
    ofertas: tuple[Oferta, ...]

    @property
    def propias(self) -> tuple[Oferta, ...]:
        return tuple(o for o in self.ofertas if o.propia)

    @property
    def ajenas(self) -> tuple[Oferta, ...]:
        return tuple(o for o in self.ofertas if not o.propia)

    def mejor_propia(self) -> Oferta | None:
        """La oferta propia mas competitiva: la de menor tasa.

        Gana quien ofrece la tasa mas baja, porque es el descuento que paga el
        vendedor del cheque. Si dos empatan, la que entro antes.
        """
        return min(self.propias, key=lambda o: (o.tasa, o.ingreso), default=None)

    def mejor_ajena(self) -> Oferta | None:
        return min(self.ajenas, key=lambda o: (o.tasa, o.ingreso), default=None)


_MY_DATA = re.compile(r"var\s+myData\s*=\s*\[(.*?)\]\s*;", re.S)
_MY_COLUMNS = re.compile(r"var\s+myColumns\s*=\s*\[(.*?)\]\s*;", re.S)
_CADENA = re.compile(r'"((?:[^"\\]|\\.)*)"')
_IDENT = re.compile(r"""<input[^>]*name=["']?ident["']?[^>]*value=["']?(\d+)""", re.I)


def _cadenas(bloque: str) -> list[str]:
    return _CADENA.findall(bloque)


def parsear_tasa(texto: str) -> Decimal:
    """'10,00' -> Decimal('10.00').

    Formato local: coma decimal y punto de miles. El JS de la plataforma rechaza
    el punto decimal, asi que quien escriba tasas tiene que emitir coma.

    Decimal y no float: comparar tasas con float da sorpresas (0.1 + 0.2), y acá
    un centavo de diferencia decide quien se lleva el cheque.
    """
    limpio = texto.strip().replace(".", "").replace(",", ".")
    try:
        valor = Decimal(limpio)
    except InvalidOperation as e:
        raise LibroIlegible(f"tasa ilegible: {texto!r}") from e
    if not valor.is_finite():
        raise LibroIlegible(f"tasa no finita: {texto!r}")
    return valor


def formatear_tasa(tasa: Decimal) -> str:
    """Decimal('26.99') -> '26,99', que es lo que espera el input de la pantalla."""
    return f"{tasa.quantize(PASO):f}".replace(".", ",")


def parsear_hora(texto: str) -> time:
    partes = texto.strip().split(":")
    if len(partes) != 3:
        raise LibroIlegible(f"hora ilegible: {texto!r}")
    try:
        h, m, s = (int(p) for p in partes)
        return time(h, m, s)
    except ValueError as e:
        raise LibroIlegible(f"hora ilegible: {texto!r}") from e


def parsear_libro(html: str, mi_agente: str | None = None) -> Libro:
    """Saca el libro del HTML de cpd-versubasta.r.

    `mi_agente` es el numero de agente propio, que es como se opera en MAV: no
    por usuario. Una oferta es propia cuando el agente coincide, y punto.

    Sin `mi_agente` se cae al link de baja, que sirve para una mirada suelta
    pero no para decidir: la plataforma lo saca cuando la subasta cierra, y
    tampoco aparece en todos los casos.
    """
    m_ident = _IDENT.search(html)
    if not m_ident:
        raise LibroIlegible("no encontre el campo 'ident' con el numero de subasta")
    ident = int(m_ident.group(1))

    m_col = _MY_COLUMNS.search(html)
    if not m_col:
        raise LibroIlegible("no encontre myColumns")
    columnas = _cadenas(m_col.group(1))
    if not columnas:
        raise LibroIlegible("myColumns vacio")

    m_datos = _MY_DATA.search(html)
    if not m_datos:
        raise LibroIlegible("no encontre myData")
    celdas = _cadenas(m_datos.group(1))

    # Las filas se cortan por cantidad de columnas y no por corchetes: la celda
    # de baja trae un "[X]" adentro que rompe cualquier conteo de corchetes.
    ancho = len(columnas)
    if len(celdas) % ancho:
        raise LibroIlegible(
            f"myData tiene {len(celdas)} celdas, que no es multiplo de las {ancho} columnas"
        )

    indice = {nombre: i for i, nombre in enumerate(columnas)}
    for requerida in ("Oferta", "Ag.", "Desc.", "Ingreso", "Baja"):
        if requerida not in indice:
            raise LibroIlegible(f"falta la columna {requerida!r}; hay {columnas}")

    ofertas = []
    for i in range(0, len(celdas), ancho):
        fila = celdas[i:i + ancho]
        crudo_id = fila[indice["Oferta"]].strip()
        if not crudo_id.isdigit():
            raise LibroIlegible(f"id de oferta ilegible: {crudo_id!r}")
        agente = fila[indice["Ag."]].strip()
        ofertas.append(Oferta(
            id=int(crudo_id),
            agente=agente,
            tasa=parsear_tasa(fila[indice["Desc."]]),
            ingreso=parsear_hora(fila[indice["Ingreso"]]),
            propia=(agente == mi_agente.strip() if mi_agente
                    else "bajaOferta(" in fila[indice["Baja"]]),
        ))

    return Libro(ident=ident, ofertas=tuple(ofertas))
