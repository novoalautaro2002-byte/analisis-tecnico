"""Contrato que cumple todo parser."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..modelo import Movimiento

_REGISTRO: list["Parser"] = []


def registrar(cls):
    """Decorador de clase: la instancia y la suma al registro."""
    _REGISTRO.append(cls())
    return cls


def registro() -> list["Parser"]:
    return list(_REGISTRO)


@dataclass
class ResultadoParseo:
    movimientos: list[Movimiento] = field(default_factory=list)
    # Lineas que parecian un movimiento pero no se pudieron interpretar.
    # No se descartan en silencio: son lo que hay que mirar para mejorar
    # el parser, y su cantidad es la medida de si el parser esta sano.
    sin_parsear: list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)

    @property
    def tasa_exito(self) -> float:
        total = len(self.movimientos) + len(self.sin_parsear)
        return len(self.movimientos) / total if total else 1.0


class Parser:
    """Base de todos los parsers.

    `especificidad` decide el orden de deteccion: mas alto gana. Un parser de
    un emisor concreto va en 100; el generico, en 0.
    """

    nombre: str = "base"
    especificidad: int = 50

    def reconoce(self, doc) -> bool:
        raise NotImplementedError

    def parsear(self, doc) -> ResultadoParseo:
        raise NotImplementedError

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Parser {self.nombre}>"
