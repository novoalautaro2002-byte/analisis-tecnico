#!/usr/bin/env python3
"""Captura read-only de pantallas de la Plataforma Trading MAV.

Se engancha por CDP a un Chrome que vos ya abriste y logueaste a mano, y guarda
un snapshot del HTML cada vez que la pantalla cambia.

Lo unico que hace contra la plataforma es leer el DOM. No navega, no hace clic,
no escribe en ningun campo y no manda ninguna orden. Eso no es una promesa del
README: es que no hay una sola llamada en este archivo que pueda hacerlo.

Ver README.md para el paso a paso en Windows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - mensaje para el usuario, no logica
    sys.exit("Falta Playwright. Instalalo con:  pip install playwright")

DOMINIO = "mav-sa.com.ar"
CDP_POR_DEFECTO = "http://localhost:9222"

# Ritmo de sondeo. Arranca lento; cuando el libro se mueve, acelera un rato y
# despues vuelve a bajar. Sondear mas rapido que esto no agrega informacion
# (el servidor no actualiza mas seguido) y solo te hace visible en sus logs.
INTERVALO_TRANQUILO_S = 5.0
INTERVALO_ACTIVO_S = 2.0
SEGUIR_ACTIVO_S = 120.0

_SCRIPTS = re.compile(r"<script[\s\S]*?</script>", re.I)
_ESTILOS = re.compile(r"<style[\s\S]*?</style>", re.I)
_ENTRE_TAGS = re.compile(r">\s+<")
_ESPACIOS = re.compile(r"\s+")


def huella(html: str) -> str:
    """Hash de lo que importa: sin scripts, sin estilos, sin espacios de mas.

    Sirve para no guardar cien veces la misma pantalla. Los horarios de las
    puntas SI entran en el hash, porque un cambio de hora es un cambio real
    del libro y es justamente lo que queremos ver.

    El espaciado se normaliza en dos pasos: se tira el que esta entre tags
    (indentacion del template, que no significa nada) y se colapsan las
    corridas dentro del texto. Sacarlo del todo pegaria celdas vecinas y dos
    pantallas distintas podrian dar la misma huella.
    """
    limpio = _ESTILOS.sub("", _SCRIPTS.sub("", html))
    limpio = _ESPACIOS.sub(" ", _ENTRE_TAGS.sub("><", limpio))
    return hashlib.sha256(limpio.strip().encode("utf-8")).hexdigest()


def nombre_corto(url: str) -> str:
    """Un identificador de pantalla legible, sacado del .r que la sirve."""
    ultimo = url.rstrip("/").split("/")[-1] or "pantalla"
    limpio = re.sub(r"[^A-Za-z0-9_.-]", "_", ultimo.split("?")[0])
    return limpio[:60] or "pantalla"


class Captura:
    """Acumula los snapshots de una corrida y los escribe a disco."""

    def __init__(self, destino: Path, tope_mb: float):
        self.destino = destino
        self.tope_bytes = int(tope_mb * 1024 * 1024)
        self.bytes_escritos = 0
        self.vistos: dict[str, str] = {}  # url -> huella del ultimo snapshot
        self.n = 0
        destino.mkdir(parents=True, exist_ok=True)
        self.indice = destino / "indice.jsonl"

    @property
    def lleno(self) -> bool:
        return self.bytes_escritos >= self.tope_bytes

    def guardar(self, url: str, html: str) -> bool:
        """Guarda el snapshot si cambio respecto del anterior de esa pantalla."""
        h = huella(html)
        if self.vistos.get(url) == h:
            return False

        self.n += 1
        ahora = datetime.now(timezone.utc).astimezone()
        archivo = f"{self.n:05d}_{ahora.strftime('%H%M%S')}_{nombre_corto(url)}.html"
        datos = html.encode("utf-8")
        (self.destino / archivo).write_bytes(datos)

        with self.indice.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "n": self.n,
                "t": ahora.isoformat(),
                "url": url,
                "archivo": archivo,
                "huella": h,
                "bytes": len(datos),
            }, ensure_ascii=False) + "\n")

        self.vistos[url] = h
        self.bytes_escritos += len(datos)
        return True


def marcos_mav(navegador, filtro: str | None):
    """Todos los frames de la plataforma que haya abiertos.

    La plataforma es un frameset: lo que ves en pantalla son cinco documentos
    (cabecera, los dos laterales, el pie, y el contenido en FS_main). El
    documento de la pestaña es solo el <frameset>, que no cambia nunca; lo que
    hay que leer es cada frame por separado.

    Se recorre en cada vuelta y no se cachea: la plataforma se auto-refresca y
    vos vas a ir navegando entre pantallas mientras esto corre.
    """
    encontrados = []
    for contexto in navegador.contexts:
        for pagina in contexto.pages:
            try:
                marcos = list(pagina.frames)
            except Exception:
                continue
            for marco in marcos:
                try:
                    url = marco.url
                except Exception:
                    continue
                if DOMINIO not in url:
                    continue  # jamas tocamos algo que no sea de la plataforma
                if filtro and filtro.lower() not in url.lower():
                    continue
                encontrados.append((marco, url))
    return encontrados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cdp", default=CDP_POR_DEFECTO, help=f"Endpoint del Chrome con debug (default {CDP_POR_DEFECTO})")
    ap.add_argument("--salida", default="capturas", help="Carpeta donde dejar los snapshots")
    ap.add_argument("--filtro", default=None, help="Capturar solo URLs que contengan este texto (ej: subasta)")
    ap.add_argument("--once", action="store_true", help="Un solo snapshot de lo que este abierto y salir")
    ap.add_argument("--tope-mb", type=float, default=500.0, help="Cortar al llegar a este tamaño total")
    args = ap.parse_args()

    sesion = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = Path(args.salida) / sesion
    captura = Captura(destino, args.tope_mb)

    with sync_playwright() as pw:
        try:
            navegador = pw.chromium.connect_over_cdp(args.cdp)
        except Exception as e:
            print(f"No me pude enganchar a Chrome en {args.cdp}.", file=sys.stderr)
            print("Arrancalo con --remote-debugging-port=9222 (ver README.md).", file=sys.stderr)
            print(f"Detalle: {e}", file=sys.stderr)
            return 1

        print(f"Enganchado. Guardando en: {destino.resolve()}")
        if args.once:
            for marco, url in marcos_mav(navegador, args.filtro):
                try:
                    html = marco.content()
                except Exception:
                    continue
                if captura.guardar(url, html):
                    print(f"  guardado  {url}")
            print(f"Listo. {captura.n} snapshot(s).")
            return 0

        abiertas = marcos_mav(navegador, args.filtro)
        if not abiertas:
            print("Ojo: no hay ninguna pestaña de la plataforma abierta todavia.")
            print("Abrila y logueate; esto la va a tomar sola cuando aparezca.")
        print("Capturando. Ctrl+C para cortar.\n")

        ultimo_cambio = 0.0
        try:
            while not captura.lleno:
                hubo_cambio = False
                for marco, url in marcos_mav(navegador, args.filtro):
                    try:
                        html = marco.content()
                    except Exception:
                        continue  # el frame estaba navegando o se auto-refresco
                    if captura.guardar(url, html):
                        hubo_cambio = True
                        print(f"  [{captura.n:05d}] {datetime.now():%H:%M:%S}  {nombre_corto(url)}")

                if hubo_cambio:
                    ultimo_cambio = time.monotonic()
                caliente = (time.monotonic() - ultimo_cambio) < SEGUIR_ACTIVO_S
                time.sleep(INTERVALO_ACTIVO_S if caliente else INTERVALO_TRANQUILO_S)
        except KeyboardInterrupt:
            print("\nCortado a mano.")

        if captura.lleno:
            print(f"\nLlegue al tope de {args.tope_mb} MB y pare.")

    print(f"\n{captura.n} snapshot(s) en {destino.resolve()}")
    print("Antes de compartir nada, pasalo por scrub.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
