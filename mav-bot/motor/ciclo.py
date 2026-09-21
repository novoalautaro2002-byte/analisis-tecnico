"""El ciclo del bot, en un solo lugar.

La consola y la interfaz usan esto mismo. El camino critico — leer, decidir,
pasar el gate, cotizar, verificar — no puede existir en dos versiones que se
vayan separando con el tiempo.
"""

from __future__ import annotations

import random
import time as reloj
from dataclasses import dataclass
from enum import Enum

from .config import ConfigSubasta
from .decision import Accion, decidir
from .libro import Libro, formatear_tasa
from .pantalla import buscar_marco, cotizar, leer_libro
from .riesgo import EstadoSesion, evaluar, verificar_despues

INTERVALO_TRANQUILO_S = 5.0
INTERVALO_ACTIVO_S = 2.0
SEGUIR_ACTIVO_S = 120.0


class Resultado(Enum):
    SIN_PANTALLA = "sin_pantalla"
    MIRANDO = "mirando"
    HARIA = "haria"             # sombra: cotizaria, pero no toca
    COTIZO = "cotizo"
    BLOQUEADO = "bloqueado"     # el gate lo freno
    CEDIDO = "cedido"
    DETENIDO = "detenido"       # algo no cierra: hay que parar


@dataclass
class Paso:
    resultado: Resultado
    detalle: str
    libro: Libro | None = None
    tasa: str | None = None

    @property
    def terminal(self) -> bool:
        return self.resultado in (Resultado.CEDIDO, Resultado.DETENIDO)


class Ciclo:
    """Una subasta vigilada. Guarda el estado entre vueltas."""

    def __init__(self, cfg: ConfigSubasta, vivo: bool, log, azar=None):
        self.cfg = cfg
        self.vivo = vivo
        self.log = log
        self.azar = azar or random.Random()
        self.estado = EstadoSesion()
        self.ultimo_cambio = 0.0
        self.huella = None

    # -- kill switch -------------------------------------------------------

    def parar(self) -> None:
        """Frena las cotizaciones nuevas.

        No puede des-enviar un POST en vuelo: lo que quedo a mitad de camino se
        resuelve releyendo el libro, no asumiendo.
        """
        self.estado.detener()

    @property
    def detenido(self) -> bool:
        return self.estado.kill

    def dormir(self, segundos: float) -> None:
        """Espera, pero mirando el kill switch.

        Una espera de 60s que ignora el boton de parar es un boton que no sirve.
        """
        fin = reloj.monotonic() + segundos
        while reloj.monotonic() < fin and not self.detenido:
            reloj.sleep(min(0.2, fin - reloj.monotonic()))

    @property
    def pausa_sugerida(self) -> float:
        """Rapido mientras el libro se mueve, lento cuando esta quieto.

        Sondear mas rapido no trae informacion nueva — el servidor no actualiza
        mas seguido — y solo te hace visible en sus logs.
        """
        caliente = (reloj.monotonic() - self.ultimo_cambio) < SEGUIR_ACTIVO_S
        return INTERVALO_ACTIVO_S if caliente else INTERVALO_TRANQUILO_S

    # -- una vuelta --------------------------------------------------------

    def tick(self, navegador) -> Paso:
        if self.detenido:
            return Paso(Resultado.DETENIDO, "detenido")

        pagina, marco = buscar_marco(navegador, self.cfg.ident)
        if marco is None:
            return Paso(Resultado.SIN_PANTALLA,
                        f"no encuentro la subasta {self.cfg.ident} abierta en Chrome")

        leido_s = reloj.monotonic()
        libro = leer_libro(marco, self.log)
        if libro is None:
            self.parar()
            return Paso(Resultado.DETENIDO, "no puedo leer el libro con confianza")

        self._anotar_cambio(libro)
        decision = decidir(libro, self.cfg, self.azar)

        if decision.accion is Accion.CEDER:
            self.log("ceder", f"CEDO: {decision.motivo}")
            return Paso(Resultado.CEDIDO, decision.motivo, libro=libro)

        if decision.accion is not Accion.RECOTIZAR:
            return Paso(Resultado.MIRANDO, decision.motivo, libro=libro)

        # Demora deliberada. Contestar al instante no gana nada contra una
        # persona, y deja una firma que se aprende en una tarde.
        espera = self.azar.uniform(self.cfg.espera_min_s, self.cfg.espera_max_s)
        if espera > 0:
            self.log("espera", f"espero {espera:.0f}s antes de mover", segundos=espera)
            self.dormir(espera)
            if self.detenido:
                return Paso(Resultado.DETENIDO, "detenido durante la espera")

            # Releer: en esos segundos el libro pudo cambiar, y decidir sobre lo
            # que vimos antes de dormir seria decidir viejo.
            pagina, marco = buscar_marco(navegador, self.cfg.ident)
            if marco is None:
                return Paso(Resultado.SIN_PANTALLA, "la pantalla se cerro mientras esperaba")
            leido_s = reloj.monotonic()
            libro = leer_libro(marco, self.log)
            if libro is None:
                self.parar()
                return Paso(Resultado.DETENIDO, "no puedo leer el libro con confianza")
            self._anotar_cambio(libro)
            decision = decidir(libro, self.cfg, self.azar)
            if decision.accion is not Accion.RECOTIZAR:
                return Paso(Resultado.MIRANDO,
                            f"tras esperar ya no hace falta: {decision.motivo}", libro=libro)

        veredicto = evaluar(decision, self.cfg, self.estado,
                            reloj.monotonic(), reloj.monotonic() - leido_s)
        if not veredicto:
            self.log("bloqueado", f"gate: {veredicto.motivo}")
            return Paso(Resultado.BLOQUEADO, veredicto.motivo, libro=libro)

        texto = formatear_tasa(decision.tasa)

        if not self.vivo:
            self.log("sombra", f"HARIA: cotizar {texto} ({decision.motivo})", tasa=texto)
            # Se cuenta igual que en vivo, para que los topes y el intervalo
            # minimo se comporten como se van a comportar de verdad.
            self.estado.registrar(self.cfg.ident, reloj.monotonic())
            return Paso(Resultado.HARIA, decision.motivo, libro=libro, tasa=texto)

        self.log("cotizando", f"cotizo {texto}", tasa=texto)
        cotizar(pagina, marco, decision.tasa, self.log)
        self.estado.registrar(self.cfg.ident, reloj.monotonic())

        # Sin pantalla de preview, la unica verificacion posible es a
        # posteriori: releer y confirmar que entro lo que queriamos.
        _, marco = buscar_marco(navegador, self.cfg.ident)
        confirmacion = leer_libro(marco, self.log) if marco else None
        if confirmacion is None:
            self.parar()
            return Paso(Resultado.DETENIDO, "no pude releer el libro despues de cotizar")

        v = verificar_despues(confirmacion, self.cfg, decision.tasa)
        self.log("verificacion", f"{'ok' if v else 'PARO'}: {v.motivo}", ok=v.ok)
        if not v:
            self.parar()
            return Paso(Resultado.DETENIDO, v.motivo, libro=confirmacion)

        self._anotar_cambio(confirmacion)
        return Paso(Resultado.COTIZO, v.motivo, libro=confirmacion, tasa=texto)

    def _anotar_cambio(self, libro: Libro) -> None:
        huella = tuple((o.id, o.tasa, o.ingreso) for o in libro.ofertas)
        if huella == self.huella:
            return
        self.huella = huella
        self.ultimo_cambio = reloj.monotonic()
        mia = libro.mejor_propia()
        ajena = libro.mejor_ajena()
        self.log("libro",
                 f"libro: mia={formatear_tasa(mia.tasa) if mia else '-'} "
                 f"mejor ajena={formatear_tasa(ajena.tasa) if ajena else '-'}",
                 ofertas=[vars(o) for o in libro.ofertas])
