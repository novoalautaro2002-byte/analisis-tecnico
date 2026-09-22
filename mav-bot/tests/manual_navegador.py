"""Prueba de la pantalla en un navegador de verdad.

NO corre con los demas tests: necesita un navegador y levanta dos servidores.
Se corre a mano cuando se toca la interfaz:

    pip install playwright && playwright install chromium
    python tests/manual_navegador.py

Existe por un bug que ningun test unitario podia ver: la pantalla se repinta
cada segundo, y al repintarse destruia los <input> del panel de edicion. Se
escribia una letra y al segundo siguiente el cursor estaba afuera del casillero.
Lo peor no era la molestia: con el arreglo desactivado, escribir "26,70" en el
piso guardaba 2,00 — solo entraba el primer caracter. Un piso de 2,00 deja al
bot bajando la tasa hasta el 2%.

Si esto se rompe de nuevo, se rompe callado. Por eso queda escrito.
"""
import sys, threading, time
sys.path.insert(0, "/home/user/analisis-tecnico/mav-bot")
import simulacro
simulacro.RUIDO = False
simulacro.arrancar_mav()
import motor.sesion as sesion
sesion.BASE = f"http://127.0.0.1:{simulacro.PUERTO}{simulacro.RUTA}"
import ui
from http.server import ThreadingHTTPServer
ui.TRABAJADOR.eco = False
ui.TRABAJADOR.start()
threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 8735), ui.Handler)
                 .serve_forever, daemon=True).start()

from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    import os
    # En un entorno con el chromium ya bajado se usa ese; si no, el de siempre.
    propio = os.environ.get("CHROMIUM")
    b = pw.chromium.launch(executable_path=propio) if propio else pw.chromium.launch()
    pg = b.new_page()
    pg.goto("http://127.0.0.1:8735/")
    pg.fill("#usuario", "a"); pg.fill("#clave", "a")
    pg.click("#btn-ingresar"); pg.wait_for_timeout(2500)
    pg.fill("#ident", str(simulacro.IDENT)); pg.fill("#piso", "26,00")
    pg.click("#btn-sombra"); pg.wait_for_timeout(2500)

    pg.click("text=editar"); pg.wait_for_timeout(600)
    campo = f"#ed-{simulacro.IDENT}-piso"
    pg.click(campo)
    pg.keyboard.press("Control+a")

    # Tipear LENTO, cruzando varios refrescos de un segundo. Ahi es donde
    # antes te sacaba del casillero.
    for ch in "26,7":
        pg.keyboard.type(ch)
        pg.wait_for_timeout(450)

    enfocado = pg.evaluate("document.activeElement.id")
    valor = pg.input_value(campo)
    print(f"  tras tipear 4 caracteres en ~2s (2 refrescos):")
    print(f"    foco en    : {enfocado!r}   (esperado: {campo[1:]!r})")
    print(f"    valor      : {valor!r}      (esperado: '26,7')")

    # Y que guardar aplique de verdad
    pg.keyboard.type("0")
    pg.click("text=Guardar condiciones"); pg.wait_for_timeout(2000)
    piso = pg.evaluate("(ultimo.subastas||[{}])[0].cfg?.piso")
    print(f"    guardado   : piso={piso!r}  (esperado: '26,70')")
    b.close()
