"""Lo unico que toca el navegador.

Dos lecturas y dos escrituras, y nada mas: encontrar el frame de la subasta,
leer su HTML, escribir el campo de tasa y apretar el boton. Todo lo demas del
bot trabaja sobre datos.
"""

from __future__ import annotations

from .libro import Libro, LibroIlegible, formatear_tasa, parsear_libro

PROGRAMA = "cpd-versubasta.r"

# Los dos unicos elementos de la pantalla que el bot toca. Un test los fija
# contra el HTML real, asi que si MAV los renombra se entera el test.
SEL_TASA = 'input[name="tasa"]'
SEL_BOTON = 'input[value="Modificar Tasa Cpr."]'


def buscar_marco(navegador, ident: int):
    """(pagina, marco) de la subasta pedida, o (None, None).

    Se busca en cada vuelta y no se cachea: la pantalla se recarga con cada
    POST y el objeto anterior queda muerto.

    El filtro por ident no es cosmetico: si hay dos subastas abiertas, es lo
    que impide que el bot toque la que no le toca.
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


def leer_libro(marco, log) -> Libro | None:
    """El libro, o None si no se puede leer con confianza.

    None no es "no pasa nada": el que llama tiene que parar. Un libro mal leido
    es una oferta mal puesta.
    """
    try:
        html = marco.content()
    except Exception as e:
        # El frame estaba navegando o se auto-refresco. Se reintenta la lectura
        # en la vuelta siguiente, que es distinto de reintentar una orden.
        log("lectura_fallida", error=str(e))
        return None
    try:
        return parsear_libro(html)
    except LibroIlegible as e:
        log("libro_ilegible", f"NO ENTIENDO LA PANTALLA: {e}", error=str(e))
        return None


def cotizar(pagina, marco, tasa, log) -> bool:
    """Escribe la tasa y aprieta el boton.

    Devuelve si la secuencia termino limpia. Un False no significa que la
    oferta no entro: significa que no sabemos. Por eso el que llama siempre
    relee el libro despues, y nunca reintenta contra la duda.
    """
    texto = formatear_tasa(tasa)

    def aceptar(dialogo):
        # El confirm() de la plataforma. Queda logueado con su texto completo,
        # que es el unico registro de que la pantalla pidio confirmacion.
        log("confirm", detalle=dialogo.message[:400])
        dialogo.accept()

    pagina.once("dialog", aceptar)
    try:
        marco.fill(SEL_TASA, texto)
        marco.click(SEL_BOTON)
        pagina.wait_for_load_state("load", timeout=20000)
        return True
    except Exception as e:
        log("cotizacion_dudosa", f"no se si entro: {e}", tasa=texto, error=str(e))
        return False
