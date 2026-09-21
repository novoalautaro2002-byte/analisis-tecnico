#!/usr/bin/env python3
"""Anonimiza los snapshots capturados antes de compartirlos.

Reemplaza nombres y numeros de comitente por placeholders estables (el mismo
comitente es siempre COMITENTE_3 en las 200 capturas), tapa CUITs y los valores
de campos hidden que tengan pinta de token de sesion.

Lo que NO toca, a proposito:
  - Los nombres de los campos hidden. Esa es justamente la estructura del
    formulario, que es lo que hay que leer para escribir el bot.
  - Los numeros de agente de las contrapartes. Es informacion de mercado que
    ves en pantalla, y hace falta para clasificar quien es bot y quien no.
  - Las tasas y los horarios de las puntas. Son el dato.

Uso:
    python scrub.py capturas/20260921_143000
    python scrub.py capturas/20260921_143000 --terminos terminos.txt

Formato de terminos.txt (uno por linea, # para comentarios):
    Juan Perez S.A.
    30-12345678-9
    ACME SRL => COMITENTE_ACME
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# CUIT / CUIL. Se tapa siempre, no hace falta listarlo a mano.
CUIT = re.compile(r"\b(\d{2})-?(\d{8})-?(\d)\b")

# Valor largo y opaco dentro de un campo hidden: casi seguro estado de sesion.
HIDDEN_TOKEN = re.compile(
    r'(<input[^>]*type=["\']?hidden["\']?[^>]*value=["\'])([A-Za-z0-9+/=_-]{16,})(["\'])',
    re.I,
)


def cargar_terminos(ruta: Path | None) -> list[tuple[str, str | None]]:
    """Lee terminos.txt. Cada linea es un termino, o 'termino => PLACEHOLDER'."""
    if ruta is None:
        return []
    if not ruta.exists():
        sys.exit(f"No encuentro {ruta}")

    terminos: list[tuple[str, str | None]] = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if "=>" in linea:
            izq, der = linea.split("=>", 1)
            terminos.append((izq.strip(), der.strip()))
        else:
            terminos.append((linea, None))
    return terminos


def revisar_terminos(terminos: list[tuple[str, str | None]], forzar: bool) -> None:
    """Un numero corto como termino reemplaza tasas y nominales por accidente."""
    riesgosos = [t for t, _ in terminos if t.isdigit() and len(t) < 5]
    if riesgosos and not forzar:
        print("Estos terminos son numeros cortos y van a pisar tasas o nominales:")
        for t in riesgosos:
            print(f"  {t}")
        print("\nUsa la forma larga (el nombre completo del comitente), o --forzar si estas seguro.")
        sys.exit(1)


def construir_reemplazos(terminos: list[tuple[str, str | None]]) -> list[tuple[re.Pattern, str]]:
    """Un patron por termino, del mas largo al mas corto.

    El orden importa: si 'ACME' se reemplazara antes que 'ACME SRL', la segunda
    nunca matchearia.
    """
    reemplazos = []
    for i, (termino, alias) in enumerate(sorted(terminos, key=lambda x: -len(x[0])), start=1):
        destino = alias or f"COMITENTE_{i}"
        reemplazos.append((re.compile(re.escape(termino), re.I), destino))
    return reemplazos


def limpiar(html: str, reemplazos: list[tuple[re.Pattern, str]]) -> tuple[str, dict[str, int]]:
    cuenta: dict[str, int] = {}

    for patron, destino in reemplazos:
        html, n = patron.subn(destino, html)
        if n:
            cuenta[destino] = cuenta.get(destino, 0) + n

    html, n = CUIT.subn("XX-XXXXXXXX-X", html)
    if n:
        cuenta["CUIT"] = n

    html, n = HIDDEN_TOKEN.subn(r"\1TOKEN\3", html)
    if n:
        cuenta["TOKEN"] = n

    return html, cuenta


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("carpeta", help="Carpeta de capturas a limpiar")
    ap.add_argument("--terminos", default=None, help="Archivo con nombres/numeros a reemplazar")
    ap.add_argument("--forzar", action="store_true", help="Permitir terminos numericos cortos")
    args = ap.parse_args()

    origen = Path(args.carpeta)
    if not origen.is_dir():
        sys.exit(f"No encuentro la carpeta {origen}")
    destino = origen.parent / f"{origen.name}_limpio"
    destino.mkdir(parents=True, exist_ok=True)

    terminos = cargar_terminos(Path(args.terminos) if args.terminos else None)
    revisar_terminos(terminos, args.forzar)
    reemplazos = construir_reemplazos(terminos)

    if not terminos:
        print("Sin terminos.txt: solo tapo CUITs y tokens.")
        print("Si en las pantallas aparecen nombres de comitente, listalos ahi.\n")

    total: dict[str, int] = {}
    archivos = 0
    for origen_html in sorted(origen.glob("*.html")):
        limpio, cuenta = limpiar(origen_html.read_text(encoding="utf-8"), reemplazos)
        (destino / origen_html.name).write_text(limpio, encoding="utf-8")
        for k, v in cuenta.items():
            total[k] = total.get(k, 0) + v
        archivos += 1

    indice = origen / "indice.jsonl"
    if indice.exists():
        (destino / "indice.jsonl").write_text(indice.read_text(encoding="utf-8"), encoding="utf-8")

    # El mapeo queda local y no se comparte: es la llave para des-anonimizar.
    if reemplazos:
        mapeo = {destino_: patron.pattern for patron, destino_ in reemplazos}
        (origen / "MAPEO_NO_COMPARTIR.json").write_text(
            json.dumps(mapeo, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"{archivos} archivo(s) -> {destino.resolve()}")
    if total:
        print("\nReemplazos:")
        for k, v in sorted(total.items(), key=lambda x: -x[1]):
            print(f"  {k:24s} {v}")
    else:
        print("\nNo reemplace nada. Si esperabas que si, revisa terminos.txt.")

    print("\nAbri un par de archivos de la carpeta _limpio y verifica antes de mandarlos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
