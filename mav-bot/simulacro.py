"""Un MAV de mentira, para ver al bot pelear sin arriesgar un peso.

    python simulacro.py

Levanta dos cosas y abre el navegador:

  * un servidor que contesta como MAV — las mismas URLs, el mismo HTML con
    `var myData`, el mismo JSON del listado, el mismo POST de alta;
  * la interfaz de siempre (`ui.py`), apuntada a ese servidor en vez de a la
    plataforma real.

Adentro hay una subasta con una oferta tuya (agente 442) y un rival que te baja
un centavo cada pocos segundos hasta su propio piso. O sea: exactamente la
guerra que describiste. Vos ponés tu piso y mirás qué hace el bot.

El simulacro no es amable a propósito:

  * tarda un poco en mostrar una oferta recién cargada, que es lo que hacía que
    el bot se cortara solo;
  * rechaza el POST si le falta el comitente, con el mismo texto que te tiró la
    plataforma de verdad ("La oferta de compra no ha sido ingresada");
  * cierra la subasta con cuenta regresiva que se reinicia en cada mejora.

El mercado está congelado hasta que el bot abre la subasta: nada se mueve
mientras te logueás y llenás el formulario. Y cuando una rueda termina, se abre
otra sola a los pocos segundos, así probar de nuevo no obliga a reiniciar nada.

Nada de esto toca la plataforma real. El servidor escucha en 127.0.0.1 y el bot
nunca ve otra dirección.
"""

from __future__ import annotations

import json
import threading
import time as reloj
import urllib.parse
from datetime import datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PUERTO = 8900
RUTA = "/cgi-bin/wspd_cgi.sh/WService=wsbroker1/"
CODIFICACION = "latin-1"

MI_AGENTE = "442"
IDENT = 1556714
CHEQUES = ("02579750", "02579751")

# Cuenta regresiva del cierre. En MAV son 3 minutos; acá va más corto para que
# una demostración dure lo que dura un café y no una reunión.
CUENTA_S = 45.0

# Cuando una rueda termina, el simulacro abre otra sola. Asi probar de nuevo no
# obliga a cortar y volver a levantar todo, y una demora tuya no arruina nada.
PAUSA_ENTRE_RUEDAS_S = 12.0

# Lo que tarda la plataforma en mostrar una oferta recién cargada. Es el detalle
# que rompía al bot, así que el simulacro lo exagera a propósito.
DEMORA_EN_VERSE_S = 1.2


# Los tests importan este modulo: sin esto, cada alta ensucia su salida.
RUIDO = True


def hablar(mensaje: str) -> None:
    if RUIDO:
        print(mensaje)


def coma(d: Decimal) -> str:
    return f"{d.quantize(Decimal('0.01')):f}".replace(".", ",")


# ---------------------------------------------------------------------------
# El mercado


class Subasta:
    """Una subasta con su libro. Todo el estado vive acá adentro."""

    def __init__(self, ident: int = IDENT):
        self.ident = ident
        self.candado = threading.Lock()
        self.proximo_id = 2046500
        self.historia: list[str] = []
        self.ronda = 0
        self.reabre_s: float | None = None
        self.abrir()

    def abrir(self) -> None:
        """Arranca una rueda nueva, congelada hasta que alguien la mire."""
        with self.candado:
            self.ronda += 1
            self.estado = "Activa"
            self.reabre_s = None
            self.ofertas = []
            # La cuenta regresiva NO corre todavia. Si corriera desde que
            # levanta el servidor, la subasta se cerraria sola mientras el
            # trader se loguea y llena el formulario — que es exactamente lo
            # que pasaba.
            self.mirada = False
            self.cierra_s = float("inf")
        # La oferta que el trader carga a mano antes de activar el bot: sin
        # esto el bot no tiene nada que defender, y no puede entrar solo.
        self.cargar(MI_AGENTE, Decimal("27.00"), "vos, a mano")
        self.ofertas[0]["cargada_s"] = 0.0      # ya visible
        self.cierra_s = float("inf")            # cargar() la habia puesto a correr

    def mirar(self) -> None:
        """Alguien abrió la subasta: recién ahí empieza a correr el reloj."""
        if self.mirada or self.estado != "Activa":
            return
        with self.candado:
            self.mirada = True
            self.cierra_s = reloj.monotonic() + CUENTA_S
        hablar(f"    (empieza la rueda {self.ronda}: cierra en {CUENTA_S:.0f}s "
               f"sin mejoras)")

    # -- lectura -----------------------------------------------------------

    def visibles(self) -> list[dict]:
        """El libro como lo contestaría el servidor: sin lo recién cargado.

        MAV no muestra una oferta en el mismo instante en que la toma. Que el
        bot aguante esa demora en vez de frenar es la mitad del arreglo de hoy.
        """
        ahora = reloj.monotonic()
        return [o for o in self.ofertas
                if ahora - o["cargada_s"] >= DEMORA_EN_VERSE_S]

    def mejor(self) -> dict | None:
        return min(self.visibles(), key=lambda o: (o["tasa"], o["id"]),
                   default=None)

    def falta_s(self) -> float:
        return max(0.0, self.cierra_s - reloj.monotonic())

    # -- escritura ---------------------------------------------------------

    def cargar(self, agente: str, tasa: Decimal, quien: str) -> None:
        """Alta o modificación. Un agente tiene una oferta por subasta."""
        with self.candado:
            self.ofertas = [o for o in self.ofertas if o["agente"] != agente]
            self.proximo_id += 1
            self.ofertas.append({
                "id": self.proximo_id, "agente": agente, "tasa": tasa,
                "hora": datetime.now(), "cargada_s": reloj.monotonic(),
            })
            # Cierre blando: cada mejora reinicia la cuenta.
            self.cierra_s = reloj.monotonic() + CUENTA_S
            self.historia.append(
                f"{datetime.now():%H:%M:%S}  {quien} carga {coma(tasa)}")

    def latir(self) -> bool:
        """Devuelve True si acaba de abrir una rueda nueva."""
        ahora = reloj.monotonic()
        if self.estado == "Activa":
            if self.mirada and self.falta_s() <= 0:
                self._cerrar(ahora)
            return False
        if self.reabre_s is not None and ahora >= self.reabre_s:
            self.abrir()
            hablar(f"\n--- rueda {self.ronda}: subasta {self.ident} de nuevo "
                   f"activa, con tu oferta en 27,00 ---")
            hablar("    (volvé a sumarla en la pantalla)\n")
            return True
        return False

    def _cerrar(self, ahora: float) -> None:
        with self.candado:
            self.estado = "Negociada"
            self.reabre_s = ahora + PAUSA_ENTRE_RUEDAS_S
        mejor = self.mejor()
        quien = "nadie" if not mejor else f"el agente {mejor['agente']}"
        tasa = "-" if not mejor else coma(mejor["tasa"])
        self.historia.append(
            f"{datetime.now():%H:%M:%S}  CIERRA en {tasa}, se la lleva {quien}")
        hablar(f"\n*** subasta {self.ident} cerrada en {tasa} ({quien}) ***\n")


class Rival:
    """El humano del otro lado: te baja un centavo hasta donde le cierra."""

    def __init__(self, subasta: Subasta, agente="406", piso=Decimal("26.90"),
                 cada_s=4.0):
        self.subasta = subasta
        self.agente = agente
        self.piso = piso
        self.cada_s = cada_s
        self.proximo_s = reloj.monotonic() + cada_s

    def reiniciar(self) -> None:
        self.proximo_s = reloj.monotonic() + self.cada_s

    def latir(self) -> None:
        # Antes de que alguien mire la subasta no pasa nada: el mercado no se
        # mueve para nadie.
        if not self.subasta.mirada:
            return
        if self.subasta.estado != "Activa" or reloj.monotonic() < self.proximo_s:
            return
        self.proximo_s = reloj.monotonic() + self.cada_s
        mejor = self.subasta.mejor()
        if mejor is None or mejor["agente"] == self.agente:
            return                      # ya tengo la punta
        objetivo = mejor["tasa"] - Decimal("0.01")
        if objetivo < self.piso:
            return                      # hasta acá llego; que se lo lleve
        self.subasta.cargar(self.agente, objetivo, f"rival {self.agente}")
        hablar(f"    rival {self.agente} baja a {coma(objetivo)}")


# ---------------------------------------------------------------------------
# Las páginas, con la forma exacta que espera el bot


def pagina_login() -> str:
    return """<html><head><title>Inicio de Sesión</title></head><body>
<form method="post" action="validar2.r">
<input type="text" name="id" value="">
<input type="password" name="password" value="">
<input type="hidden" name="origen" value="web">
<div id="deshinibhir-modal"><div>Si su usuario se encuentra inhibido,
comuníquese con un usuario Master.</div></div>
</form></body></html>"""


def pagina_adentro() -> str:
    return "<html><body><h1>Listado de subastas</h1></body></html>"


def pagina_subasta(s: Subasta, aviso: str = "") -> str:
    """Como cpd-versubasta.r: el libro en un array de JS y el form de alta."""
    filas = ",\n".join(
        f"""[ " {o['id']}","{o['agente']}", "{coma(o['tasa'])}", """
        f""""{o['hora']:%H:%M:%S}", "<a href='#' onClick='bajaOferta({o['id']})'>[X]</a>"]"""
        for o in sorted(s.visibles(), key=lambda o: (o["tasa"], o["id"]))
    )
    porcheque = "\n".join(
        f'<input type="hidden" name="comitcpr{c}">\n'
        f'<input type="hidden" name="cuitcpr{c}">\n'
        f'<input type="hidden" name="excepcpr{c}" value="No">\n'
        f'<input type="hidden" name="condcpr{c}">'
        for c in CHEQUES
    )
    return f"""<html><head>
<script>
        var myData = [
{filas}
        ];
        var myColumns = [
                "Oferta", "Ag.", "Desc.", "Ingreso", "Baja"
        ];
</script></head>
<body>
{f'<div class="aviso">{aviso}</div>' if aviso else ''}
<form method="post">
<input type="hidden" name="action">
<input type="hidden" name="id">
<input type="hidden" name="ident" value="{s.ident}">
{porcheque}
<input type="hidden" id="tipoinstrumento" name="tipoinstrumento" value="ECHEQ">
<input style="text-align: right" type="text" name="tasa" size="6" value="">
</form>
</body></html>"""


def pagina_cheques(comitente="51414", cuit="30-11111111-1") -> str:
    """Como cpd-ch-subasta-i-v2.r, con el name= repetido y todo."""
    return "\n".join(
        f"""<input data-role='none' type='text' size='9' value='{comitente}'
              name='comit-cpr{c}' id='idcomitcpr{c}'>
<input data-role='none' type="hidden" value="{comitente}" name="comitcpr{c}" name="hidcomitcpr{c}">
<input data-role='none' type="hidden" value="{cuit}" name="cuitcpr{c}" id="hidcuitcpr{c}">
<input data-role='none' type="hidden" value="Si" name="excepcpr{c}" name="hidexcepcpr{c}">
<input data-role='none' type="hidden" value="EX" name="condcpr{c}" name="hidcondcpr{c}">"""
        for c in CHEQUES
    )


def fila_listado(s: Subasta) -> dict:
    """Con los nombres de campo reales, los que confirmó el diagnóstico."""
    mejor = s.mejor()
    # Antes de que empiece la rueda no hay cuenta corriendo, pero la pantalla
    # igual muestra un horario de cierre previsto.
    falta = s.falta_s()
    cierre = datetime.now() + timedelta(
        seconds=CUENTA_S if falta == float("inf") else falta)
    tmin = datetime.now() - timedelta(seconds=30)
    return {
        "ident": str(s.ident),
        "estado": s.estado,
        "segmento": "Avalado",
        "tipo-instrumento": "ECHEQ",
        "moneda": "1", "moneda-signo": "$",
        "tasa-cpr": coma(mejor["tasa"]) if mejor else "",
        "agente-cpr": mejor["agente"] if mejor else "",
        "tasa-vdr": "30,00",
        "agente-vdr": MI_AGENTE,
        "tiempo-minimo": f"{tmin:%H:%M:%S}",
        "tiempo-minimo-ss": str(tmin.hour * 3600 + tmin.minute * 60 + tmin.second),
        "hora-cierre": f"{cierre:%H:%M:%S}",
        "hora-cierre-ss": str(cierre.hour * 3600 + cierre.minute * 60 + cierre.second),
        "cantidad-cheques": str(len(CHEQUES)),
        "monto": "1.500.000,00",
        "ya-negociado": "si" if s.estado != "Activa" else "no",
        "primera-neg": "si",
    }


# ---------------------------------------------------------------------------
# El servidor


class Handler(BaseHTTPRequestHandler):
    subasta: Subasta = None          # lo pone arrancar()
    rival: Rival = None

    protocol_version = "HTTP/1.1"

    def log_message(self, *a):        # silencio: el ruido lo pone el bot
        pass

    # -- plomería ----------------------------------------------------------

    def _responder(self, cuerpo: str, tipo="text/html; charset=iso-8859-1"):
        crudo = cuerpo.encode(CODIFICACION, errors="replace")
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(crudo)))
        self.end_headers()
        self.wfile.write(crudo)

    def _programa(self) -> tuple[str, dict]:
        partes = urllib.parse.urlparse(self.path)
        programa = partes.path.split("/")[-1]
        params = urllib.parse.parse_qs(partes.query, encoding=CODIFICACION)
        return programa, {k: v[0] for k, v in params.items()}

    def _latir(self):
        if self.subasta.latir():
            self.rival.reiniciar()
        self.rival.latir()

    # -- rutas -------------------------------------------------------------

    def do_GET(self):
        self._latir()
        programa, params = self._programa()

        if programa == "mvr-usuarios.r":
            return self._responder(pagina_login())
        if programa == "cpd-subastas-listado.r":
            return self._responder(pagina_adentro())
        if programa == "cpd-subastas-api.p":
            filas = [fila_listado(self.subasta)]
            pedido = (params.get("p-ident") or "").strip()
            if pedido and pedido != str(self.subasta.ident):
                filas = []
            return self._responder(json.dumps({"work-json": filas}),
                                   "application/json")
        if programa == "cpd-versubasta.r":
            # Abrir la subasta es lo que larga la cuenta regresiva.
            self.subasta.mirar()
            return self._responder(pagina_subasta(self.subasta))
        if programa == "cpd-ch-subasta-i-v2.r":
            return self._responder(pagina_cheques())
        if programa == "cpd-of-compra-i.r":
            return self._responder("<html><body>ofertas de compra</body></html>")
        self.send_error(404)

    def do_POST(self):
        self._latir()
        programa, _ = self._programa()
        largo = int(self.headers.get("Content-Length") or 0)
        crudo = self.rfile.read(largo).decode(CODIFICACION, errors="replace")
        campos = {k: v[0] for k, v in
                  urllib.parse.parse_qs(crudo, keep_blank_values=True,
                                        encoding=CODIFICACION).items()}

        if programa == "validar2.r":
            return self._responder(pagina_adentro())
        if programa == "cpd-versubasta.r":
            return self._responder(alta(self.subasta, campos))
        self.send_error(404)


def alta(s: Subasta, campos: dict) -> str:
    """El alta de oferta, con los mismos rechazos que la plataforma real."""
    if s.estado != "Activa":
        return pagina_subasta(s, "La subasta no se encuentra activa.")
    if campos.get("action") != "altaCompra":
        return pagina_subasta(s, "La oferta de compra no ha sido ingresada.")

    # Sin comitente no hay orden. Es el error que te tiró MAV de verdad
    # cuando el payload salía incompleto.
    faltan = [c for c in CHEQUES if not (campos.get(f"comitcpr{c}") or "").strip()]
    if faltan:
        hablar(f"    !! RECHAZADO: sin comitente en {faltan}")
        return pagina_subasta(s, "La oferta de compra no ha sido ingresada.")

    try:
        tasa = Decimal((campos.get("tasa") or "").replace(".", "").replace(",", "."))
    except Exception:
        return pagina_subasta(s, "Tasa inválida.")

    s.cargar(MI_AGENTE, tasa, "EL BOT")
    comitente = campos.get(f"comitcpr{CHEQUES[0]}")
    hablar(f"    bot carga {coma(tasa)}  comitente {comitente}  "
           f"(cierra en {s.falta_s():.0f}s)")
    return pagina_subasta(s)


# ---------------------------------------------------------------------------
# Arranque


def arrancar_mav() -> Subasta:
    subasta = Subasta()
    Handler.subasta = subasta
    Handler.rival = Rival(subasta)
    servidor = ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return subasta


def main() -> int:
    subasta = arrancar_mav()

    # El bot habla con el simulacro y con nadie más.
    import motor.sesion as sesion
    sesion.BASE = f"http://127.0.0.1:{PUERTO}{RUTA}"

    import ui

    print(__doc__.split("\n\n")[0])
    print()
    print(f"  MAV de mentira en  http://127.0.0.1:{PUERTO}{RUTA}")
    print(f"  Subasta            {subasta.ident}")
    print(f"  Tu oferta          27,00  (agente {MI_AGENTE})")
    print(f"  El rival           406, te baja 0,01 cada {Handler.rival.cada_s:.0f}s "
          f"hasta {coma(Handler.rival.piso)}")
    print(f"  Cierra             {CUENTA_S:.0f}s sin que nadie mejore "
          f"(en MAV son 3 minutos)")
    print()
    print("  Nada se mueve hasta que el bot abra la subasta, así que tomate el")
    print(f"  tiempo que quieras. Cerrada una rueda, a los {PAUSA_ENTRE_RUEDAS_S:.0f}s se abre otra.")
    print()
    print("  En la pantalla del bot:")
    print("    1. usuario y contraseña: cualquier cosa, no se valida")
    print(f"    2. Subasta {IDENT}, y 'Sumar en VIVO'")
    print()
    print("  Corré las dos y compará, que es donde se ve si hace lo que querés:")
    print()
    print("    Piso 26,50  ->  el bot le gana. El rival se planta en 26,90,")
    print("                    el bot queda en 26,90 y la subasta cierra a su")
    print("                    favor cuando pasan los segundos sin mejoras.")
    print()
    print("    Piso 26,96  ->  el bot cede. Llega a 26,96, el rival tira 26,95,")
    print("                    y para superarlo tendría que perforar tu piso.")
    print("                    Se planta y te lo dice. No lo perfora nunca.")
    print()
    return ui.main()


if __name__ == "__main__":
    raise SystemExit(main())
