"""Armado del POST de la oferta, sin inventar un solo campo.

La regla es una sola y todo lo demas sale de ahi: **el bot copia, no escribe**.
De los ~70 campos que viajan, el bot solo decide dos — la tasa y la accion. El
resto se relaya tal cual lo mando el servidor, incluidos los comitentes y sus
CUIT, que son el unico lugar donde un error no se arregla pidiendo disculpas.

Es lo mismo que hace la pantalla. `ofertaCompra()` copia los campos por cheque
desde el iframe al form principal y postea; aca se hace igual, y despues se
verifica que ningun campo no editable quedo con un valor que el bot haya
inventado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Los unicos campos que el bot decide.
CAMPOS_EDITABLES = frozenset({"action", "id", "tasa"})

# Los que el form principal trae vacios y hay que traer del iframe, igual que
# hace ofertaCompra() en la pantalla.
PREFIJOS_POR_CHEQUE = ("comitcpr", "cuitcpr", "excepcpr", "condcpr")

ALTA_COMPRA = "altaCompra"
BAJA_COMPRA = "bajaCompra"

_INPUT = re.compile(r"<input\b([^>]*)>", re.I)
_ATRIBUTO = re.compile(
    r"""([A-Za-z_:][-\w:.]*)      # nombre
        (?:\s*=\s*
           (?:"([^"]*)"           # "valor"
             |'([^']*)'           # 'valor'
             |([^\s"'=<>`]+)))?   # valor suelto""",
    re.X,
)


class FormularioIlegible(Exception):
    """El HTML no tiene la forma esperada. Se para en vez de improvisar."""


class CampoProhibido(Exception):
    """Alguien intento cambiar un campo que el bot no tiene permitido tocar."""


def _atributos(crudo: str) -> dict[str, str]:
    """Atributos de un tag, con la primera aparicion ganando.

    No es un detalle: los inputs del iframe de MAV traen `name=` dos veces
    (`name="comitcpr02579750" name="hidcomitcpr02579750"`). El navegador se
    queda con el primero, asi que nosotros tambien. Quedarse con el ultimo
    postearia los campos con otro nombre y la oferta saldria sin comitente.
    """
    salida: dict[str, str] = {}
    for m in _ATRIBUTO.finditer(crudo):
        clave = m.group(1).lower()
        if clave in salida:
            continue
        salida[clave] = m.group(2) or m.group(3) or m.group(4) or ""
    return salida


def parsear_campos(html: str) -> dict[str, str]:
    """Todos los <input> con nombre, como name -> value.

    Un input sin `value` vale "". La pantalla tiene algunos escritos mal
    (`value"No"`, sin el igual); se leen como vacios, que es lo que hace el
    navegador.
    """
    campos: dict[str, str] = {}
    for m in _INPUT.finditer(html):
        attrs = _atributos(m.group(1))
        nombre = attrs.get("name")
        if not nombre:
            continue
        campos.setdefault(nombre, attrs.get("value", ""))
    return campos


def campos_por_cheque(campos: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in campos.items() if k.startswith(PREFIJOS_POR_CHEQUE)}


@dataclass(frozen=True)
class Payload:
    campos: dict[str, str]

    def pares(self) -> list[tuple[str, str]]:
        return list(self.campos.items())


def armar_oferta(html_subasta: str, html_cheques: str, tasa: str) -> Payload:
    """El POST de una oferta de compra.

    `tasa` va con coma decimal, como la escribe el trader y como la espera la
    plataforma; el JS rechaza el punto antes de postear.
    """
    pagina = parsear_campos(html_subasta)
    iframe = parsear_campos(html_cheques)

    if "ident" not in pagina:
        raise FormularioIlegible("la pantalla de subasta no trae 'ident'")
    if "tasa" not in pagina:
        raise FormularioIlegible("la pantalla de subasta no trae el campo 'tasa'")

    del_iframe = campos_por_cheque(iframe)
    if not del_iframe:
        raise FormularioIlegible(
            "el iframe de cheques no trae comitentes; sin eso no se arma la oferta"
        )

    campos = dict(pagina)
    for nombre, valor in del_iframe.items():
        if nombre in campos:
            campos[nombre] = valor

    faltantes = [k for k in campos_por_cheque(campos) if not campos[k].strip()]
    if faltantes:
        # La pantalla exige comitente y CUIT por cheque. Si el servidor los
        # mando vacios es que algo no esta cargado, y no lo vamos a completar
        # nosotros.
        raise FormularioIlegible(
            f"hay {len(faltantes)} campo(s) por cheque sin valor: {faltantes[:3]}"
        )

    # El JS de la pantalla exige que el comitente sea numerico y distinto de
    # cero. El bot postea directo, asi que la validacion la tiene que hacer el.
    # Un "0" no lo agarra el control de arriba — no esta vacio — y es
    # exactamente como se ve un comitente que no quedo cargado.
    for nombre, valor in campos.items():
        if not nombre.startswith("comitcpr"):
            continue
        limpio = valor.strip()
        if not limpio.isdigit() or int(limpio) == 0:
            raise FormularioIlegible(
                f"el comitente de {nombre!r} es {valor!r}, que no es un numero "
                f"de comitente valido"
            )

    campos["tasa"] = tasa
    campos["action"] = ALTA_COMPRA

    verificar(campos, pagina, iframe)
    return Payload(campos)


def verificar(campos: dict[str, str], pagina: dict[str, str],
              iframe: dict[str, str]) -> None:
    """Confirma que el bot no invento nada.

    Todo campo que no sea editable tiene que valer exactamente lo que mando el
    servidor, en la pantalla o en el iframe. Es el reemplazo del control de
    preview que esta plataforma no tiene, y corre antes de cada POST.
    """
    for nombre, valor in campos.items():
        if nombre in CAMPOS_EDITABLES:
            continue
        origenes = {v for v in (pagina.get(nombre), iframe.get(nombre))
                    if v is not None}
        if valor not in origenes:
            raise CampoProhibido(
                f"el campo {nombre!r} quedo en {valor!r}, que no es lo que "
                f"mando el servidor ({sorted(origenes)!r})"
            )

    nuevos = set(campos) - set(pagina) - CAMPOS_EDITABLES
    if nuevos:
        raise CampoProhibido(f"campos que la pantalla no tenia: {sorted(nuevos)}")


def armar_baja(html_subasta: str, id_oferta: int) -> Payload:
    """El POST para dar de baja una oferta propia."""
    campos = dict(parsear_campos(html_subasta))
    if "ident" not in campos:
        raise FormularioIlegible("la pantalla de subasta no trae 'ident'")
    campos["action"] = BAJA_COMPRA
    campos["id"] = str(id_oferta)
    return Payload(campos)


@dataclass(frozen=True)
class Form:
    """Un <form> leido tal cual, para poder devolverlo completo.

    Se usa en el login: no sabemos de antemano como se llama el campo del
    codigo 2FA, asi que en vez de adivinarlo se lee el formulario que manda el
    servidor, se completan los campos visibles y se reenvia lo demas intacto.
    Es la misma regla de siempre: copiar, no escribir.
    """

    action: str
    campos: dict[str, str]
    visibles: tuple[str, ...]


_FORM = re.compile(r"<form\b([^>]*)>(.*?)</form>", re.I | re.S)


def parsear_form(html: str, con_campo: str | None = None) -> Form:
    """El primer <form> del HTML, o el primero que tenga `con_campo`."""
    for m in _FORM.finditer(html):
        attrs = _atributos(m.group(1))
        cuerpo = m.group(2)
        campos = parsear_campos(cuerpo)
        if con_campo and con_campo not in campos:
            continue
        visibles = []
        for i in _INPUT.finditer(cuerpo):
            a = _atributos(i.group(1))
            nombre = a.get("name")
            tipo = (a.get("type") or "text").lower()
            if nombre and tipo in ("text", "password", "tel", "number"):
                visibles.append(nombre)
        return Form(action=attrs.get("action", ""), campos=campos,
                    visibles=tuple(visibles))
    raise FormularioIlegible(
        f"no encontre un formulario{' con ' + con_campo if con_campo else ''}")
