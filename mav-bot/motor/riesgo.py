"""El gate de riesgo: lo ultimo que corre antes de tocar la pantalla.

El plan original tenia un control mejor — comparar la pantalla de preview contra
lo que el bot creia estar mandando — pero en esta plataforma no hay preview: el
alta es un solo POST. Asi que el chequeo se hace sobre la decision ya tomada, y
despues se verifica releyendo el libro.

Todo lo que no pase por aca no llega a la plataforma. Cada rechazo dice por que,
porque el log de rechazos es la mitad del valor del dry-run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .config import ConfigSubasta
from .decision import Accion, Decision


@dataclass(frozen=True)
class Veredicto:
    ok: bool
    motivo: str

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class EstadoSesion:
    """Lo que el gate necesita recordar entre vueltas."""

    kill: bool = False
    recotizaciones: dict[int, int] = field(default_factory=dict)
    ultima_s: dict[int, float] = field(default_factory=dict)

    def registrar(self, ident: int, ahora_s: float) -> None:
        """Se llama despues de una recotizacion efectiva, no antes."""
        self.recotizaciones[ident] = self.recotizaciones.get(ident, 0) + 1
        self.ultima_s[ident] = ahora_s

    def detener(self) -> None:
        """Kill switch. Frena las cotizaciones nuevas.

        No puede des-enviar un POST en vuelo: lo que quedo a mitad de camino se
        resuelve releyendo el libro, no asumiendo.
        """
        self.kill = True


def evaluar(
    decision: Decision,
    cfg: ConfigSubasta,
    estado: EstadoSesion,
    ahora_s: float,
    antiguedad_libro_s: float,
) -> Veredicto:
    if estado.kill:
        return Veredicto(False, "kill switch activado")

    if decision.accion is not Accion.RECOTIZAR:
        return Veredicto(False, f"la decision no es recotizar sino {decision.accion.value}")

    tasa = decision.tasa
    if tasa is None:
        return Veredicto(False, "decision de recotizar sin tasa")

    # Cinturon contra errores de parseo. Una tasa de 2698,99 nunca es real: es
    # "26,99" leido mal. Antes que ofertarla, parar.
    if not (cfg.tasa_min_absoluta <= tasa <= cfg.tasa_max_absoluta):
        return Veredicto(
            False,
            f"tasa {tasa} fuera de la banda plausible "
            f"[{cfg.tasa_min_absoluta}, {cfg.tasa_max_absoluta}]",
        )

    if tasa < cfg.piso:
        return Veredicto(False, f"tasa {tasa} por debajo del piso {cfg.piso}")

    # Monotonia: dentro de una subasta la tasa solo baja. Si el bot quiere subir,
    # algo se leyo mal.
    if decision.mia is not None and tasa >= decision.mia.tasa:
        return Veredicto(
            False,
            f"tasa {tasa} no mejora la propia {decision.mia.tasa}",
        )

    # Nunca decidir sobre un libro viejo.
    if antiguedad_libro_s > cfg.antiguedad_max_libro_s:
        return Veredicto(
            False,
            f"el libro tiene {antiguedad_libro_s:.1f}s, mas que el maximo "
            f"de {cfg.antiguedad_max_libro_s:.1f}s",
        )

    hechas = estado.recotizaciones.get(cfg.ident, 0)
    if hechas >= cfg.max_recotizaciones:
        return Veredicto(
            False, f"ya hice {hechas} recotizaciones, el tope es {cfg.max_recotizaciones}"
        )

    ultima = estado.ultima_s.get(cfg.ident)
    if ultima is not None:
        transcurrido = ahora_s - ultima
        if transcurrido < cfg.intervalo_min_s:
            return Veredicto(
                False,
                f"pasaron {transcurrido:.1f}s desde la ultima, el minimo "
                f"es {cfg.intervalo_min_s:.1f}s",
            )

    return Veredicto(True, f"habilitado a cotizar {tasa}")


def verificar_despues(libro_nuevo, cfg: ConfigSubasta, tasa_esperada: Decimal) -> Veredicto:
    """Relee el libro despues de cotizar y confirma que entro lo que queriamos.

    Reemplaza al control de preview que esta plataforma no tiene. Si no coincide,
    el bot para: puede ser que el POST no haya entrado, que haya entrado otra
    cosa, o que alguien se haya metido en el medio. Ninguna de las tres se
    resuelve reintentando a ciegas.
    """
    if libro_nuevo.ident != cfg.ident:
        return Veredicto(False, f"el libro releido es de la subasta {libro_nuevo.ident}")

    mia = libro_nuevo.mejor_propia()
    if mia is None:
        return Veredicto(False, "despues de cotizar no tengo ninguna oferta viva")
    if mia.tasa != tasa_esperada:
        return Veredicto(
            False, f"esperaba mi oferta en {tasa_esperada} y la leo en {mia.tasa}"
        )
    return Veredicto(True, f"confirmado: mi oferta quedo en {mia.tasa}")
