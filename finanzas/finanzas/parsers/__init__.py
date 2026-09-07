"""Registro de parsers por emisor.

Un parser sabe dos cosas y nada mas: reconocer si un documento es suyo, y
convertirlo en Movimientos. No conoce la base ni al resto de los parsers.

Para sumar un emisor nuevo alcanza con crear un modulo aca, decorar la clase
con @registrar e importarlo abajo. Nada mas del sistema cambia.
"""

from __future__ import annotations

from .base import Parser, ResultadoParseo, registrar, registro  # noqa: F401

# Importar los modulos los auto-registra via el decorador.
from . import galicia, tarjeta_generica  # noqa: E402,F401


def detectar(doc) -> Parser | None:
    """Devuelve el parser que reconoce el documento, o None.

    Se prueban de mayor a menor especificidad: un parser de un emisor concreto
    tiene que ganarle siempre al generico, que es el ultimo recurso.
    """
    candidatos = sorted(registro(), key=lambda p: -p.especificidad)
    for parser in candidatos:
        try:
            if parser.reconoce(doc):
                return parser
        except Exception:
            continue
    return None
