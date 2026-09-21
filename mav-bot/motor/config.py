"""Configuracion de una subasta que el bot va a cuidar.

El trader carga a mano el comitente y la primera oferta. Esto solo describe
hasta donde puede llegar el bot y con que modales.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .libro import PASO

# Banda de tasas plausibles. No es un limite operativo, es el cinturon contra un
# error de parseo: si leer "26,99" da 2699, la decision se aborta antes de
# convertirse en una oferta de 2698,99.
TASA_MIN_ABSOLUTA = Decimal("-50")
TASA_MAX_ABSOLUTA = Decimal("500")


class ConfigInvalida(Exception):
    pass


@dataclass(frozen=True)
class ConfigSubasta:
    ident: int
    """Numero de subasta. El bot no toca ninguna otra."""

    piso: Decimal
    """Tasa minima que aceptamos.

    Gana la tasa mas baja, asi que el piso es el limite de abajo: el punto donde
    dejamos de pelear y cedemos el cheque. El bot nunca ofrece por debajo.
    """

    decremento_min: Decimal = PASO
    decremento_max: Decimal = PASO
    """Cuanto bajamos por debajo de la mejor punta ajena.

    Aleatorio dentro del rango. Bajar siempre exactamente un centavo convierte al
    bot en algo predecible, y un rival puede buscarte el piso por sondeo: te tira
    una tasa, mira si contestas, baja, repite. Con dos o tres subastas ya sabe
    donde te plantas.
    """

    prob_respuesta: float = 1.0
    """Probabilidad de contestar una vuelta. Por debajo de 1 el bot a veces
    aguanta, que es la otra mitad de no ser predecible."""

    espera_min_s: float = 0.0
    espera_max_s: float = 0.0
    """Demora antes de recotizar. Contestar siempre en 300ms es una firma que se
    aprende en una tarde. Contra humanos, ademas, no sirve de nada."""

    max_recotizaciones: int = 60
    """Tope por subasta y por sesion. Cualquier loop se choca contra esto."""

    intervalo_min_s: float = 10.0
    """Piso de tiempo entre dos recotizaciones de la misma subasta."""

    antiguedad_max_libro_s: float = 30.0
    """Si la ultima lectura del libro es mas vieja que esto, no se cotiza.
    Nunca actuar sobre un libro viejo."""

    tasa_min_absoluta: Decimal = TASA_MIN_ABSOLUTA
    tasa_max_absoluta: Decimal = TASA_MAX_ABSOLUTA

    def __post_init__(self) -> None:
        if self.ident <= 0:
            raise ConfigInvalida("el numero de subasta tiene que ser positivo")
        if not (self.tasa_min_absoluta <= self.piso <= self.tasa_max_absoluta):
            raise ConfigInvalida(
                f"el piso {self.piso} cae fuera de la banda plausible "
                f"[{self.tasa_min_absoluta}, {self.tasa_max_absoluta}]"
            )
        if self.decremento_min < PASO:
            raise ConfigInvalida(f"el decremento minimo no puede ser menor a {PASO}")
        if self.decremento_max < self.decremento_min:
            raise ConfigInvalida("decremento_max no puede ser menor que decremento_min")
        if not (0.0 < self.prob_respuesta <= 1.0):
            raise ConfigInvalida("prob_respuesta tiene que estar en (0, 1]")
        if self.espera_min_s < 0 or self.espera_max_s < self.espera_min_s:
            raise ConfigInvalida("la ventana de espera es invalida")
        if self.max_recotizaciones <= 0:
            raise ConfigInvalida("max_recotizaciones tiene que ser positivo")
        if self.intervalo_min_s < 0:
            raise ConfigInvalida("intervalo_min_s no puede ser negativo")
        if self.antiguedad_max_libro_s <= 0:
            raise ConfigInvalida("antiguedad_max_libro_s tiene que ser positivo")
