"""Prueba de la pantalla en un navegador de verdad.

NO corre con los demas tests: necesita un navegador y levanta dos servidores.
Se corre a mano cada vez que se toca la interfaz:

    pip install playwright && playwright install chromium
    python tests/manual_navegador.py

Existe por dos bugs que ningun test unitario podia ver, los dos de la misma
causa: la pantalla se repinta entera cada segundo, y eso destruye los <input>
del panel de edicion.

  1. Se perdia el foco: escribias una letra y al segundo estabas afuera del
     casillero. Peor que molesto — escribir "26,70" en el piso guardaba 2,00,
     porque solo entraba la primera tecla, y un piso de 2,00 deja al bot
     bajando la tasa hasta el 2%.
  2. El casillero de "Cargar tu tasa" no guardaba lo tipeado, asi que cada
     repintado lo vaciaba. El cursor se quedaba y el texto desaparecia.

Los dos se rompen callados. Por eso esto queda escrito.
"""

import os
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import simulacro  # noqa: E402
import ui  # noqa: E402

PUERTO = 8736


def levantar():
    simulacro.RUIDO = False
    simulacro.arrancar_mav()
    import motor.sesion as sesion
    sesion.BASE = f"http://127.0.0.1:{simulacro.PUERTO}{simulacro.RUTA}"
    ui.TRABAJADOR.eco = False
    ui.TRABAJADOR.start()
    threading.Thread(
        target=ThreadingHTTPServer(("127.0.0.1", PUERTO), ui.Handler).serve_forever,
        daemon=True).start()


def main() -> int:
    from playwright.sync_api import sync_playwright

    levantar()
    ident = simulacro.IDENT
    fallas = []

    def revisar(que, obtenido, esperado):
        ok = obtenido == esperado
        print(f"  {'ok ' if ok else 'MAL'}  {que}: {obtenido!r}"
              + ("" if ok else f"  (esperaba {esperado!r})"))
        if not ok:
            fallas.append(que)

    with sync_playwright() as pw:
        propio = os.environ.get("CHROMIUM")
        b = (pw.chromium.launch(executable_path=propio) if propio
             else pw.chromium.launch())
        pg = b.new_page()
        pg.on("pageerror", lambda e: fallas.append(f"error de JS: {e}"))
        pg.goto(f"http://127.0.0.1:{PUERTO}/")

        pg.fill("#usuario", "a")
        pg.fill("#clave", "a")
        pg.click("#btn-ingresar")
        pg.wait_for_timeout(2500)
        pg.fill("#ident", str(ident))
        pg.fill("#piso", "26,00")
        pg.once("dialog", lambda d: d.accept())
        pg.click("#btn-vivo")          # en VIVO: la guerra corre mientras tipeamos
        pg.wait_for_timeout(4000)

        # Tipear cruzando un refresco por tecla es la condicion que rompia.
        def tipear(campo, texto):
            # El boton alterna: si el panel ya esta abierto, volver a apretarlo
            # lo cierra. La primera version del test se cerraba el panel sola.
            if pg.evaluate("abierto") != ident:
                pg.click("text=editar")
                pg.wait_for_timeout(500)
            pg.click(campo)
            pg.keyboard.press("Control+a")
            for ch in texto:
                pg.keyboard.type(ch)
                pg.wait_for_timeout(1100)
                revisar(f"{campo} sigue enfocado", pg.evaluate(
                    "document.activeElement.id"), campo[1:])
            revisar(f"{campo} conserva lo tipeado", pg.input_value(campo), texto)

        tipear(f"#ed-{ident}-piso", "26,45")
        # Cambiar el piso de una subasta en VIVO pide confirmacion, y con razon:
        # es el precio al que vas a comprar si alguien empuja. Playwright
        # rechaza los dialogos por defecto, asi que hay que aceptarlo a mano —
        # y que sin aceptarlo no se guarde nada es justamente lo correcto.
        pg.once("dialog", lambda d: d.accept())
        pg.click(f"#guardar-{ident}")
        pg.wait_for_timeout(2000)
        revisar("el piso nuevo quedo guardado",
                pg.evaluate("(ultimo.subastas||[{}])[0].cfg?.piso"), "26,45")

        tipear(f"#manual-{ident}", "26,42")
        pg.once("dialog", lambda d: d.accept())
        pg.click(f"#cargar-{ident}")
        pg.wait_for_timeout(4000)
        revisar("la tasa manual entro al libro",
                pg.evaluate("(ultimo.subastas||[{}])[0].mia"), "26,42")
        revisar("y el bot sigue cuidandola",
                pg.evaluate("(ultimo.subastas||[{}])[0].fase") != "detenido", True)

        b.close()

    print("\n" + ("TODO BIEN" if not fallas else f"FALLA: {fallas}"))
    return 1 if fallas else 0


if __name__ == "__main__":
    raise SystemExit(main())
