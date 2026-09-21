"""Varias subastas vigiladas a la vez, sobre una sola sesión.

MAV admite una sesión por usuario, así que no hay forma de paralelizar por
conexión: los pedidos salen en serie igual. Lo que sí se puede — y es lo que
faltaba — es que ninguna subasta quede esperando por otra.

Cada vigilante dice cuándo quiere volver a mirar. La mesa atiende al que le toca
antes, uno por vuelta. Con el sondeo en 2s y cinco subastas, cada una se
refresca cada 2s y los pedidos quedan repartidos, no en ráfaga.
"""

from __future__ import annotations

import random
import time as reloj

from .config import ConfigSubasta
from .sesion import Sesion
from .vigilante import Vigilante, Vista


class Mesa:
    def __init__(self, sesion: Sesion, log, azar: random.Random | None = None):
        self.sesion = sesion
        self.log = log
        self.azar = azar or random.Random()
        self.vigilantes: dict[int, Vigilante] = {}

    # -- alta y baja -------------------------------------------------------

    def sumar(self, cfg: ConfigSubasta, vivo: bool) -> Vigilante:
        """Agrega una subasta. Si ya estaba, la reemplaza con la config nueva."""
        v = Vigilante(cfg, self.sesion, vivo=vivo, log=self.log, azar=self.azar)
        self.vigilantes[cfg.ident] = v
        self.log("alta", f"[{cfg.ident}] vigilando en modo "
                         f"{'VIVO' if vivo else 'SOMBRA'}")
        return v

    def sacar(self, ident: int, motivo: str = "sacada a mano") -> None:
        v = self.vigilantes.pop(ident, None)
        if v is not None:
            v.parar(motivo)
            self.log("baja", f"[{ident}] {motivo}")

    def parar_todo(self, motivo: str = "parado a mano") -> None:
        """Kill switch. Frena las cotizaciones nuevas de todas las subastas.

        No puede des-enviar un POST en vuelo: lo que quedó a mitad de camino se
        resuelve releyendo el libro, no asumiendo.
        """
        for v in self.vigilantes.values():
            v.parar(motivo)

    # -- el latido ---------------------------------------------------------

    @property
    def activos(self) -> list[Vigilante]:
        return [v for v in self.vigilantes.values() if not v.terminado]

    def tick(self, ahora_s: float | None = None) -> bool:
        """Atiende a lo sumo una subasta. Devuelve si hizo algo.

        Una por vuelta, y no todas juntas: así un pedido lento de una no
        retrasa al resto más de lo inevitable, y la cola de la sesión no se
        llena de ráfagas.
        """
        ahora_s = reloj.monotonic() if ahora_s is None else ahora_s
        pendientes = [v for v in self.activos if v.listo_para(ahora_s)]
        if not pendientes:
            return False
        # El que hace más rato que espera va primero.
        pendientes.sort(key=lambda v: v.proxima_s)
        pendientes[0].tick(ahora_s)
        return True

    def dormir_hasta(self, ahora_s: float | None = None) -> float:
        """Cuánto puede descansar el hilo sin llegar tarde a nada."""
        ahora_s = reloj.monotonic() if ahora_s is None else ahora_s
        activos = self.activos
        if not activos:
            return 0.5
        falta = min(v.proxima_s for v in activos) - ahora_s
        return max(0.0, min(falta, 0.5))

    # -- para la interfaz --------------------------------------------------

    def vistas(self, ahora_s: float | None = None) -> list[Vista]:
        ahora_s = reloj.monotonic() if ahora_s is None else ahora_s
        return [v.vista(ahora_s)
                for v in sorted(self.vigilantes.values(), key=lambda v: v.cfg.ident)]
