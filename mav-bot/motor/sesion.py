"""Conversacion directa con la plataforma, sin navegador.

Usa solo la biblioteca estandar: una dependencia menos para instalar.

El ingreso se hace aca mismo, en la maquina del trader. La contraseña y el
codigo viven en memoria el tiempo que dura el pedido y no se escriben en ningun
archivo ni en el log. El 2FA no se adivina: se lee el formulario que manda el
servidor, se completan los campos visibles y se devuelve el resto intacto.

Los GET se pueden reintentar. Los POST **nunca**: un alta que no sabemos si
entro se resuelve releyendo el libro, no mandandola de nuevo.
"""

from __future__ import annotations

import http.cookiejar
import re
import time as reloj
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .formulario import FormularioIlegible, parsear_form

BASE = "https://trading.mav-sa.com.ar/cgi-bin/wspd_cgi.sh/WService=wsbroker1/"
LOGIN = "mvr-usuarios.r"

# La plataforma emite y espera ISO-8859-1. Mandar UTF-8 rompe los acentos.
CODIFICACION = "latin-1"

NAVEGADOR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_PANTALLA_LOGIN = re.compile(r"validar2\.r|Inicio de Sesi", re.I)
_COOKIE = re.compile(r"(mvrcookie|mvrusername)\s*=\s*([^;,\s]+)")

# <meta http-equiv="refresh" content="0; URL=login.r?..."> — una redireccion
# que hace el navegador, no el servidor, asi que urllib no la sigue sola. El
# login de MAV la usa entre pasos; sin seguirla, el ingreso se queda a mitad.
_META_REFRESH = re.compile(
    r"""<meta[^>]*http-equiv=["']?refresh["']?[^>]*"""
    r"""content=["'][^"']*url\s*=\s*([^"'>\s]+)""", re.I)


def destino_refresh(html: str) -> str | None:
    m = _META_REFRESH.search(html)
    return m.group(1) if m else None
_ERROR = re.compile(
    r"(usuario o contrase|incorrect\w*|inhibid\w*|expirad\w*|bloquead\w*|"
    r"sesi\w+ activa|ya se encuentra)", re.I)

# Elementos que SOLO existen en el paso del codigo: el login inicial no los
# trae. Es un marcador positivo, mucho mas confiable que mirar si sigue
# habiendo un campo password — el paso del codigo lo conserva.
_MARCAS_2FA = re.compile(
    r"""id=["']?(ingresarcodigo|reenviocodigo|codigoincorrecto|"""
    r"""codigoexpirado|contador|reenviar|contexpir|tiempoexpir)["']?""", re.I)


# El login trae siempre un cartel de ayuda que explica que hacer "si su usuario
# se encuentra inhibido". Buscar errores sin sacarlo primero da un falso
# positivo de inhibicion en cada ingreso.
_AYUDA = re.compile(
    r"<div[^>]*id=[\"']?deshinibhir-modal.*?</div>\s*</div>", re.I | re.S)


def pide_codigo(html: str) -> bool:
    return bool(_MARCAS_2FA.search(html))


_SCRIPT = re.compile(r"<script[\s\S]*?</script>", re.I)
_ESTILO = re.compile(r"<style[\s\S]*?</style>", re.I)
_COMENTARIO = re.compile(r"<!--[\s\S]*?-->")
_TAG = re.compile(r"<[^>]+>")
_ESPACIOS = re.compile(r"\s+")


def texto_visible(html: str) -> str:
    """Solo lo que el usuario lee en pantalla.

    Buscar errores sobre el HTML crudo no sirve: `codigoincorrecto` es el id de
    un elemento y `inhibido` vive en un cartel de ayuda que viene siempre. Los
    dos hacian que cada ingreso pareciera fallado.
    """
    limpio = _AYUDA.sub("", html)
    limpio = _COMENTARIO.sub("", _ESTILO.sub("", _SCRIPT.sub("", limpio)))
    return _ESPACIOS.sub(" ", _TAG.sub(" ", limpio)).strip()


class SesionCaida(Exception):
    """La plataforma devolvio el login: la sesion vencio.

    Tiene excepcion propia porque siempre termina igual: parar y avisar. El bot
    no puede re-loguearse solo sin el codigo, que le llega al trader.
    """


class ErrorDePlataforma(Exception):
    pass


class IngresoRechazado(Exception):
    """Credenciales mal, usuario inhibido, o codigo vencido."""


@dataclass
class Pendiente:
    """Lo que el servidor pide para seguir: normalmente el codigo del 2FA."""

    campos: tuple[str, ...]
    mensaje: str


@dataclass
class Sesion:
    tiempo_max_s: float = 20.0
    guardar_en: Path | None = None
    jar: http.cookiejar.CookieJar = field(default_factory=http.cookiejar.CookieJar)
    _abridor: urllib.request.OpenerDirector | None = field(default=None, repr=False)
    _paso: object | None = field(default=None, repr=False)
    _base: str = field(default="", repr=False)

    def __post_init__(self):
        if self._abridor is None:
            self._abridor = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(self.jar))

    # -- ingreso -----------------------------------------------------------

    def ingresar(self, usuario: str, clave: str) -> Pendiente | None:
        """Manda usuario y contraseña.

        Devuelve None si ya quedo adentro, o un Pendiente con los campos que
        falta completar — el codigo del 2FA, con el nombre que le ponga el
        servidor.
        """
        html = self._pedir(urllib.request.Request(BASE + LOGIN), en_login=True)
        # El login limpio, para despues poder mostrar solo lo que cambio.
        self._base = texto_visible(html)
        form = parsear_form(html, con_campo="password")
        campos = dict(form.campos)
        campos["id"] = usuario
        campos["password"] = clave
        return self._enviar_form(form.action or LOGIN, campos)

    def continuar(self, valores: dict[str, str]) -> Pendiente | None:
        """Completa lo que pidio el paso anterior, tipicamente el codigo."""
        if self._paso is None:
            raise IngresoRechazado("no hay ningun ingreso a medio hacer")
        accion, campos = self._paso
        campos = dict(campos)
        campos.update(valores)
        return self._enviar_form(accion, campos)

    def _enviar_form(self, accion: str, campos: dict[str, str]) -> Pendiente | None:
        html = self._postear_crudo(accion, campos.items(), en_login=True)

        if not es_pantalla_de_login(html) and self.adentro():
            self._paso = None
            return None

        error = _texto_de_error(_ERROR.search(texto_visible(html)))

        if pide_codigo(html):
            try:
                form = parsear_form(html)
            except FormularioIlegible:
                raise IngresoRechazado(error or self._sin_entender(html))
            # Campos visibles sin valor, sin contar los del login que ya
            # mandamos: eso es lo que el servidor esta pidiendo ahora.
            faltan = tuple(c for c in form.visibles
                           if c not in ("id", "password")
                           and not (form.campos.get(c) or "").strip())
            if faltan:
                self._paso = (form.action or accion, dict(form.campos))
                return Pendiente(campos=faltan,
                                 mensaje=error or "Te mandaron el código.")

        self._paso = None
        raise IngresoRechazado(self._explicar(html, error))

    def _explicar(self, html: str, error: str | None) -> str:
        """El motivo, y ademas la respuesta guardada.

        Se guarda siempre que el ingreso falla, no solo cuando no se entiende:
        un mensaje reconocido tambien puede estar mal interpretado, y sin el
        HTML no hay forma de saberlo. Es la pantalla de login de la plataforma;
        no lleva contraseñas, a lo sumo el nombre de usuario.
        """
        guardado = None
        if self.guardar_en is not None:
            try:
                self.guardar_en.parent.mkdir(parents=True, exist_ok=True)
                ruta = self.guardar_en.with_name(f"ingreso_{int(reloj.time())}.html")
                ruta.write_text(html, encoding=CODIFICACION, errors="replace")
                guardado = ruta.name
            except Exception:
                pass

        partes = [error or "La plataforma no aceptó el ingreso."]
        dice = self.novedad(html)
        if dice:
            partes.append(f"MAV dice: «{dice}»")
        if guardado:
            partes.append(f"(respuesta guardada en logs/{guardado})")
        return " ".join(partes)

    def novedad(self, html: str) -> str:
        """Lo que dice esta pantalla y no decia el login limpio.

        Mostrar el texto entero no sirve: es casi todo el mismo formulario. Lo
        util es la diferencia, que es justamente el mensaje de la plataforma.
        """
        if not self._base:
            return ""
        conocidas = set(self._base.split())
        nuevas = [p for p in texto_visible(html).split() if p not in conocidas]
        return " ".join(nuevas)[:300].strip()

    def adentro(self) -> bool:
        """Confirma contra una pantalla real, no contra la respuesta del login."""
        try:
            self.get("cpd-subastas-listado.r")
            return True
        except (SesionCaida, ErrorDePlataforma):
            return False

    # -- sesion ya abierta en otro lado ------------------------------------

    @classmethod
    def desde_texto(cls, texto: str) -> "Sesion":
        """Arma la sesion desde una cookie copiada del navegador.

        Sigue existiendo para cuando el trader ya tiene la sesion abierta y
        prefiere no volver a ingresar. Acepta el volcado entero de
        `document.cookie` para no tener que buscar cual es.
        """
        encontradas = dict(_COOKIE.findall(texto or ""))
        if "mvrcookie" not in encontradas:
            raise ErrorDePlataforma(
                "no encuentro 'mvrcookie' en lo que pegaste; "
                "copia la salida de document.cookie con la sesion abierta"
            )
        s = cls()
        dominio = urllib.parse.urlparse(BASE).hostname
        for nombre, valor in encontradas.items():
            s.jar.set_cookie(http.cookiejar.Cookie(
                0, nombre, valor, None, False, dominio, True, False,
                "/", True, True, None, False, None, None, {}))
        return s

    @property
    def cabecera_cookie(self) -> str:
        return "; ".join(f"{c.name}={c.value}" for c in self.jar)

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
        return self._postear_crudo(programa, pares, referer_ident=referer_ident)

    # -- plomeria ----------------------------------------------------------

    def _postear_crudo(self, programa: str, pares, referer_ident: int | None = None,
                       en_login: bool = False) -> str:
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
        return self._pedir(pedido, en_login=en_login)

    def _pedir(self, pedido: urllib.request.Request, en_login: bool = False) -> str:
        html = self._pedir_una(pedido)

        # Seguir los meta-refresh, como haria el navegador. Solo en el ingreso:
        # fuera de ahi un refresh inesperado es una señal, no algo a seguir a
        # ciegas. Con tope de saltos por si la plataforma cicla.
        if en_login:
            for _ in range(6):
                destino = destino_refresh(html)
                if not destino:
                    break
                url = urllib.parse.urljoin(pedido.full_url, destino)
                html = self._pedir_una(urllib.request.Request(url, method="GET"))

        if not en_login and es_pantalla_de_login(html):
            raise SesionCaida("la plataforma devolvio el login: la sesion vencio")
        return html

    def _pedir_una(self, pedido: urllib.request.Request) -> str:
        pedido.add_header("User-Agent", NAVEGADOR)
        pedido.add_header("Accept-Language", "es-AR,es;q=0.9")
        try:
            with self._abridor.open(pedido, timeout=self.tiempo_max_s) as r:
                crudo = r.read()
        except urllib.error.HTTPError as e:
            raise ErrorDePlataforma(f"HTTP {e.code} en {pedido.full_url}") from e
        except urllib.error.URLError as e:
            raise ErrorDePlataforma(f"no llegue a la plataforma: {e.reason}") from e
        return crudo.decode(CODIFICACION, errors="replace")


def _texto_de_error(m) -> str | None:
    if not m:
        return None
    pista = m.group(1).lower()
    if "inhibid" in pista:
        return ("El usuario quedó inhibido. Lo tiene que destrabar un usuario "
                "Master de tu oficina.")
    if "expirad" in pista:
        return "El código expiró. Pedí uno nuevo."
    if "bloquead" in pista:
        return "El usuario está bloqueado."
    return "Usuario, contraseña o código incorrectos."


def es_pantalla_de_login(html: str) -> bool:
    """Si la respuesta es el login en vez de lo que pedimos.

    Se mira solo el principio: la palabra puede aparecer en cualquier lado de
    una pagina larga, pero el formulario de login esta arriba de todo.
    """
    return bool(_PANTALLA_LOGIN.search(html[:4000]))
