#!/usr/bin/env python3
"""Interfaz del bot.

    python ui.py

Levanta un servidor en tu maquina y abre el navegador. Solo biblioteca estandar:
no hay nada que instalar.

No maneja ningun navegador: le habla directo a la plataforma. El ingreso se hace
en esta misma pantalla, con el 2FA de siempre. La contraseña y el codigo viven
en memoria el tiempo que dura el pedido y no se escriben en ningun archivo ni en
el log.

El servidor escucha solo en 127.0.0.1: desde esta pantalla se lanzan ordenes
reales, no tiene por que llegarle nadie de afuera.
"""

from __future__ import annotations

import json
import queue
import threading
import time as reloj
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from motor.ciclo_http import Ciclo
from motor.config import ConfigInvalida, ConfigSubasta
from motor.formulario import CampoProhibido, FormularioIlegible
from motor.libro import LibroIlegible, formatear_tasa, parsear_libro, parsear_tasa
from motor.registro import Registro
from motor.sesion import (
    ErrorDePlataforma,
    IngresoRechazado,
    Sesion,
    SesionCaida,
)

AQUI = Path(__file__).parent
PUERTO = 8733


class Trabajador(threading.Thread):
    """El hilo que habla con la plataforma."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.ordenes: queue.Queue = queue.Queue()
        self.candado = threading.Lock()
        self.sesion: Sesion | None = None
        self.ciclo: Ciclo | None = None
        self.log: Registro | None = None
        self.salir = False
        self.eco = True          # los tests lo apagan
        self.estado = {
            "sesion": False,
            "pendiente": [],
            "corriendo": False,
            "modo": None,
            "subasta": None,
            "libro": None,
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
        while not self.salir:
            self._atender()
            if self.ciclo is not None and self.estado["corriendo"]:
                try:
                    self._una_vuelta()
                except Exception as e:
                    # Cualquier sorpresa frena las cotizaciones: es preferible
                    # quedarse quieto a seguir con un estado que no entendemos.
                    self._frenar(f"error inesperado: {e}")
            else:
                reloj.sleep(0.3)

    def _atender(self) -> None:
        while True:
            try:
                orden, datos = self.ordenes.get_nowait()
            except queue.Empty:
                return
            try:
                if orden == "ingresar":
                    self._ingresar(datos.get("usuario", ""), datos.get("clave", ""))
                elif orden == "codigo":
                    self._codigo(datos.get("valores") or {})
                elif orden == "sesion":
                    self._conectar(datos.get("cookie", ""))
                elif orden == "detalle":
                    self._detalle(int(datos.get("ident") or 0))
                elif orden == "arrancar":
                    self._arrancar(datos)
                elif orden == "parar":
                    self._frenar("parado a mano")
            except (ConfigInvalida, LibroIlegible, ErrorDePlataforma, SesionCaida,
                    FormularioIlegible, CampoProhibido, IngresoRechazado) as e:
                self._set(aviso=str(e))
            except Exception as e:
                self._set(aviso=f"error: {e}")

    def _ingresar(self, usuario: str, clave: str) -> None:
        """Usuario y contraseña.

        Ni la clave ni el codigo entran nunca al estado ni al log: viven en la
        llamada y se van con ella.
        """
        # Si no entiende la respuesta, la guarda en logs/ para poder mirarla.
        sesion = Sesion(guardar_en=AQUI / "logs" / "x")
        pendiente = sesion.ingresar(usuario, clave)
        self.sesion = sesion
        if pendiente is None:
            self._set(sesion=True, pendiente=[], aviso="Sesión iniciada.")
        else:
            self._set(sesion=False, pendiente=list(pendiente.campos),
                      aviso=pendiente.mensaje)

    def _codigo(self, valores: dict) -> None:
        if self.sesion is None:
            self._set(aviso="Primero ingresá usuario y contraseña.")
            return
        pendiente = self.sesion.continuar({k: str(v) for k, v in valores.items()})
        if pendiente is None:
            self._set(sesion=True, pendiente=[], aviso="Sesión iniciada.")
        else:
            self._set(pendiente=list(pendiente.campos), aviso=pendiente.mensaje)

    def _conectar(self, texto: str) -> None:
        sesion = Sesion.desde_texto(texto)
        # Se prueba antes de darla por buena: una cookie vencida que parece
        # valida es peor que no tener ninguna.
        sesion.get("cpd-subastas-listado.r")
        self.sesion = sesion
        self._set(sesion=True, aviso="Sesión OK.")

    def _detalle(self, ident: int) -> None:
        if self.sesion is None:
            self._set(aviso="Primero ingresá a la plataforma.")
            return
        libro = parsear_libro(self.sesion.subasta(ident))
        self._set(subasta=ident, libro=self._libro_json(libro), aviso=None)

    def _arrancar(self, datos: dict) -> None:
        if self.sesion is None:
            self._set(aviso="Primero ingresá a la plataforma.")
            return
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
        self.ciclo = Ciclo(cfg, self.sesion, vivo=vivo, log=self.log)
        self.log("arranque", f"arranco en modo {'VIVO' if vivo else 'SOMBRA'}",
                 config=str(cfg))
        self._set(corriendo=True, modo="vivo" if vivo else "sombra",
                  subasta=cfg.ident, recotizaciones=0, aviso=None)

    def _frenar(self, motivo: str) -> None:
        if self.ciclo is not None:
            self.ciclo.parar()
        if self.log is not None:
            self.log("parada", motivo)
        self._set(corriendo=False, aviso=motivo)

    def _una_vuelta(self) -> None:
        paso = self.ciclo.tick()
        datos = {"recotizaciones": self.ciclo.estado.recotizaciones.get(
            self.ciclo.cfg.ident, 0)}
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
            self._responder((AQUI / "ui" / "index.html").read_bytes(),
                            "text/html; charset=utf-8")
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

        rutas = {"/api/ingresar": "ingresar", "/api/codigo": "codigo",
                 "/api/sesion": "sesion", "/api/detalle": "detalle",
                 "/api/arrancar": "arrancar", "/api/parar": "parar"}
        orden = rutas.get(self.path)
        if orden is None:
            return self._json({"error": "no existe"}, 404)
        TRABAJADOR.pedir(orden, **datos)
        self._json({"ok": True})


def main() -> int:
    TRABAJADOR.start()
    # Solo loopback: la interfaz manda ordenes reales.
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
