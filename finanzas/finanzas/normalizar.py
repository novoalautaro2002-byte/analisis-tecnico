"""Categorizacion, cuotas y conciliacion.

Es donde esta el valor real del sistema. Las tres cosas comparten un
principio: son deterministas y auditables. Nada adivina en silencio.
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .modelo import Movimiento, normalizar_texto

# Reglas de arranque. Son intencionalmente pocas: la idea es que crezcan a
# partir de tus correcciones, no que yo adivine en que gastas.
REGLAS_INICIALES: list[tuple[str, str]] = [
    (r"\b(COTO|CARREFOUR|DIA|JUMBO|VEA|DISCO|CHANGOMAS|LIBERTAD)\b", "supermercado"),
    (r"\b(YPF|SHELL|AXION|PUMA|GNC)\b", "combustible"),
    (r"\b(NETFLIX|SPOTIFY|DISNEY|HBO|MAX|PRIME VIDEO|YOUTUBE)\b", "suscripciones"),
    (r"\b(MERCADOLIBRE|MERCADO LIBRE|MELI)\b", "compras"),
    (r"\b(UBER|CABIFY|DIDI|SUBE|AUTOPISTA|PEAJE)\b", "transporte"),
    (r"\b(RAPPI|PEDIDOSYA|PEDIDOS YA)\b", "delivery"),
    (r"\b(EDESUR|EDENOR|METROGAS|AYSA|ABL|MUNICIPAL)\b", "servicios"),
    (r"\b(PERSONAL|CLARO|MOVISTAR|FIBERTEL|TELECENTRO|FLOW)\b", "telefonia"),
    (r"\b(FARMACITY|FARMACIA|OSDE|SWISS MEDICAL|GALENO)\b", "salud"),
    (r"\b(IVA|PERCEPCION|IMPUESTO|LEY 25413|SELLOS|DEBITOS Y CREDITOS)\b", "impuestos"),
    (r"\b(INTERES|PUNITORIO|FINANCIACION|COMISION|MANTENIMIENTO)\b", "costo financiero"),
    (r"\b(SU PAGO|PAGO RECIBIDO|PAGO EN PESOS)\b", "pago de tarjeta"),
    (r"\b(SUELDO|HABERES|REMUNERACION)\b", "ingresos"),
]


def sembrar_reglas(con: sqlite3.Connection) -> int:
    """Carga las reglas iniciales si la tabla esta vacia. No pisa las tuyas."""
    hay = con.execute("SELECT COUNT(*) AS n FROM reglas_categoria").fetchone()["n"]
    if hay:
        return 0
    con.executemany(
        "INSERT OR IGNORE INTO reglas_categoria (patron, categoria) VALUES (?,?)",
        REGLAS_INICIALES,
    )
    con.commit()
    return len(REGLAS_INICIALES)


def cargar_reglas(con: sqlite3.Connection) -> list[tuple[re.Pattern, str]]:
    filas = con.execute(
        "SELECT patron, categoria FROM reglas_categoria ORDER BY prioridad, patron"
    ).fetchall()
    reglas = []
    for f in filas:
        try:
            reglas.append((re.compile(f["patron"]), f["categoria"]))
        except re.error:
            continue
    return reglas


def categorizar(con: sqlite3.Connection, solo_sin_categoria: bool = True) -> dict[str, int]:
    """Aplica las reglas a los movimientos. Devuelve el conteo por categoria.

    Determinista a proposito: la primera regla que matchea gana, y el orden
    lo fija la prioridad. Si un movimiento queda sin categoria, queda visible
    en el panel en vez de caer en un cajon 'otros' que nadie mira.
    """
    reglas = cargar_reglas(con)
    sql = "SELECT id, descripcion, comercio FROM movimientos"
    if solo_sin_categoria:
        sql += " WHERE categoria IS NULL"

    conteo: dict[str, int] = defaultdict(int)
    actualizaciones = []
    for fila in con.execute(sql):
        texto = normalizar_texto(f"{fila['descripcion']} {fila['comercio'] or ''}")
        for patron, categoria in reglas:
            if patron.search(texto):
                actualizaciones.append((categoria, fila["id"]))
                conteo[categoria] += 1
                break

    if actualizaciones:
        con.executemany("UPDATE movimientos SET categoria = ? WHERE id = ?", actualizaciones)
        con.commit()
    return dict(conteo)


def agregar_regla(con: sqlite3.Connection, patron: str, categoria: str, prioridad: int = 50) -> None:
    """Suma una regla. Prioridad menor gana: tus reglas le ganan a las iniciales."""
    re.compile(patron)  # falla temprano si la expresion esta mal
    con.execute(
        "INSERT INTO reglas_categoria (patron, categoria, prioridad) VALUES (?,?,?) "
        "ON CONFLICT (patron) DO UPDATE SET categoria = excluded.categoria, "
        "prioridad = excluded.prioridad",
        (patron, categoria, prioridad),
    )
    con.commit()


# ----------------------------------------------------------------------
# Cuotas
# ----------------------------------------------------------------------


@dataclass
class Compromiso:
    """Lo que queda por pagar de una compra en cuotas."""

    grupo: str
    descripcion: str
    cuenta: str
    moneda: str
    cuota_actual: int
    cuota_total: int
    monto_cuota: Decimal

    @property
    def cuotas_restantes(self) -> int:
        return max(0, self.cuota_total - self.cuota_actual)

    @property
    def total_pendiente(self) -> Decimal:
        return abs(self.monto_cuota) * self.cuotas_restantes


def compromisos_pendientes(con: sqlite3.Connection) -> list[Compromiso]:
    """Cuanto hay comprometido hacia adelante por compras en cuotas.

    Se toma la cuota mas avanzada vista de cada grupo: es la que dice en que
    punto del plan estas hoy.
    """
    filas = con.execute(
        """
        SELECT grupo_cuotas, descripcion, cuenta, moneda,
               MAX(cuota_actual) AS cuota_actual, cuota_total, centavos
        FROM movimientos
        WHERE grupo_cuotas IS NOT NULL AND cuota_total > 1
        GROUP BY grupo_cuotas
        ORDER BY cuota_total - MAX(cuota_actual) DESC
        """
    ).fetchall()

    compromisos = [
        Compromiso(
            grupo=f["grupo_cuotas"],
            descripcion=f["descripcion"],
            cuenta=f["cuenta"],
            moneda=f["moneda"],
            cuota_actual=f["cuota_actual"],
            cuota_total=f["cuota_total"],
            monto_cuota=Decimal(f["centavos"]) / 100,
        )
        for f in filas
    ]
    return [c for c in compromisos if c.cuotas_restantes > 0]


# ----------------------------------------------------------------------
# Conciliacion
# ----------------------------------------------------------------------


@dataclass
class Conciliacion:
    cuenta: str
    fecha: date
    saldo_real: Decimal
    saldo_derivado: Decimal
    moneda: str

    @property
    def diferencia(self) -> Decimal:
        return self.saldo_real - self.saldo_derivado

    @property
    def cuadra(self) -> bool:
        return abs(self.diferencia) < Decimal("0.01")


def conciliar(con: sqlite3.Connection) -> list[Conciliacion]:
    """Compara el saldo real que cargaste contra el que sale de los movimientos.

    Este es el control de calidad del sistema. Si la diferencia es cero, la
    ingesta esta capturando todo. Si no lo es, sabes exactamente cuanto falta,
    aunque no sepas de que movimiento se trata.

    Entre dos anclas consecutivas se compara la variacion, no el saldo
    absoluto: la primera ancla fija el punto de partida y no hay forma de
    derivar lo que paso antes de ella.

    Cada moneda se concilia por separado. Sumar pesos y dolares en un mismo
    total no significa nada, y el error queda escondido dentro de una
    diferencia que parece plausible.
    """
    anclas = con.execute(
        "SELECT cuenta, fecha, centavos, moneda FROM saldos ORDER BY cuenta, moneda, fecha"
    ).fetchall()

    por_cuenta: dict[tuple[str, str], list] = defaultdict(list)
    for a in anclas:
        por_cuenta[(a["cuenta"], a["moneda"])].append(a)

    resultado: list[Conciliacion] = []
    for (cuenta, moneda), lista in por_cuenta.items():
        for previa, actual in zip(lista, lista[1:]):
            flujo = con.execute(
                """
                SELECT COALESCE(SUM(centavos), 0) AS neto FROM movimientos
                WHERE cuenta = ? AND moneda = ? AND fecha > ? AND fecha <= ?
                """,
                (cuenta, moneda, previa["fecha"], actual["fecha"]),
            ).fetchone()["neto"]

            derivado = Decimal(previa["centavos"] + flujo) / 100
            resultado.append(
                Conciliacion(
                    cuenta=cuenta,
                    fecha=date.fromisoformat(actual["fecha"]),
                    saldo_real=Decimal(actual["centavos"]) / 100,
                    saldo_derivado=derivado,
                    moneda=actual["moneda"],
                )
            )
    return resultado


# ----------------------------------------------------------------------
# Salud de la ingesta
# ----------------------------------------------------------------------


def dias_sin_movimientos(con: sqlite3.Connection) -> int | None:
    """Dias desde el ultimo movimiento registrado.

    El silencio es el unico modo de falla peligroso del diseno: si la ingesta
    se rompe, el panel no se pone en rojo, se queda quieto, y eso es
    indistinguible de un mes sin gastos.
    """
    fila = con.execute("SELECT MAX(fecha) AS ultima FROM movimientos").fetchone()
    if not fila or not fila["ultima"]:
        return None
    return (date.today() - date.fromisoformat(fila["ultima"])).days
