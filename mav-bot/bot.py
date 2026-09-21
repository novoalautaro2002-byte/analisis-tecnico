#!/usr/bin/env python3
"""Cuida una subasta: vigila el libro y recotiza cuando alguien te supera.

Arranca SIEMPRE en modo sombra: mira, decide y escribe en el log lo que haria,
pero no toca la pantalla. Para que actue de verdad hay que pasar --vivo a
proposito.

Vos cargas a mano el comitente y la primera oferta. El bot solo escribe el campo
de tasa y aprieta "Modificar Tasa Cpr.". No escribe comitentes, no arma ningun
POST, y no puede entrar en una subasta donde no tengas ya una oferta viva.

    python bot.py --ident 1556714 --piso 25,00
    python bot.py --ident 1556714 --piso 25,00 --vivo
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time as reloj
from datetime import datetime
from pathlib import Path

from motor.config import ConfigInvalida, ConfigSubasta
from motor.decision import Accion, decidir
from motor.libro import LibroIlegible, formatear_tasa, parsear_libro, parsear_tasa
from motor.riesgo import EstadoSesion, evaluar, verificar_despues

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sys.exit("Falta Playwright. Instalalo con:  pip install playwright")

PROGRAMA = "cpd-versubasta.r"
CDP_POR_DEFECTO = "http://localhost:9222"

SEL_TASA = 'input[name="tasa"]'
SEL_BOTON = 'input[value="Modificar Tasa Cpr."]'

INTERVALO_TRANQUILO_S = 5.0
INTERVALO_ACTIVO_S = 2.0
SEGUIR_ACTIVO_S = 120.0


class Registro:
    """Log a archivo y a pantalla. Todo lo que el bot ve y decide queda escrito."""

    def __init__(self, ruta: Path):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.fh = ruta.open("a", encoding="utf-8")

    def __call__(self, evento: str, mostrar: str | None = None, **datos) -> None:
        ahora = datetime.now()
        self.fh.write(json.dumps(
            {"t": ahora.isoformat(), "evento": evento, **datos},
            ensure_ascii=False, default=str,
        ) + "\n")
        self.fh.flush()
        if mostrar:
            print(f"{ahora:%H:%M:%S}  {mostrar}", flush=True)

    def cerrar(self) -> None:
        self.fh.close()


def buscar_marco(navegador, ident: int):
    """El frame donde vive la subasta que nos toca.

    Se busca en cada vuelta: la pantalla se recarga sola con cada POST y el
    objeto anterior queda muerto.
    """
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
                if PROGRAMA in url and f"ident={ident}" in url:
                    return pagina, marco
    return None, None


def leer_libro(marco, log):
    try:
        html = marco.content()
    except Exception as e:
        log("lectura_fallida", error=str(e))
        return None
    try:
        return parsear_libro(html)
    except LibroIlegible as e:
        # Si la pantalla cambio de forma, se para. Un libro mal leido es una
        # oferta mal puesta.
        log("libro_ilegible", f"NO ENTIENDO LA PANTALLA: {e}", error=str(e))
        return None


def cotizar(pagina, marco, tasa, log) -> bool:
    """Escribe la tasa y aprieta el boton. Lo unico que toca la plataforma."""
    texto = formatear_tasa(tasa)

    def aceptar(dialogo):
        log("confirm", detalle=dialogo.message[:400])
        dialogo.accept()

    pagina.once("dialog", aceptar)
    try:
        marco.fill(SEL_TASA, texto)
        marco.click(SEL_BOTON)
        pagina.wait_for_load_state("load", timeout=20000)
        return True
    except Exception as e:
        # Ante cualquier duda no se reintenta: se relee el libro y se decide de
        # nuevo con lo que la pantalla diga.
        log("cotizacion_dudosa", f"no se si entro: {e}", tasa=texto, error=str(e))
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
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
    ap.add_argument("--cdp", default=CDP_POR_DEFECTO)
    ap.add_argument("--log", default=None)
    ap.add_argument("--vivo", action="store_true",
                    help="Cotizar de verdad. Sin esto solo mira y anota.")
    args = ap.parse_args()

    try:
        cfg = ConfigSubasta(
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
    except (ConfigInvalida, LibroIlegible) as e:
        print(f"Configuracion invalida: {e}", file=sys.stderr)
        return 1

    ruta_log = Path(args.log or f"logs/subasta_{args.ident}_"
                                f"{datetime.now():%Y%m%d_%H%M%S}.jsonl")
    log = Registro(ruta_log)
    azar = random.Random()
    estado = EstadoSesion()

    modo = "VIVO" if args.vivo else "SOMBRA"
    print(f"Subasta {cfg.ident} | piso {formatear_tasa(cfg.piso)} | modo {modo}")
    if not args.vivo:
        print("Solo mira y anota. Para que cotice de verdad: --vivo")
    print(f"Log: {ruta_log}\nCtrl+C para cortar.\n")
    log("arranque", modo=modo, config=str(cfg))

    with sync_playwright() as pw:
        try:
            navegador = pw.chromium.connect_over_cdp(args.cdp)
        except Exception as e:
            print(f"No me pude enganchar a Chrome en {args.cdp}: {e}", file=sys.stderr)
            return 1

        ultimo_cambio = 0.0
        huella_previa = None
        try:
            while True:
                pagina, marco = buscar_marco(navegador, cfg.ident)
                if marco is None:
                    log("sin_pantalla", "no encuentro la subasta abierta")
                    reloj.sleep(INTERVALO_TRANQUILO_S)
                    continue

                leido_s = reloj.monotonic()
                libro = leer_libro(marco, log)
                if libro is None:
                    estado.detener()
                    log("detenido", "PARO: no puedo leer el libro con confianza")
                    break

                huella = tuple((o.id, o.tasa, o.ingreso) for o in libro.ofertas)
                if huella != huella_previa:
                    ultimo_cambio = reloj.monotonic()
                    huella_previa = huella
                    mia = libro.mejor_propia()
                    ajena = libro.mejor_ajena()
                    log("libro",
                        f"libro: mia={formatear_tasa(mia.tasa) if mia else '-'} "
                        f"mejor ajena={formatear_tasa(ajena.tasa) if ajena else '-'}",
                        ofertas=[o.__dict__ for o in libro.ofertas])

                decision = decidir(libro, cfg, azar)

                if decision.accion is Accion.CEDER:
                    log("ceder", f"CEDO: {decision.motivo}", motivo=decision.motivo)
                    break

                if decision.accion is not Accion.RECOTIZAR:
                    reloj.sleep(INTERVALO_ACTIVO_S
                                if reloj.monotonic() - ultimo_cambio < SEGUIR_ACTIVO_S
                                else INTERVALO_TRANQUILO_S)
                    continue

                # Demora deliberada. Contestar siempre al instante es una firma,
                # y contra un humano no gana nada.
                espera = azar.uniform(cfg.espera_min_s, cfg.espera_max_s)
                if espera > 0:
                    log("espera", f"espero {espera:.0f}s antes de mover", segundos=espera)
                    reloj.sleep(espera)

                    # Releer: en esos segundos el libro pudo cambiar, y decidir
                    # sobre lo que vimos antes de dormir seria decidir viejo.
                    pagina, marco = buscar_marco(navegador, cfg.ident)
                    if marco is None:
                        continue
                    leido_s = reloj.monotonic()
                    libro = leer_libro(marco, log)
                    if libro is None:
                        estado.detener()
                        break
                    decision = decidir(libro, cfg, azar)
                    if decision.accion is not Accion.RECOTIZAR:
                        log("cambio_de_idea", f"tras esperar: {decision.motivo}")
                        continue

                veredicto = evaluar(decision, cfg, estado, reloj.monotonic(),
                                    reloj.monotonic() - leido_s)
                if not veredicto:
                    log("bloqueado", f"gate: {veredicto.motivo}", motivo=veredicto.motivo)
                    reloj.sleep(INTERVALO_ACTIVO_S)
                    continue

                if not args.vivo:
                    log("sombra",
                        f"HARIA: cotizar {formatear_tasa(decision.tasa)} "
                        f"({decision.motivo})",
                        tasa=str(decision.tasa), motivo=decision.motivo)
                    # En sombra se cuenta igual, para que los topes y el
                    # intervalo minimo se comporten como en vivo.
                    estado.registrar(cfg.ident, reloj.monotonic())
                    reloj.sleep(INTERVALO_ACTIVO_S)
                    continue

                log("cotizando", f"cotizo {formatear_tasa(decision.tasa)}",
                    tasa=str(decision.tasa))
                cotizar(pagina, marco, decision.tasa, log)
                estado.registrar(cfg.ident, reloj.monotonic())

                # Sin pantalla de preview, la unica verificacion posible es a
                # posteriori: releer y confirmar que entro lo que queriamos.
                pagina, marco = buscar_marco(navegador, cfg.ident)
                confirmacion = leer_libro(marco, log) if marco else None
                if confirmacion is None:
                    estado.detener()
                    log("detenido", "PARO: no pude releer el libro despues de cotizar")
                    break
                v = verificar_despues(confirmacion, cfg, decision.tasa)
                log("verificacion", f"{'ok' if v else 'PARO'}: {v.motivo}", ok=v.ok,
                    motivo=v.motivo)
                if not v:
                    estado.detener()
                    break
                huella_previa = tuple(
                    (o.id, o.tasa, o.ingreso) for o in confirmacion.ofertas
                )

        except KeyboardInterrupt:
            estado.detener()
            log("kill", "cortado a mano")

    print(f"\nListo. El detalle quedo en {ruta_log}")
    log.cerrar()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
