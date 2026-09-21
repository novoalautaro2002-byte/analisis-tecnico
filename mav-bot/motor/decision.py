"""Que hacer con el libro que acabamos de leer.

Funcion pura: mismo libro, misma config y mismo generador de azar dan siempre la
misma decision. Todo lo que tiene efecto — esperar, escribir en la pantalla,
apretar el boton — vive afuera.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from .config import ConfigSubasta
from .libro import PASO, Libro, Oferta


class Accion(Enum):
    NADA = "nada"
    RECOTIZAR = "recotizar"
    CEDER = "ceder"
    SIN_OFERTA = "sin_oferta"


@dataclass(frozen=True)
class Decision:
    accion: Accion
    motivo: str
    tasa: Decimal | None = None
    mia: Oferta | None = None
    rival: Oferta | None = None


class SubastaEquivocada(Exception):
    """El libro leido no es el de la subasta configurada."""


def _decremento(cfg: ConfigSubasta, azar: random.Random) -> Decimal:
    """Un decremento al azar dentro del rango, cuantizado al centavo."""
    if cfg.decremento_max == cfg.decremento_min:
        return cfg.decremento_min
    span = cfg.decremento_max - cfg.decremento_min
    saltos = int((span / PASO).to_integral_value(rounding=ROUND_HALF_UP))
    return cfg.decremento_min + PASO * azar.randint(0, saltos)


def decidir(libro: Libro, cfg: ConfigSubasta, azar: random.Random) -> Decision:
    if libro.ident != cfg.ident:
        # Fallar cerrado: mejor parar que cotizar en la subasta equivocada.
        raise SubastaEquivocada(f"config para {cfg.ident}, libro de {libro.ident}")

    mia = libro.mejor_propia()
    if mia is None:
        # Sin oferta cargada a mano no hay nada que modificar. La whitelist es
        # fisica: el bot no puede entrar donde el trader no entro antes.
        return Decision(Accion.SIN_OFERTA, "no tengo ninguna oferta viva en esta subasta")

    rival = libro.mejor_ajena()
    if rival is None:
        return Decision(Accion.NADA, "soy el unico en el libro", mia=mia)

    if mia.tasa < rival.tasa:
        return Decision(Accion.NADA, "mi tasa ya es la mejor", mia=mia, rival=rival)

    if mia.tasa == rival.tasa and mia.ingreso < rival.ingreso:
        # Empate con prioridad temporal. Bajar seria regalar un centavo por una
        # posicion que ya tenemos.
        return Decision(
            Accion.NADA,
            f"empate en {mia.tasa} pero entre antes ({mia.ingreso} vs {rival.ingreso})",
            mia=mia, rival=rival,
        )

    # A partir de aca hay que mejorar la oferta.

    # Ni el centavo minimo alcanza sin perforar el piso: se cede el cheque.
    if rival.tasa - PASO < cfg.piso:
        return Decision(
            Accion.CEDER,
            f"para superar {rival.tasa} habria que perforar el piso {cfg.piso}",
            mia=mia, rival=rival,
        )

    if azar.random() > cfg.prob_respuesta:
        return Decision(Accion.NADA, "aguanto esta vuelta", mia=mia, rival=rival)

    # El piso recorta el decremento en vez de anular la jugada: si todavia queda
    # aire para ganar, no se regala el cheque por como salio el azar.
    objetivo = max(cfg.piso, (rival.tasa - _decremento(cfg, azar)).quantize(PASO))

    return Decision(
        Accion.RECOTIZAR,
        f"{rival.tasa} del agente {rival.agente} me supera; bajo a {objetivo}",
        tasa=objetivo, mia=mia, rival=rival,
    )
