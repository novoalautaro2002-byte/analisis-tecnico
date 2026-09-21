"""La fila del listado: todo lo que MAV sabe de una subasta, sin abrirla.

`cpd-subastas-api.p` devuelve, por subasta, bastante mas de lo que su propia
pantalla muestra. Lo que importa acá:

    ident            numero de subasta
    estado           Activa / Negociada / Desierta / ...
    ya-negociado     si ya hubo concertacion
    tasa-cpr         la mejor tasa compradora, que es donde pasa la guerra
    agente-cpr       el agente que la tiene puesta
    agente-vdr       el agente vendedor del lote
    tiempo-minimo    T.Min: la hora desde la cual corren los 3 minutos
    hora-cierre      cierre previsto (se corre con cada mejora)
    cantidad-cheques, monto, segmento, moneda

Dos consecuencias practicas:

1. Un solo request trae el tablero entero. Antes cada subasta gastaba un pedido
   propio solo para preguntar si seguia activa.
2. `(estado, tasa-cpr, agente-cpr)` alcanza como huella para saber si el libro
   *pudo* haber cambiado. Cuando no cambio, no hace falta abrir la subasta.

Ojo con lo segundo: es una optimizacion, no una fuente de verdad. Quien decide
sigue siendo el libro. El vigilante contrasta las dos cosas cada vez que lee el
libro y, si alguna vez no coinciden, apaga el atajo y vuelve a leer siempre.

Nada de lo que hay acá levanta excepciones: el listado es informativo. Un campo
que no se entiende queda en None y el bot sigue con lo que si entiende.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Estados en los que el bot puede operar. Cualquier otro (negociada, desierta,
# anulada, vencida) significa que no hay nada que defender.
ESTADOS_VIVOS = ("activa", "activas")


def _texto(fila: dict, clave: str) -> str:
    valor = fila.get(clave)
    return "" if valor is None else str(valor).strip()


def _numero(fila: dict, clave: str) -> Decimal | None:
    """Progress manda a veces 25.44 y a veces "25,44". Los dos sirven."""
    crudo = _texto(fila, clave)
    if not crudo:
        return None
    if "," in crudo:                       # formato local: coma decimal
        crudo = crudo.replace(".", "").replace(",", ".")
    try:
        valor = Decimal(crudo)
    except InvalidOperation:
        return None
    return valor if valor.is_finite() else None


def _entero(fila: dict, clave: str) -> int | None:
    crudo = _texto(fila, clave)
    try:
        return int(float(crudo.replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _booleano(fila: dict, clave: str) -> bool:
    crudo = _texto(fila, clave).lower()
    return crudo in ("yes", "si", "sí", "true", "1", "s")


@dataclass(frozen=True)
class Ficha:
    ident: int
    estado: str = ""
    segmento: str = ""
    moneda: str = ""
    tasa_cpr: Decimal | None = None
    agente_cpr: str = ""
    tasa_vdr: Decimal | None = None
    agente_vdr: str = ""
    tiempo_minimo: str = ""
    tiempo_minimo_ss: int | None = None
    hora_cierre: str = ""
    hora_cierre_ss: int | None = None
    cantidad_cheques: int | None = None
    monto: Decimal | None = None
    ya_negociado: bool = False

    @property
    def viva(self) -> bool:
        """Sin estado legible se asume viva: no frenamos por no entender."""
        return not self.estado or self.estado.lower() in ESTADOS_VIVOS

    @property
    def huella(self) -> tuple:
        """Lo que tiene que cambiar para que valga la pena abrir la subasta.

        La tasa compradora y quien la tiene: si un rival mejora, cambia una de
        las dos. Si empata sin destronar a nadie, no cambia ninguna — y no hay
        nada que contestar.
        """
        return (self.estado.lower(), self.tasa_cpr, self.agente_cpr,
                self.ya_negociado)


def leer_ficha(fila: dict) -> Ficha | None:
    ident = _entero(fila, "ident")
    if not ident:
        return None
    return Ficha(
        ident=ident,
        estado=_texto(fila, "estado"),
        segmento=_texto(fila, "segmento"),
        moneda=_texto(fila, "moneda-signo") or _texto(fila, "moneda"),
        tasa_cpr=_numero(fila, "tasa-cpr"),
        agente_cpr=_texto(fila, "agente-cpr"),
        tasa_vdr=_numero(fila, "tasa-vdr"),
        agente_vdr=_texto(fila, "agente-vdr"),
        tiempo_minimo=_texto(fila, "tiempo-minimo"),
        tiempo_minimo_ss=_entero(fila, "tiempo-minimo-ss"),
        hora_cierre=_texto(fila, "hora-cierre"),
        hora_cierre_ss=_entero(fila, "hora-cierre-ss"),
        cantidad_cheques=_entero(fila, "cantidad-cheques"),
        monto=_numero(fila, "monto"),
        ya_negociado=_booleano(fila, "ya-negociado"),
    )
