"""Conversacion directa con la plataforma, sin navegador.

Usa solo la biblioteca estandar: una dependencia menos para instalar.

La sesion es la que el trader ya abrio a mano en su navegador. Este modulo no
sabe usuario ni contraseña y no las pide: recibe la cookie de una sesion viva y
la relaya. El 2FA se hace donde siempre.

Los GET se pueden reintentar. Los POST **nunca**: un alta que no sabemos si
entro se resuelve releyendo el libro, no mandandola de nuevo.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

BASE = "https://trading.mav-sa.com.ar/cgi-bin/wspd_cgi.sh/WService=wsbroker1/"

# La plataforma emite y espera ISO-8859-1. Mandar UTF-8 rompe los nombres con
# acento y los CUIT quedan bien pero los comitentes no.
CODIFICACION = "latin-1"

NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_PANTALLA_LOGIN = re.compile(r"validar2\.r|Inicio de Sesi", re.I)
_COOKIE = re.compile(r"(mvrcookie|mvrusername)\s*=\s*([^;,\s]+)")


class SesionCaida(Exception):
    """La plataforma contesto la pantalla de login: la sesion vencio.

    Importa que sea su propia excepcion: el bot no puede re-loguearse solo
    (el 2FA lo impide), asi que esto siempre termina en parar y avisar.
    """


class ErrorDePlataforma(Exception):
    pass


@dataclass
class Sesion:
    cookies: dict[str, str] = field(default_factory=dict)
    tiempo_max_s: float = 20.0

    @classmethod
    def desde_texto(cls, texto: str) -> "Sesion":
        """Arma la sesion desde lo que devuelve `document.cookie`.

        Se acepta el volcado entero para que el trader copie y pegue sin tener
        que buscar cual de todas es.
        """
        encontradas = dict(_COOKIE.findall(texto or ""))
        if "mvrcookie" not in encontradas:
            raise ErrorDePlataforma(
                "no encuentro 'mvrcookie' en lo que pegaste; "
                "copia la salida de document.cookie con la sesion abierta"
            )
        return cls(cookies=encontradas)

    @property
    def cabecera_cookie(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.cookies.items())

    # -- lecturas ----------------------------------------------------------

    def get(self, programa: str, **params) -> str:
        url = BASE + programa
        if params:
            url += "?" + urllib.parse.urlencode(params, encoding=CODIFICACION)
        return self._pedir(urllib.request.Request(url, method="GET"))

    def subasta(self, ident: int) -> str:
        return self.get("cpd-versubasta.r", ident=ident)

    def cheques(self, ident: int) -> str:
        return self.get("cpd-ch-subasta-i-v2.r", ident=ident)

    # -- escritura ---------------------------------------------------------

    def postear(self, programa: str, pares, referer_ident: int | None = None) -> str:
        """El POST de la oferta. Sin reintentos, por diseño."""
        cuerpo = urllib.parse.urlencode(list(pares), encoding=CODIFICACION,
                                        errors="replace").encode(CODIFICACION)
        # El form de la pantalla no tiene atributo action: postea contra su
        # propia URL, con el ident en el query. Se replica igual.
        destino = BASE + programa
        if referer_ident is not None:
            destino += f"?ident={referer_ident}"
        pedido = urllib.request.Request(
            destino, data=cuerpo, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Referer": destino},
        )
        return self._pedir(pedido)

    # -- plomeria ----------------------------------------------------------

    def _pedir(self, pedido: urllib.request.Request) -> str:
        pedido.add_header("Cookie", self.cabecera_cookie)
        pedido.add_header("User-Agent", NAVEGADOR)
        pedido.add_header("Accept-Language", "es-AR,es;q=0.9")
        try:
            with urllib.request.urlopen(pedido, timeout=self.tiempo_max_s) as r:
                crudo = r.read()
        except urllib.error.HTTPError as e:
            raise ErrorDePlataforma(f"HTTP {e.code} en {pedido.full_url}") from e
        except urllib.error.URLError as e:
            raise ErrorDePlataforma(f"no llegue a la plataforma: {e.reason}") from e

        html = crudo.decode(CODIFICACION, errors="replace")
        if es_pantalla_de_login(html):
            raise SesionCaida("la plataforma devolvio el login: la sesion vencio")
        return html


def es_pantalla_de_login(html: str) -> bool:
    """Si la respuesta es el login en vez de lo que pedimos.

    Se mira solo el principio: la palabra puede aparecer en cualquier lado de
    una pagina larga, pero el formulario de login esta arriba de todo.
    """
    return bool(_PANTALLA_LOGIN.search(html[:4000]))
