#!/usr/bin/env python3
"""Cuida una subasta desde la consola.

Si preferis botones, corre `python ui.py` en vez de esto: hace lo mismo con una
pantalla.

Arranca SIEMPRE en modo sombra: mira, decide y anota lo que haria, sin tocar
nada. Para que actue de verdad hay que pasar --vivo a proposito.

    python bot.py --ident 1556714 --piso 25,00
    python bot.py --ident 1556714 --piso 25,00 --espera-min 20 --espera-max 60 --vivo
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from motor.ciclo import Ciclo, Resultado
from motor.config import ConfigInvalida, ConfigSubasta
from motor.libro import LibroIlegible, formatear_tasa, parsear_tasa
from motor.registro import Registro

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("Falta Playwright. Instalalo con:  pip install playwright")

CDP_POR_DEFECTO = "http://localhost:9222"


def agregar_parametros(ap: argparse.ArgumentParser) -> None:
    """Los mismos parametros que ofrece la interfaz."""
    ap.add_argument("--ident", type=int, required=True, help="Numero de subasta")
    ap.add_argument("--piso", required=True, help="Tasa minima aceptable, con coma")
    ap.add_argument("--decremento-min", default="0,01")
    ap.add_argument("--decremento-max", default="0,01")
    ap.add_argument("--prob", type=float, default=1.0,
                    help="Probabilidad de contestar cada vuelta")
    ap.add_argument("--espera-min", type=float, default=0.0,
                    help="Demora minima antes de recotizar, en segundos")
    ap.add_argument("--espera-max", type=float, default=0.0)
    ap.add_argument("--max-recotizaciones", type=int, default=60)
    ap.add_argument("--intervalo-min", type=float, default=10.0)


def armar_config(args) -> ConfigSubasta:
    return ConfigSubasta(
        ident=args.ident,
        piso=parsear_tasa(args.piso),
        decremento_min=parsear_tasa(args.decremento_min),
        decremento_max=parsear_tasa(args.decremento_max),
        prob_respuesta=args.prob,
        espera_min_s=args.espera_min,
        espera_max_s=args.espera_max,
        max_recotizaciones=args.max_recotizaciones,
        intervalo_min_s=args.intervalo_min,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    agregar_parametros(ap)
    ap.add_argument("--cdp", default=CDP_POR_DEFECTO)
    ap.add_argument("--log", default=None)
    ap.add_argument("--vivo", action="store_true",
                    help="Cotizar de verdad. Sin esto solo mira y anota.")
    args = ap.parse_args()

    try:
        cfg = armar_config(args)
    except (ConfigInvalida, LibroIlegible) as e:
        print(f"Configuracion invalida: {e}", file=sys.stderr)
        return 1

    ruta = Path(args.log or f"logs/subasta_{args.ident}_"
                            f"{datetime.now():%Y%m%d_%H%M%S}.jsonl")
    log = Registro(ruta)
    ciclo = Ciclo(cfg, vivo=args.vivo, log=log)

    modo = "VIVO" if args.vivo else "SOMBRA"
    print(f"Subasta {cfg.ident} | piso {formatear_tasa(cfg.piso)} | modo {modo}")
    if not args.vivo:
        print("Solo mira y anota. Para que cotice de verdad: --vivo")
    print(f"Log: {ruta}\nCtrl+C para cortar.\n")
    log("arranque", modo=modo, config=str(cfg))

    with sync_playwright() as pw:
        try:
            navegador = pw.chromium.connect_over_cdp(args.cdp)
        except Exception as e:
            print(f"No me pude enganchar a Chrome en {args.cdp}: {e}", file=sys.stderr)
            return 1

        try:
            while True:
                paso = ciclo.tick(navegador)
                if paso.resultado is Resultado.SIN_PANTALLA:
                    log("sin_pantalla", paso.detalle)
                if paso.terminal:
                    print(f"\n{paso.resultado.value}: {paso.detalle}")
                    break
                ciclo.dormir(ciclo.pausa_sugerida)
        except KeyboardInterrupt:
            ciclo.parar()
            log("kill", "cortado a mano")

    print(f"\nListo. El detalle quedo en {ruta}")
    log.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
