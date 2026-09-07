"""Generacion del panel.

Mismo patron que analisis-tecnico: un HTML autocontenido que lee un JSON
generado al lado. El JSON es tambien el formato de exportacion, asi que
sirve para llevarse los datos a una planilla sin tocar la base.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from . import normalizar
from .modelo import TIPOS_NO_COMPUTABLES

# Se excluyen de todo total de gastos, pero siguen en la base: el total
# impreso del resumen los incluye y sin ellos la verificacion no cierra.
_FILTRO_COMPUTABLE = "tipo NOT IN ({})".format(
    ",".join(f"'{t}'" for t in sorted(TIPOS_NO_COMPUTABLES))
)


def _d(valor: Decimal) -> float:
    """Decimal a float, solo para serializar. Los calculos ya se hicieron."""
    return float(valor)


def construir_datos(con: sqlite3.Connection) -> dict:
    cuentas = con.execute(
        """
        SELECT cuenta, moneda, COUNT(*) AS movimientos,
               MIN(fecha) AS desde, MAX(fecha) AS hasta, SUM(centavos) AS neto
        FROM movimientos GROUP BY cuenta, moneda ORDER BY cuenta
        """
    ).fetchall()

    por_mes = con.execute(
        f"""
        SELECT substr(fecha, 1, 7) AS mes, moneda,
               SUM(CASE WHEN centavos < 0 THEN -centavos ELSE 0 END) AS egresos,
               SUM(CASE WHEN centavos > 0 THEN  centavos ELSE 0 END) AS ingresos
        FROM movimientos WHERE {_FILTRO_COMPUTABLE}
        GROUP BY mes, moneda ORDER BY mes
        """
    ).fetchall()

    por_categoria = con.execute(
        f"""
        SELECT COALESCE(categoria, 'sin categoria') AS categoria, moneda,
               COUNT(*) AS n, SUM(centavos) AS total
        FROM movimientos WHERE {_FILTRO_COMPUTABLE}
        GROUP BY categoria, moneda
        ORDER BY ABS(SUM(centavos)) DESC
        """
    ).fetchall()

    recientes = con.execute(
        """
        SELECT fecha, cuenta, descripcion, centavos, moneda, categoria,
               cuota_actual, cuota_total
        FROM movimientos ORDER BY fecha DESC, id LIMIT 100
        """
    ).fetchall()

    excluido = con.execute(
        f"""
        SELECT tipo, moneda, COUNT(*) AS n, SUM(centavos) AS total
        FROM movimientos WHERE NOT ({_FILTRO_COMPUTABLE})
        GROUP BY tipo, moneda ORDER BY tipo
        """
    ).fetchall()

    conciliaciones = normalizar.conciliar(con)
    compromisos = normalizar.compromisos_pendientes(con)
    dias_quieto = normalizar.dias_sin_movimientos(con)

    return {
        "generado": date.today().isoformat(),
        "salud": {
            "dias_sin_movimientos": dias_quieto,
            # El silencio es el modo de falla que no se ve. Se marca explicito.
            "ingesta_sospechosa": dias_quieto is not None and dias_quieto > 10,
            "conciliaciones_que_cierran": sum(1 for c in conciliaciones if c.cuadra),
            "conciliaciones_totales": len(conciliaciones),
        },
        # Impuestos y pagos de tarjeta: se muestran aparte, nunca en gastos.
        "excluido": [
            {
                "tipo": f["tipo"], "moneda": f["moneda"], "n": f["n"],
                "total": _d(Decimal(f["total"]) / 100),
            }
            for f in excluido
        ],
        "cuentas": [
            {
                "cuenta": f["cuenta"], "moneda": f["moneda"],
                "movimientos": f["movimientos"], "desde": f["desde"], "hasta": f["hasta"],
                "neto": _d(Decimal(f["neto"]) / 100),
            }
            for f in cuentas
        ],
        "por_mes": [
            {
                "mes": f["mes"], "moneda": f["moneda"],
                "egresos": _d(Decimal(f["egresos"]) / 100),
                "ingresos": _d(Decimal(f["ingresos"]) / 100),
            }
            for f in por_mes
        ],
        "por_categoria": [
            {
                "categoria": f["categoria"], "moneda": f["moneda"], "n": f["n"],
                "total": _d(Decimal(f["total"]) / 100),
            }
            for f in por_categoria
        ],
        "conciliacion": [
            {
                "cuenta": c.cuenta, "fecha": c.fecha.isoformat(), "moneda": c.moneda,
                "saldo_real": _d(c.saldo_real), "saldo_derivado": _d(c.saldo_derivado),
                "diferencia": _d(c.diferencia), "cuadra": c.cuadra,
            }
            for c in conciliaciones
        ],
        "cuotas": [
            {
                "descripcion": c.descripcion, "cuenta": c.cuenta, "moneda": c.moneda,
                "cuota_actual": c.cuota_actual, "cuota_total": c.cuota_total,
                "monto_cuota": _d(abs(c.monto_cuota)),
                "pendiente": _d(c.total_pendiente),
            }
            for c in compromisos
        ],
        "movimientos": [
            {
                "fecha": f["fecha"], "cuenta": f["cuenta"], "descripcion": f["descripcion"],
                "monto": _d(Decimal(f["centavos"]) / 100), "moneda": f["moneda"],
                "categoria": f["categoria"],
                "cuota": f"{f['cuota_actual']}/{f['cuota_total']}" if f["cuota_total"] else None,
            }
            for f in recientes
        ],
    }


PLANTILLA = Path(__file__).parent.parent / "panel" / "plantilla.html"
_MARCA_DATOS = "<!--DATOS-->"


def generar(con: sqlite3.Connection, salida: Path, unico: bool = False) -> Path:
    """Escribe el panel. Con unico=True los datos van embebidos en el HTML.

    El modo unico existe por el telefono: un HTML que hace fetch de datos.json
    no funciona abierto con file://, porque el navegador bloquea la lectura.
    Con los datos adentro es un solo archivo que se manda por donde sea y se
    abre sin servidor ni conexion.
    """
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)

    datos = construir_datos(con)
    html = PLANTILLA.read_text(encoding="utf-8")

    if unico:
        html = html.replace(_MARCA_DATOS, _bloque_datos(datos), 1)
    else:
        (salida.parent / "datos.json").write_text(
            json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if PLANTILLA.resolve() != salida.resolve():
        salida.write_text(html, encoding="utf-8")
    return salida


def _bloque_datos(datos: dict) -> str:
    """Serializa los datos para embeberlos en el HTML.

    '</script>' dentro del JSON cerraria la etiqueta antes de tiempo y rompe
    la pagina, asi que se escapa. Los datos vienen de descripciones de
    resumenes bancarios, que son texto arbitrario.
    """
    crudo = json.dumps(datos, ensure_ascii=False, separators=(",", ":"))
    seguro = crudo.replace("</", "<\\/")
    return f"<script>window.__DATOS__ = {seguro};</script>"
