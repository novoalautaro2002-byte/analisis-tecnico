"""Persistencia en SQLite.

Un archivo, sin servidor. Para un usuario y unos miles de movimientos al ano
cualquier cosa mas grande es sobreingenieria.

Los montos se guardan como enteros en centavos. Nunca como float: un float
binario no puede representar 0,10 exacto y los errores se acumulan al sumar.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .modelo import Moneda, Movimiento, Tipo

ESQUEMA_VERSION = 2

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS movimientos (
    id              TEXT PRIMARY KEY,
    fecha           TEXT NOT NULL,
    fecha_operacion TEXT,
    cuenta          TEXT NOT NULL,
    descripcion     TEXT NOT NULL,
    comercio        TEXT,
    centavos        INTEGER NOT NULL,
    moneda          TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    cuota_actual    INTEGER,
    cuota_total     INTEGER,
    grupo_cuotas    TEXT,
    categoria       TEXT,
    fuente          TEXT,
    ingresado_en    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_mov_fecha  ON movimientos (fecha);
CREATE INDEX IF NOT EXISTS ix_mov_cuenta ON movimientos (cuenta, fecha);
CREATE INDEX IF NOT EXISTS ix_mov_grupo  ON movimientos (grupo_cuotas);

-- Archivos ya ingeridos, por hash de contenido. Permite volver a tirar la
-- carpeta entera sin reprocesar lo que ya se proceso.
CREATE TABLE IF NOT EXISTS archivos (
    sha256       TEXT PRIMARY KEY,
    nombre       TEXT NOT NULL,
    parser       TEXT,
    movimientos  INTEGER NOT NULL DEFAULT 0,
    ingresado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cuentas (
    id      TEXT PRIMARY KEY,
    nombre  TEXT NOT NULL,
    emisor  TEXT,
    clase   TEXT,
    moneda  TEXT NOT NULL DEFAULT 'ARS'
);

-- El ancla mensual de la conciliacion: el saldo real que cargas vos.
-- La moneda es parte de la clave: una misma cuenta puede tener saldo en
-- pesos y en dolares a la misma fecha, que es el caso normal aca. Sin la
-- moneda en la clave, el segundo saldo pisa al primero en silencio.
CREATE TABLE IF NOT EXISTS saldos (
    cuenta   TEXT NOT NULL,
    fecha    TEXT NOT NULL,
    moneda   TEXT NOT NULL DEFAULT 'ARS',
    centavos INTEGER NOT NULL,
    nota     TEXT,
    PRIMARY KEY (cuenta, fecha, moneda)
);

CREATE TABLE IF NOT EXISTS reglas_categoria (
    patron    TEXT PRIMARY KEY,
    categoria TEXT NOT NULL,
    prioridad INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def conectar(ruta: Path | str = "finanzas.db") -> sqlite3.Connection:
    """Abre la base, la crea si no existe y aplica migraciones pendientes."""
    con = sqlite3.connect(str(ruta))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(_ESQUEMA)
    _migrar(con)
    return con


def _migrar(con: sqlite3.Connection) -> None:
    fila = con.execute("SELECT valor FROM meta WHERE clave = 'esquema'").fetchone()
    actual = int(fila["valor"]) if fila else 0
    if actual == ESQUEMA_VERSION:
        return

    if 0 < actual < 2:
        # v1 tenia PRIMARY KEY (cuenta, fecha), sin la moneda: dos saldos de
        # la misma cuenta y fecha en monedas distintas se pisaban. SQLite no
        # permite cambiar una clave primaria, hay que reconstruir la tabla.
        con.executescript(
            """
            CREATE TABLE saldos_v2 (
                cuenta   TEXT NOT NULL,
                fecha    TEXT NOT NULL,
                moneda   TEXT NOT NULL DEFAULT 'ARS',
                centavos INTEGER NOT NULL,
                nota     TEXT,
                PRIMARY KEY (cuenta, fecha, moneda)
            );
            INSERT OR IGNORE INTO saldos_v2 (cuenta, fecha, moneda, centavos, nota)
                SELECT cuenta, fecha, moneda, centavos, nota FROM saldos;
            DROP TABLE saldos;
            ALTER TABLE saldos_v2 RENAME TO saldos;
            """
        )

    con.execute(
        "INSERT INTO meta (clave, valor) VALUES ('esquema', ?) "
        "ON CONFLICT (clave) DO UPDATE SET valor = excluded.valor",
        (str(ESQUEMA_VERSION),),
    )
    con.commit()


# --------------------------------------------------------------------------
# Escritura
# --------------------------------------------------------------------------


def guardar_movimientos(con: sqlite3.Connection, movs: list[Movimiento]) -> tuple[int, int]:
    """Inserta movimientos. Devuelve (nuevos, ya_existentes).

    La idempotencia sale del id determinista del modelo, no de comparar
    campos aca: un movimiento visto dos veces produce el mismo id y el
    INSERT OR IGNORE lo descarta.
    """
    ahora = datetime.now().isoformat(timespec="seconds")
    nuevos = 0
    for m in movs:
        cur = con.execute(
            """
            INSERT OR IGNORE INTO movimientos
                (id, fecha, fecha_operacion, cuenta, descripcion, comercio,
                 centavos, moneda, tipo, cuota_actual, cuota_total,
                 grupo_cuotas, categoria, fuente, ingresado_en)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                m.id,
                m.fecha.isoformat(),
                m.fecha_operacion.isoformat() if m.fecha_operacion else None,
                m.cuenta,
                m.descripcion,
                m.comercio,
                m.centavos,
                m.moneda.value,
                m.tipo.value,
                m.cuota_actual,
                m.cuota_total,
                m.grupo_cuotas,
                m.categoria,
                m.fuente,
                ahora,
            ),
        )
        nuevos += cur.rowcount
    con.commit()
    return nuevos, len(movs) - nuevos


def archivo_ya_ingerido(con: sqlite3.Connection, sha256: str) -> bool:
    fila = con.execute("SELECT 1 FROM archivos WHERE sha256 = ?", (sha256,)).fetchone()
    return fila is not None


def registrar_archivo(
    con: sqlite3.Connection, sha256: str, nombre: str, parser: str, movimientos: int
) -> None:
    con.execute(
        """
        INSERT INTO archivos (sha256, nombre, parser, movimientos, ingresado_en)
        VALUES (?,?,?,?,?)
        ON CONFLICT (sha256) DO UPDATE SET
            nombre = excluded.nombre,
            parser = excluded.parser,
            movimientos = excluded.movimientos
        """,
        (sha256, nombre, parser, movimientos, datetime.now().isoformat(timespec="seconds")),
    )
    con.commit()


def registrar_cuenta(
    con: sqlite3.Connection,
    id_cuenta: str,
    nombre: str,
    emisor: str = "",
    clase: str = "",
    moneda: str = "ARS",
) -> None:
    con.execute(
        """
        INSERT INTO cuentas (id, nombre, emisor, clase, moneda) VALUES (?,?,?,?,?)
        ON CONFLICT (id) DO UPDATE SET nombre = excluded.nombre
        """,
        (id_cuenta, nombre, emisor, clase, moneda),
    )
    con.commit()


def guardar_saldo(
    con: sqlite3.Connection,
    cuenta: str,
    fecha: date,
    monto: Decimal,
    moneda: str = "ARS",
    nota: str = "",
) -> None:
    """Registra el saldo real de una cuenta a una fecha. Es el ancla mensual."""
    con.execute(
        """
        INSERT INTO saldos (cuenta, fecha, moneda, centavos, nota) VALUES (?,?,?,?,?)
        ON CONFLICT (cuenta, fecha, moneda) DO UPDATE SET
            centavos = excluded.centavos, nota = excluded.nota
        """,
        (cuenta, fecha.isoformat(), moneda, int((monto * 100).to_integral_value()), nota),
    )
    con.commit()


# --------------------------------------------------------------------------
# Lectura
# --------------------------------------------------------------------------


def leer_movimientos(
    con: sqlite3.Connection,
    desde: date | None = None,
    hasta: date | None = None,
    cuenta: str | None = None,
) -> list[Movimiento]:
    sql = "SELECT * FROM movimientos WHERE 1=1"
    params: list[object] = []
    if desde:
        sql += " AND fecha >= ?"
        params.append(desde.isoformat())
    if hasta:
        sql += " AND fecha <= ?"
        params.append(hasta.isoformat())
    if cuenta:
        sql += " AND cuenta = ?"
        params.append(cuenta)
    sql += " ORDER BY fecha DESC, id"
    return [_fila_a_movimiento(f) for f in con.execute(sql, params)]


def _fila_a_movimiento(f: sqlite3.Row) -> Movimiento:
    return Movimiento(
        fecha=date.fromisoformat(f["fecha"]),
        cuenta=f["cuenta"],
        descripcion=f["descripcion"],
        monto=Decimal(f["centavos"]) / 100,
        moneda=Moneda(f["moneda"]),
        tipo=Tipo(f["tipo"]),
        fuente=f["fuente"] or "",
        fecha_operacion=date.fromisoformat(f["fecha_operacion"]) if f["fecha_operacion"] else None,
        cuota_actual=f["cuota_actual"],
        cuota_total=f["cuota_total"],
        categoria=f["categoria"],
        comercio=f["comercio"],
    )


def resumen_cuentas(con: sqlite3.Connection) -> list[dict]:
    """Por cuenta: cantidad de movimientos, rango de fechas y neto."""
    filas = con.execute(
        """
        SELECT cuenta, moneda,
               COUNT(*)        AS movimientos,
               MIN(fecha)      AS desde,
               MAX(fecha)      AS hasta,
               SUM(centavos)   AS neto
        FROM movimientos
        GROUP BY cuenta, moneda
        ORDER BY cuenta
        """
    ).fetchall()
    return [
        {
            "cuenta": f["cuenta"],
            "moneda": f["moneda"],
            "movimientos": f["movimientos"],
            "desde": f["desde"],
            "hasta": f["hasta"],
            "neto": Decimal(f["neto"]) / 100,
        }
        for f in filas
    ]
