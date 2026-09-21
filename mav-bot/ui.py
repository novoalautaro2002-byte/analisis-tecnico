#!/usr/bin/env python3
"""Interfaz del bot: una pantalla local con botones.

    python ui.py

Levanta un servidor en tu maquina y abre el navegador. Solo escucha en
127.0.0.1: nadie de afuera llega.

Playwright no es thread-safe, asi que toda la conversacion con Chrome vive en un
unico hilo y el servidor web le habla por una cola. El navegador consulta el
estado; nunca maneja a Chrome por su cuenta.
"""

from __future__ import annotations

import json
import queue
import sys
import threading
import time as reloj
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from motor.ciclo import Ciclo
from motor.config import ConfigInvalida, ConfigSubasta
from motor.libro import LibroIlegible, formatear_tasa, parsear_tasa
from motor.pantalla import buscar_marco, leer_libro
from motor.registro import Registro

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("Falta Playwright. Instalalo con:  pip install playwright")

AQUI = Path(__file__).parent
CDP = "http://localhost:9222"
PUERTO = 8733


class Trabajador(threading.Thread):
    """El unico hilo que habla con Chrome."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.ordenes: queue.Queue = queue.Queue()
        self.candado = threading.Lock()
        self.ciclo: Ciclo | None = None
        self.log: Registro | None = None
        self.salir = False
        self.eco = True          # los tests lo apagan
        self.estado = {
            "chrome": "conectando",
            "corriendo": False,
            "modo": None,
            "subasta": None,
            "libro": None,
            "ultimo": None,
            "recotizaciones": 0,
            "aviso": None,
            "log": [],
        }

    # -- lo que ve la interfaz --------------------------------------------

    def leer_estado(self) -> dict:
        with self.candado:
            e = dict(self.estado)
            if self.log is not None:
                e["log"] = list(self.log.recientes)[-60:]
            return e

    def _set(self, **kw) -> None:
        with self.candado:
            self.estado.update(kw)

    def pedir(self, orden: str, **datos) -> None:
        self.ordenes.put((orden, datos))

    # -- el hilo -----------------------------------------------------------

    def run(self) -> None:
        with sync_playwright() as pw:
            navegador = None
            proximo_intento = 0.0
            while not self.salir:
                if navegador is None and reloj.monotonic() >= proximo_intento:
                    try:
                        navegador = pw.chromium.connect_over_cdp(CDP)
                        self._set(chrome="ok")
                    except Exception:
                        self._set(chrome="sin conexion")
                        proximo_intento = reloj.monotonic() + 3.0

                self._atender(navegador)

                if navegador is not None and self.ciclo is not None and \
                        self.estado["corriendo"]:
                    try:
                        self._una_vuelta(navegador)
                    except Exception as e:
                        # Cualquier sorpresa frena las cotizaciones. Es preferible
                        # quedarse quieto a seguir con un estado que no entendemos.
                        self._frenar(f"error inesperado: {e}")
                        navegador = None
                        proximo_intento = reloj.monotonic() + 3.0
                else:
                    reloj.sleep(0.3)

    def _atender(self, navegador) -> None:
        while True:
            try:
                orden, datos = self.ordenes.get_nowait()
            except queue.Empty:
                return
            try:
                if orden == "detalle":
                    self._detalle(navegador, datos["ident"])
                elif orden == "arrancar":
                    self._arrancar(datos)
                elif orden == "parar":
                    self._frenar("parado a mano")
            except (ConfigInvalida, LibroIlegible) as e:
                self._set(aviso=str(e))
            except Exception as e:
                self._set(aviso=f"error: {e}")

    def _detalle(self, navegador, ident: int) -> None:
        if navegador is None:
            self._set(aviso="Chrome no esta enganchado todavia.")
            return
        _, marco = buscar_marco(navegador, ident)
        if marco is None:
            self._set(libro=None, subasta=ident,
                      aviso=f"No encuentro la subasta {ident} abierta en Chrome. "
                            f"Abrila ahi y volve a probar.")
            return
        libro = leer_libro(marco, self.log or (lambda *a, **k: None))
        if libro is None:
            self._set(libro=None, aviso="No pude leer esa pantalla.")
            return
        self._set(subasta=ident, libro=self._libro_json(libro), aviso=None)

    def _arrancar(self, datos: dict) -> None:
        cfg = ConfigSubasta(
            ident=int(datos["ident"]),
            piso=parsear_tasa(datos["piso"]),
            decremento_min=parsear_tasa(datos["decremento_min"]),
            decremento_max=parsear_tasa(datos["decremento_max"]),
            prob_respuesta=float(datos["prob"]),
            espera_min_s=float(datos["espera_min"]),
            espera_max_s=float(datos["espera_max"]),
            max_recotizaciones=int(datos["max_recotizaciones"]),
            intervalo_min_s=float(datos["intervalo_min"]),
        )
        vivo = bool(datos.get("vivo"))
        ruta = AQUI / "logs" / (f"subasta_{cfg.ident}_"
                                f"{datetime.now():%Y%m%d_%H%M%S}.jsonl")
        self.log = Registro(ruta, eco=self.eco)
        self.ciclo = Ciclo(cfg, vivo=vivo, log=self.log)
        self.log("arranque", f"arranco en modo {'VIVO' if vivo else 'SOMBRA'}",
                 config=str(cfg))
        self._set(corriendo=True, modo="vivo" if vivo else "sombra",
                  subasta=cfg.ident, recotizaciones=0, aviso=None,
                  ultimo=None)

    def _frenar(self, motivo: str) -> None:
        if self.ciclo is not None:
            self.ciclo.parar()
        if self.log is not None:
            self.log("parada", motivo)
        self._set(corriendo=False, aviso=motivo)

    def _una_vuelta(self, navegador) -> None:
        paso = self.ciclo.tick(navegador)
        datos = {
            "ultimo": {"resultado": paso.resultado.value, "detalle": paso.detalle,
                       "tasa": paso.tasa, "hora": f"{datetime.now():%H:%M:%S}"},
            "recotizaciones": self.ciclo.estado.recotizaciones.get(
                self.ciclo.cfg.ident, 0),
        }
        if paso.libro is not None:
            datos["libro"] = self._libro_json(paso.libro)
        self._set(**datos)

        if paso.terminal:
            self._set(corriendo=False,
                      aviso=f"{paso.resultado.value}: {paso.detalle}")
            return
        self.ciclo.dormir(self.ciclo.pausa_sugerida)

    @staticmethod
    def _libro_json(libro) -> dict:
        mia = libro.mejor_propia()
        ajena = libro.mejor_ajena()
        return {
            "ident": libro.ident,
            "ofertas": [
                {"id": o.id, "agente": o.agente, "tasa": formatear_tasa(o.tasa),
                 "hora": o.ingreso.strftime("%H:%M:%S"), "propia": o.propia}
                for o in sorted(libro.ofertas, key=lambda o: (o.tasa, o.ingreso))
            ],
            "mia": formatear_tasa(mia.tasa) if mia else None,
            "mejor_ajena": formatear_tasa(ajena.tasa) if ajena else None,
            "gano": bool(mia and (not ajena or mia.tasa < ajena.tasa)),
        }


TRABAJADOR = Trabajador()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):   # el servidor no ensucia la consola
        pass

    def _responder(self, cuerpo: bytes, tipo: str, codigo: int = 200) -> None:
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _json(self, datos: dict, codigo: int = 200) -> None:
        self._responder(json.dumps(datos, ensure_ascii=False, default=str).encode(),
                        "application/json; charset=utf-8", codigo)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            pagina = (AQUI / "ui" / "index.html").read_bytes()
            self._responder(pagina, "text/html; charset=utf-8")
        elif self.path == "/api/estado":
            self._json(TRABAJADOR.leer_estado())
        else:
            self._json({"error": "no existe"}, 404)

    def do_POST(self):
        largo = int(self.headers.get("Content-Length") or 0)
        try:
            datos = json.loads(self.rfile.read(largo) or b"{}")
        except json.JSONDecodeError:
            return self._json({"error": "json invalido"}, 400)

        if self.path == "/api/detalle":
            TRABAJADOR.pedir("detalle", ident=int(datos.get("ident") or 0))
        elif self.path == "/api/arrancar":
            TRABAJADOR.pedir("arrancar", **datos)
        elif self.path == "/api/parar":
            TRABAJADOR.pedir("parar")
        else:
            return self._json({"error": "no existe"}, 404)
        self._json({"ok": True})


def main() -> int:
    TRABAJADOR.start()
    # Solo loopback: la interfaz maneja ordenes reales, no sale de la maquina.
    servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler)
    url = f"http://127.0.0.1:{PUERTO}/"
    print(f"Interfaz del bot en {url}")
    print("Dejá esta ventana abierta. Ctrl+C para cortar.\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nCortando.")
        TRABAJADOR.pedir("parar")
        TRABAJADOR.salir = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
