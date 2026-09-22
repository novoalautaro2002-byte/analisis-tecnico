#!/usr/bin/env python3
"""Interfaz del bot.

    python ui.py

Levanta un servidor en tu máquina y abre el navegador. Solo biblioteca
estándar: no hay nada que instalar.

No maneja ningún navegador: le habla directo a la plataforma. El ingreso se hace
en esta misma pantalla, con el 2FA de siempre. La contraseña y el código viven
en memoria el tiempo que dura el pedido y no se escriben en ningún archivo ni en
el log.

El servidor escucha solo en 127.0.0.1: desde esta pantalla se lanzan órdenes
reales, no tiene por qué llegarle nadie de afuera.
"""

from __future__ import annotations

import json
import queue
import threading
import time as reloj
import webbrowser
from dataclasses import asdict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from motor.config import ConfigInvalida, ConfigSubasta
from motor.ficha import leer_ficha
from motor.formulario import CampoProhibido, FormularioIlegible, parsear_campos
from motor.libro import LibroIlegible, formatear_tasa, parsear_libro, parsear_tasa
from motor.mesa import Mesa
from motor.registro import Registro
from motor.sesion import ErrorDePlataforma, IngresoRechazado, Sesion, SesionCaida

AQUI = Path(__file__).parent
PUERTO = 8733


def version() -> str:
    """Fecha del archivo mas nuevo del bot.

    Existe porque la pregunta "¿estas corriendo la version con el arreglo?" nos
    costo una vuelta entera. Sale de los archivos, asi que se actualiza sola con
    cada ACTUALIZAR.bat y no depende de que alguien se acuerde de tocar nada.
    """
    try:
        archivos = [*AQUI.glob("*.py"), *AQUI.glob("motor/*.py"),
                    *AQUI.glob("ui/*.html")]
        return f"{datetime.fromtimestamp(max(a.stat().st_mtime for a in archivos)):%d/%m %H:%M}"
    except (ValueError, OSError):
        return "?"

# En MAV se opera por agente y el del trader no cambia nunca, asi que viene
# puesto. Se puede editar en pantalla si hace falta.
AGENTE_POR_DEFECTO = "442"


class Trabajador(threading.Thread):
    """El hilo que habla con la plataforma."""

    daemon = True

    def __init__(self):
        super().__init__()
        self.ordenes: queue.Queue = queue.Queue()
        self.candado = threading.Lock()
        self.sesion: Sesion | None = None
        self.mesa: Mesa | None = None
        self.log: Registro | None = None
        self.salir = False
        self.eco = True          # los tests lo apagan
        self.agente = AGENTE_POR_DEFECTO   # constante del trader
        self.estado = {
            "sesion": False,
            "agente": AGENTE_POR_DEFECTO,
            "pendiente": [],
            "subastas": [],
            "mirado": None,
            "aviso": None,
            "log": [],
            "version": version(),
        }

    # -- lo que ve la interfaz --------------------------------------------

    def leer_estado(self) -> dict:
        with self.candado:
            e = dict(self.estado)
        if self.mesa is not None:
            e["subastas"] = [asdict(v) for v in self.mesa.vistas()]
        if self.log is not None:
            e["log"] = list(self.log.recientes)[-80:]
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
            if self.mesa is not None and self.mesa.activos:
                if not self.mesa.tick():
                    reloj.sleep(self.mesa.dormir_hasta())
            else:
                reloj.sleep(0.2)

    def _atender(self) -> None:
        while True:
            try:
                orden, datos = self.ordenes.get_nowait()
            except queue.Empty:
                return
            try:
                self._despachar(orden, datos)
            except (ConfigInvalida, LibroIlegible, ErrorDePlataforma, SesionCaida,
                    FormularioIlegible, CampoProhibido, IngresoRechazado) as e:
                self._set(aviso=str(e))
            except Exception as e:
                self._set(aviso=f"error: {e}")

    def _despachar(self, orden: str, datos: dict) -> None:
        if orden == "agente":
            self._agente(str(datos.get("agente", "")))
        elif orden == "ingresar":
            self._ingresar(datos.get("usuario", ""), datos.get("clave", ""))
        elif orden == "codigo":
            self._codigo(datos.get("valores") or {})
        elif orden == "mirar":
            self._mirar(int(datos.get("ident") or 0))
        elif orden == "sumar":
            self._sumar(datos)
        elif orden == "editar":
            self._editar(datos)
        elif orden == "manual":
            self._manual(datos)
        elif orden == "sacar":
            self._exige_mesa().sacar(int(datos["ident"]))
        elif orden == "parar":
            self._exige_mesa().parar_todo()
            self._set(aviso="Parado.")
        elif orden == "diagnostico":
            self._diagnostico(int(datos.get("ident") or 0))

    def _agente(self, agente: str) -> None:
        """En MAV se opera por agente, no por usuario: define qué oferta es tuya."""
        self.agente = agente.strip()
        self._set(agente=self.agente,
                  aviso=f"Operando como agente {self.agente}."
                        if self.agente else "Falta tu número de agente.")

    def _exige_agente(self) -> str:
        if not self.agente:
            raise ErrorDePlataforma("Primero poné tu número de agente.")
        return self.agente

    # -- sesión ------------------------------------------------------------

    def _ingresar(self, usuario: str, clave: str) -> None:
        """Ni la clave ni el código entran al estado ni al log."""
        sesion = Sesion(guardar_en=AQUI / "logs" / "x")
        pendiente = sesion.ingresar(usuario, clave)
        self.sesion = sesion
        self._tras_ingreso(pendiente)

    def _codigo(self, valores: dict) -> None:
        if self.sesion is None:
            self._set(aviso="Primero ingresá usuario y contraseña.")
            return
        self._tras_ingreso(
            self.sesion.continuar({k: str(v) for k, v in valores.items()}))

    def _tras_ingreso(self, pendiente) -> None:
        if pendiente is not None:
            self._set(sesion=False, pendiente=list(pendiente.campos),
                      aviso=pendiente.mensaje)
            return
        ruta = AQUI / "logs" / f"mesa_{datetime.now():%Y%m%d_%H%M%S}.jsonl"
        self.log = Registro(ruta, eco=self.eco)
        self.mesa = Mesa(self.sesion, self.log)
        self._set(sesion=True, pendiente=[], aviso="Sesión iniciada.")

    def _exige_sesion(self) -> Sesion:
        if self.sesion is None:
            raise ErrorDePlataforma("Primero ingresá a la plataforma.")
        return self.sesion

    def _exige_mesa(self) -> Mesa:
        self._exige_sesion()
        if self.mesa is None:
            raise ErrorDePlataforma("Primero ingresá a la plataforma.")
        return self.mesa

    # -- subastas ----------------------------------------------------------

    def _mirar(self, ident: int) -> None:
        """Una lectura suelta, para ver cómo está parada la puja."""
        sesion = self._exige_sesion()
        libro = parsear_libro(sesion.subasta(ident), self._exige_agente())
        mia = libro.mejor_propia()
        ajena = libro.mejor_ajena()
        ficha = leer_ficha(sesion.estado_subasta(ident) or {})
        self._set(mirado={
            "ident": ident,
            "estado": (ficha.estado or None) if ficha else None,
            "segmento": (ficha.segmento or None) if ficha else None,
            "tmin": (ficha.tiempo_minimo or None) if ficha else None,
            "cierre": (ficha.hora_cierre or None) if ficha else None,
            "cheques": ficha.cantidad_cheques if ficha else None,
            "agente_vdr": (ficha.agente_vdr or None) if ficha else None,
            "tasa_vdr": (formatear_tasa(ficha.tasa_vdr)
                         if ficha and ficha.tasa_vdr is not None else None),
            "mia": formatear_tasa(mia.tasa) if mia else None,
            "mejor_ajena": formatear_tasa(ajena.tasa) if ajena else None,
            "gano": bool(mia and (not ajena or mia.tasa < ajena.tasa)),
            "ofertas": [
                {"id": o.id, "agente": o.agente, "tasa": formatear_tasa(o.tasa),
                 "hora": o.ingreso.strftime("%H:%M:%S"), "propia": o.propia}
                for o in sorted(libro.ofertas, key=lambda o: (o.tasa, o.ingreso))
            ],
        }, aviso=None)

    def _sumar(self, datos: dict) -> None:
        mesa = self._exige_mesa()
        mesa.sumar(self._armar_config(datos), vivo=bool(datos.get("vivo")))
        self._set(aviso=None)

    def _armar_config(self, datos: dict) -> ConfigSubasta:
        return ConfigSubasta(
            ident=int(datos["ident"]),
            mi_agente=self._exige_agente(),
            piso=parsear_tasa(datos["piso"]),
            decremento_min=parsear_tasa(datos["decremento_min"]),
            decremento_max=parsear_tasa(datos["decremento_max"]),
            prob_respuesta=float(datos["prob"]),
            espera_min_s=float(datos["espera_min"]),
            espera_max_s=float(datos["espera_max"]),
            sondeo_s=float(datos["sondeo"]),
            max_recotizaciones=int(datos["max_recotizaciones"]),
        )

    def _editar(self, datos: dict) -> None:
        """Cambia las condiciones sin sacar la subasta de la mesa.

        Sacar y volver a sumar dejaba a la subasta sin defensa el rato que
        tardaba en ponerse al día, y borraba la cuenta de recotizaciones.
        """
        mesa = self._exige_mesa()
        mesa.reconfigurar(self._armar_config(datos))
        self._set(aviso=f"Subasta {datos['ident']}: condiciones cambiadas.")

    def _manual(self, datos: dict) -> None:
        """Una tasa puesta por el trader, mandada por el camino del bot."""
        mesa = self._exige_mesa()
        ident = int(datos["ident"])
        texto = mesa.cargar_a_mano(ident, parsear_tasa(datos["tasa"]))
        self._set(aviso=f"Subasta {ident}: cargada tu tasa {texto}.")

    # -- diagnóstico -------------------------------------------------------

    def _diagnostico(self, ident: int) -> None:
        """Guarda lo que el bot ve de una subasta y resume su lectura.

        Existe porque la detección de ofertas propias depende de cómo MAV
        renderiza la columna de baja, y eso solo se corrige mirando el HTML de
        un caso real en vez de deducirlo.
        """
        sesion = self._exige_sesion()
        carpeta = AQUI / "logs"
        carpeta.mkdir(parents=True, exist_ok=True)
        sello = f"{ident}_{datetime.now():%Y%m%d_%H%M%S}"

        paginas = {
            "subasta": ("cpd-versubasta.r", lambda: sesion.subasta(ident)),
            "ofertas-compra": ("cpd-of-compra-i.r", lambda: sesion.ofertas_compra(ident)),
            "cheques": ("cpd-ch-subasta-i-v2.r", lambda: sesion.cheques(ident)),
            # El JSON del listado: hay que ver si trae el tiempo minimo y la
            # mejor oferta compradora de cada subasta. Si los trae, se puede
            # vigilar N subastas con un pedido en vez de N.
            "listado-json": ("cpd-subastas-api.p",
                             lambda: sesion.listado_crudo(**{"p-ident": ident})),
            "listado-activas-json": ("cpd-subastas-api.p",
                                     lambda: sesion.listado_crudo(
                                         **{"p-estado": "Activas"})),
        }
        guardados, filas = [], []
        for nombre, (_, traer) in paginas.items():
            try:
                html = traer()
            except (ErrorDePlataforma, SesionCaida) as e:
                filas.append(f"{nombre}: no se pudo leer ({e})")
                continue
            ext = "json" if nombre.endswith("json") else "html"
            ruta = carpeta / f"diag_{sello}_{nombre}.{ext}"
            ruta.write_text(html, encoding="latin-1", errors="replace")
            guardados.append(ruta.name)
            if nombre == "subasta":
                filas.extend(self._resumen_libro(html, self.agente))
            elif nombre == "listado-json":
                filas.append(self._resumen_json(html))

        self._set(mirado=None, aviso=" | ".join(filas) +
                  f"  →  guardado en logs/: {', '.join(guardados)}")

    @staticmethod
    def _resumen_json(crudo: str) -> str:
        """Que campos trae cada fila del listado, para saber que se puede usar."""
        try:
            filas = json.loads(crudo).get("work-json") or []
        except (json.JSONDecodeError, AttributeError):
            return "el listado no devolvio JSON"
        if not filas:
            return "el listado vino vacio"
        return "campos del listado: " + ", ".join(sorted(filas[0]))

    @staticmethod
    def _resumen_libro(html: str, agente: str = "") -> list[str]:
        try:
            libro = parsear_libro(html, agente)
        except LibroIlegible as e:
            return [f"no entiendo el libro: {e}"]
        campos = parsear_campos(html)
        filas = [f"subasta {libro.ident}, {len(libro.ofertas)} oferta(s)"]
        for o in libro.ofertas:
            filas.append(f"#{o.id} ag {o.agente} {formatear_tasa(o.tasa)} "
                         f"{'PROPIA' if o.propia else 'ajena'}")
        filas.append(f"campos del form: {len(campos)}")
        return filas


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

        orden = {"/api/ingresar": "ingresar", "/api/codigo": "codigo",
                 "/api/mirar": "mirar", "/api/sumar": "sumar",
                 "/api/sacar": "sacar", "/api/parar": "parar",
                 "/api/editar": "editar", "/api/manual": "manual",
                 "/api/agente": "agente",
                 "/api/diagnostico": "diagnostico"}.get(self.path)
        if orden is None:
            return self._json({"error": "no existe"}, 404)
        TRABAJADOR.pedir(orden, **datos)
        self._json({"ok": True})


def main() -> int:
    TRABAJADOR.start()
    # Solo loopback: la interfaz manda órdenes reales.
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
