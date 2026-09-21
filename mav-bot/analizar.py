#!/usr/bin/env python3
"""Reconstruye una guerra de tasas y muestra que habria hecho el bot.

Lee capturas de cpd-versubasta.r, arma la linea de tiempo, mide los tiempos de
respuesta de cada agente y corre el motor contra cada momento de la partida.

No toca la plataforma. Es lectura de archivos y nada mas.

    python analizar.py capturas/*.htm --agente 442 --piso 25,00
"""

from __future__ import annotations

import argparse
import glob
import random
import sys
from decimal import Decimal
from pathlib import Path

from motor.analisis import clasificar, fusionar, reacciones
from motor.config import ConfigSubasta
from motor.decision import Accion, decidir
from motor.libro import (
    Libro,
    LibroIlegible,
    Oferta,
    formatear_tasa,
    parsear_libro,
    parsear_tasa,
)


def leer(rutas: list[Path]) -> list[Libro]:
    libros = []
    for ruta in rutas:
        # La plataforma emite ISO-8859-1. Si algun archivo vino de otro lado,
        # se lee igual sin romper por un acento.
        texto = ruta.read_bytes().decode("latin-1")
        try:
            libros.append(parsear_libro(texto))
        except LibroIlegible as e:
            print(f"  salteo {ruta.name}: {e}", file=sys.stderr)
    return libros


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("archivos", nargs="+", help="Capturas HTML de la subasta")
    ap.add_argument("--agente", required=True, help="Tu numero de agente (ej: 442)")
    ap.add_argument("--piso", required=True, help="Tasa minima aceptable, con coma (ej: 25,00)")
    ap.add_argument("--decremento-min", default="0,01")
    ap.add_argument("--decremento-max", default="0,01")
    ap.add_argument("--semilla", type=int, default=0)
    args = ap.parse_args()

    rutas = [Path(p) for patron in args.archivos for p in sorted(glob.glob(patron))]
    if not rutas:
        print("No encontre ninguno de esos archivos.", file=sys.stderr)
        return 1

    libros = leer(rutas)
    if not libros:
        print("Ninguna captura se pudo leer.", file=sys.stderr)
        return 1

    historia = fusionar(libros)
    ident = historia.ident
    piso = parsear_tasa(args.piso)

    print(f"Subasta {ident} — {len(rutas)} captura(s), {len(historia.ofertas)} oferta(s)\n")

    print("LINEA DE TIEMPO")
    for o in historia.ofertas:
        quien = "vos" if o.agente == args.agente else f"ag {o.agente}"
        marca = "  (dada de baja)" if o.id in historia.retiradas else ""
        print(f"  {o.ingreso}  {quien:>8}  {formatear_tasa(o.tasa):>8}   "
              f"oferta {o.id}{marca}")

    print("\nTIEMPOS DE RESPUESTA")
    hubo_reaccion = False
    for ag in [args.agente] + sorted({o.agente for o in historia.ofertas
                                      if o.agente != args.agente}):
        rs = reacciones(historia, ag)
        if not rs:
            continue
        hubo_reaccion = True
        quien = "vos" if ag == args.agente else f"agente {ag}"
        veredicto, porque = clasificar([r.demora_s for r in rs])
        detalle = ", ".join(
            f"{r.demora_s:.0f}s bajando {formatear_tasa(r.recorte)}" for r in rs
        )
        print(f"  {quien}: {detalle}")
        print(f"    -> {veredicto} ({porque})")
    if not hubo_reaccion:
        print("  ninguna: en estas capturas nadie contesto la punta de otro.")

    cfg = ConfigSubasta(
        ident=ident,
        piso=piso,
        decremento_min=parsear_tasa(args.decremento_min),
        decremento_max=parsear_tasa(args.decremento_max),
    )

    print(f"\nQUE HABRIA HECHO EL BOT (piso {formatear_tasa(piso)})")
    azar = random.Random(args.semilla)
    # Se replica sobre las capturas reales y no sobre prefijos de la union: la
    # union contiene ofertas que en su momento ya estaban dadas de baja, y
    # decidir sobre un libro que nunca existio no dice nada.
    for lib in historia.libros:
        # Las capturas de subastas cerradas pierden el link de baja, asi que
        # aca la propiedad se resuelve por numero de agente. En vivo el bot usa
        # el link, que es mas preciso.
        visto = Libro(ident=lib.ident, ofertas=tuple(
            Oferta(id=o.id, agente=o.agente, tasa=o.tasa, ingreso=o.ingreso,
                   propia=(o.agente == args.agente))
            for o in lib.ofertas
        ))
        d = decidir(visto, cfg, azar)
        momento = max((o.ingreso for o in lib.ofertas), default=None)
        etiqueta = f"libro al {momento}" if momento else "libro vacio"
        if d.accion is Accion.RECOTIZAR:
            print(f"  {etiqueta}: cotizar {formatear_tasa(d.tasa)} — {d.motivo}")
        else:
            print(f"  {etiqueta}: {d.accion.value} — {d.motivo}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
