"""Reconstruccion de una guerra de tasas a partir de capturas.

Cada oferta viaja con su propio horario, asi que la union de varias capturas
reconstruye la secuencia completa aunque las capturas sean salteadas. Con eso se
puede medir cuanto tarda cada agente en contestar, que es lo que separa a un
humano de un bot, y correr el motor contra una partida que ya paso.

Ojo con la propiedad de las ofertas: el link de baja desaparece cuando la
subasta cierra, asi que para analizar partidas terminadas hay que decir cual es
el agente propio. En vivo el bot no usa esto — usa el link, que es mas preciso.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import time
from decimal import Decimal

from .libro import Libro, Oferta

# Un rival que contesta sistematicamente por debajo de esto no es una persona.
SEGUNDOS_BOT = 10.0
# Y uno que tarda mas que esto, sistematicamente, si lo es.
SEGUNDOS_HUMANO = 45.0
MUESTRAS_MINIMAS = 3


@dataclass(frozen=True)
class Reaccion:
    """Una respuesta: alguien cotizo, y despues otro le contesto."""

    provocador: Oferta
    respuesta: Oferta
    demora_s: float
    recorte: Decimal      # cuanto bajo respecto del provocador


def _a_segundos(t: time) -> float:
    """Segundos desde medianoche.

    Las capturas traen la hora sin fecha, asi que las diferencias valen dentro
    de una misma rueda. Alcanza: ninguna subasta cruza la medianoche.
    """
    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6


@dataclass(frozen=True)
class Historia:
    ident: int
    ofertas: tuple[Oferta, ...]     # todas las vistas, ordenadas por hora
    retiradas: frozenset[int]       # las que desaparecieron antes del final
    libros: tuple[Libro, ...]       # las capturas, en orden

    def vigentes(self) -> tuple[Oferta, ...]:
        return tuple(o for o in self.ofertas if o.id not in self.retiradas)


def _hora_captura(lib: Libro) -> time:
    """Proxy del momento de la captura: la oferta mas nueva que contiene."""
    return max((o.ingreso for o in lib.ofertas), default=time.min)


def fusionar(libros: list[Libro]) -> Historia:
    """Une varias capturas en una sola linea de tiempo, sin repetir ofertas.

    Una oferta que aparecia en una captura y falta en la ultima fue dada de
    baja. Hay que marcarlas: si no, la union mezcla ofertas que nunca
    convivieron y salen "reacciones" que en la pantalla nadie vio.
    """
    if not libros:
        raise ValueError("no hay capturas para fusionar")
    idents = {lib.ident for lib in libros}
    if len(idents) > 1:
        raise ValueError(f"las capturas son de subastas distintas: {sorted(idents)}")

    ordenados = tuple(sorted(libros, key=_hora_captura))

    # La clave incluye la tasa y la hora, no solo el id: el libro guarda las
    # ofertas vivas y nada mas, y una recotizacion reescribe la oferta en el
    # lugar. Deduplicar por id solo se comeria todos los pasos de la guerra y
    # dejaria un unico movimiento donde hubo cincuenta.
    estados: dict[tuple[int, Decimal, time], Oferta] = {}
    fue_propia: set[int] = set()
    for lib in ordenados:
        for o in lib.ofertas:
            estados[(o.id, o.tasa, o.ingreso)] = o
            if o.propia:
                # El link de baja se pierde cuando la subasta cierra, asi que
                # alcanza con haberla visto propia una vez.
                fue_propia.add(o.id)

    ofertas = tuple(sorted(
        (o if o.propia or o.id not in fue_propia
         else Oferta(id=o.id, agente=o.agente, tasa=o.tasa,
                     ingreso=o.ingreso, propia=True)
         for o in estados.values()),
        key=lambda o: (o.ingreso, o.id),
    ))

    ultima = ordenados[-1]
    vivos = {o.id for o in ultima.ofertas}
    corte = _hora_captura(ultima)
    retiradas = frozenset(
        o.id for o in ofertas if o.id not in vivos and o.ingreso <= corte
    )

    return Historia(
        ident=ordenados[0].ident,
        ofertas=ofertas,
        retiradas=retiradas,
        libros=ordenados,
    )


def reacciones(historia: Historia, agente: str) -> list[Reaccion]:
    """Las veces que `agente` contesto a la oferta de otro.

    Se toma como provocador la ultima oferta ajena anterior a la suya. No es
    exacto — puede haber habido varias — pero es la lectura que tendria alguien
    mirando la pantalla, que es justo lo que queremos medir.

    Solo cuentan las respuestas que mejoran la punta del otro. Una oferta que
    deja la tasa igual o mas alta no es una contestacion: es una oferta suelta
    que quedo cerca en el tiempo.
    """
    linea = historia.ofertas
    salida = []
    for i, o in enumerate(linea):
        if o.agente != agente:
            continue
        previas = [p for p in linea[:i] if p.agente != agente]
        if not previas:
            continue
        provocador = previas[-1]
        recorte = provocador.tasa - o.tasa
        if recorte <= 0:
            continue
        salida.append(Reaccion(
            provocador=provocador,
            respuesta=o,
            demora_s=_a_segundos(o.ingreso) - _a_segundos(provocador.ingreso),
            recorte=recorte,
        ))
    return salida


def clasificar(demoras: list[float]) -> tuple[str, str]:
    """(veredicto, explicacion) a partir de los tiempos de respuesta.

    Contra un bot la guerra termina en el piso de alguien, garantizado, asi que
    pelear round por round es regalar el camino. Contra un humano que se cansa,
    en cambio, es justamente donde esta el negocio. Por eso conviene saberlo.
    """
    if len(demoras) < MUESTRAS_MINIMAS:
        return "desconocido", f"solo {len(demoras)} respuesta(s), hacen falta {MUESTRAS_MINIMAS}"

    mediana = statistics.median(demoras)
    dispersion = statistics.pstdev(demoras)

    if mediana <= SEGUNDOS_BOT and dispersion <= SEGUNDOS_BOT:
        return "bot", f"contesta en {mediana:.0f}s de mediana y casi sin variacion"
    if mediana >= SEGUNDOS_HUMANO:
        return "humano", f"tarda {mediana:.0f}s de mediana"
    if dispersion >= mediana:
        return "humano", f"mediana {mediana:.0f}s pero muy irregular (±{dispersion:.0f}s)"
    return "desconocido", f"mediana {mediana:.0f}s, dispersion {dispersion:.0f}s: no concluyente"
