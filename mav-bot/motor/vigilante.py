"""Una subasta vigilada, sin bloquear nunca.

El diseño anterior dormía adentro del ciclo. Eso traía tres problemas a la vez:
no se podía cuidar más de una subasta (una espera de 60s congelaba al resto),
el botón de parar tardaba lo que durara la siesta, y los tiempos configurados se
sumaban a los del sondeo en vez de respetarse.

Acá `tick()` vuelve enseguida, siempre. Lo que hay que esperar se anota como un
instante futuro y el que llama decide cuándo volver. Así N subastas avanzan
en paralelo sobre una sola sesión.

La otra corrección de fondo: se distingue lo pasajero de lo fatal. Un timeout de
red no frena el bot — se reintenta con backoff. Solo frenan las cosas que
significan que no entendemos el estado del mundo.
"""

from __future__ import annotations

import random
import time as reloj
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .config import ConfigSubasta
from .decision import Accion, decidir
from .formulario import CampoProhibido, FormularioIlegible, armar_oferta
from .libro import Libro, LibroIlegible, formatear_tasa, parsear_libro
from .riesgo import EstadoSesion, evaluar, verificar_despues
from .sesion import ErrorDePlataforma, Sesion, SesionCaida

# Cada cuánto se vuelve a preguntar el estado de la subasta (activa, negociada,
# desierta). Es un request extra, así que no va en cada vuelta.
CADA_ESTADO_S = 45.0

# Fallas de red seguidas antes de darse por vencido. Con backoff, esto da
# alrededor de un minuto de insistencia antes de frenar.
FALLAS_TOLERADAS = 6

ESTADOS_VIVOS = ("activa", "activas")


class Fase(Enum):
    MIRANDO = "mirando"          # leyendo el libro, sin nada que hacer
    ESPERANDO = "esperando"      # decidió cotizar, cumpliendo la demora
    CEDIDO = "cedido"            # tocó el piso
    CERRADA = "cerrada"          # la subasta ya no está activa
    DETENIDO = "detenido"        # algo no cierra; no seguimos

    @property
    def terminal(self) -> bool:
        return self in (Fase.CEDIDO, Fase.CERRADA, Fase.DETENIDO)


@dataclass
class Vista:
    """Lo que la interfaz muestra de esta subasta."""

    ident: int
    fase: str
    detalle: str
    vivo: bool
    recotizaciones: int
    mia: str | None = None
    mejor_ajena: str | None = None
    gano: bool = False
    estado: str | None = None
    segmento: str | None = None
    ofertas: list = field(default_factory=list)
    falta_s: float = 0.0


class Vigilante:
    """Cuida una subasta. No duerme: agenda."""

    def __init__(self, cfg: ConfigSubasta, sesion: Sesion, vivo: bool, log,
                 azar: random.Random | None = None):
        self.cfg = cfg
        self.sesion = sesion
        self.vivo = vivo
        self.log = log
        self.azar = azar or random.Random()
        self.estado = EstadoSesion()

        self.fase = Fase.MIRANDO
        self.detalle = "arrancando"
        self.libro: Libro | None = None
        self._html: str = ""          # el HTML de la ultima lectura del libro
        self.estado_subasta: str | None = None
        self.segmento: str | None = None

        self.proxima_s = 0.0          # cuándo volver a mirar
        self.cotizar_en_s: float | None = None
        self.tasa_pendiente: Decimal | None = None
        self.visto_estado_s = 0.0
        self.fallas = 0
        self.ultimo_cambio_s = 0.0
        self.huella = None

    # -- control -----------------------------------------------------------

    def parar(self, motivo: str = "parado a mano") -> None:
        self.estado.detener()
        self._fase(Fase.DETENIDO, motivo)

    @property
    def terminado(self) -> bool:
        return self.fase.terminal

    def listo_para(self, ahora_s: float) -> bool:
        return ahora_s >= self.proxima_s

    # -- una vuelta --------------------------------------------------------

    def tick(self, ahora_s: float) -> None:
        """Avanza lo que se pueda ahora mismo. Nunca bloquea."""
        if self.terminado:
            return

        try:
            self._avanzar(ahora_s)
            self.fallas = 0
        except (ErrorDePlataforma,) as e:
            # Pasajero: la red falló o la plataforma tardó. Reintentar una
            # lectura no es reintentar una orden.
            self.fallas += 1
            espera = min(2 ** self.fallas, 30)
            self.proxima_s = ahora_s + espera
            self.log("red", f"[{self.cfg.ident}] {e}; reintento en {espera:.0f}s",
                     fallas=self.fallas)
            if self.fallas >= FALLAS_TOLERADAS:
                self._fase(Fase.DETENIDO,
                           f"la plataforma no responde hace rato: {e}")
        except SesionCaida as e:
            # El 2FA impide re-loguearse solo.
            self._fase(Fase.DETENIDO, str(e))
        except (LibroIlegible, FormularioIlegible, CampoProhibido) as e:
            self._fase(Fase.DETENIDO, str(e))

    def _avanzar(self, ahora_s: float) -> None:
        if self.estado_subasta is None or \
                (ahora_s - self.visto_estado_s) > CADA_ESTADO_S:
            self._mirar_estado(ahora_s)
            if self.terminado:
                return

        # Si hay una cotización agendada y todavía no es hora, no gastamos un
        # request: el libro se relee recién cuando toca actuar.
        if self.cotizar_en_s is not None and ahora_s < self.cotizar_en_s:
            self.proxima_s = min(self.cotizar_en_s, ahora_s + self.cfg.sondeo_s)
            return

        libro = self._leer()
        decision = decidir(libro, self.cfg, self.azar)

        if decision.accion is Accion.CEDER:
            self._fase(Fase.CEDIDO, decision.motivo)
            return

        if decision.accion is not Accion.RECOTIZAR:
            self.cotizar_en_s = None
            self.tasa_pendiente = None
            self._fase(Fase.MIRANDO, decision.motivo)
            self.proxima_s = ahora_s + self.cfg.sondeo_s
            return

        # Hay que mejorar la oferta. La demora deliberada se agenda, no se
        # duerme: mientras tanto el resto de las subastas siguen avanzando.
        if self.cotizar_en_s is None:
            espera = self.azar.uniform(self.cfg.espera_min_s, self.cfg.espera_max_s)
            if espera > 0:
                self.cotizar_en_s = ahora_s + espera
                self.tasa_pendiente = decision.tasa
                self._fase(Fase.ESPERANDO,
                           f"{decision.motivo}; espero {espera:.1f}s")
                self.proxima_s = min(self.cotizar_en_s,
                                     ahora_s + self.cfg.sondeo_s)
                return

        # Se cumplió la espera (o no había). La decisión se recalculó recién
        # sobre el libro que acabamos de leer, así que no está vieja.
        self.cotizar_en_s = None
        self.tasa_pendiente = None
        self._cotizar(decision, libro, ahora_s)

    def _cotizar(self, decision, libro: Libro, ahora_s: float) -> None:
        veredicto = evaluar(decision, self.cfg, self.estado, ahora_s, 0.0)
        if not veredicto:
            self.log("bloqueado", f"[{self.cfg.ident}] gate: {veredicto.motivo}")
            self._fase(Fase.MIRANDO, veredicto.motivo)
            self.proxima_s = ahora_s + self.cfg.sondeo_s
            return

        texto = formatear_tasa(decision.tasa)
        # Se reusa el HTML de la lectura que acabamos de hacer en vez de pedir
        # la misma página otra vez: es un round-trip menos en cada
        # recotización, y además garantiza que el payload salga del mismo
        # estado sobre el que se decidió.
        payload = armar_oferta(self._html, self.sesion.cheques(self.cfg.ident),
                               texto)

        if not self.vivo:
            self.log("sombra", f"[{self.cfg.ident}] HARÍA: cotizar {texto} "
                               f"({decision.motivo})", tasa=texto)
            self.estado.registrar(self.cfg.ident, ahora_s)
            self._fase(Fase.MIRANDO, f"haría {texto}: {decision.motivo}")
            self.proxima_s = ahora_s + self.cfg.sondeo_s
            return

        self.log("cotizando", f"[{self.cfg.ident}] cotizo {texto}", tasa=texto)
        try:
            self.sesion.postear("cpd-versubasta.r", payload.pares(),
                                referer_ident=self.cfg.ident)
        except ErrorDePlataforma as e:
            # No se reintenta: no sabemos si entró. Lo dice el libro, no nosotros.
            self.log("cotizacion_dudosa", f"[{self.cfg.ident}] no sé si entró: {e}")
        self.estado.registrar(self.cfg.ident, ahora_s)

        libro = self._leer()
        v = verificar_despues(libro, self.cfg, decision.tasa)
        self.log("verificacion",
                 f"[{self.cfg.ident}] {'ok' if v else 'PARO'}: {v.motivo}", ok=v.ok)
        if not v:
            self._fase(Fase.DETENIDO, v.motivo)
            return
        self._fase(Fase.MIRANDO, f"cotizada en {texto}")
        self.proxima_s = ahora_s + self.cfg.sondeo_s

    # -- lecturas ----------------------------------------------------------

    def _leer(self) -> Libro:
        self._html = self.sesion.subasta(self.cfg.ident)
        libro = parsear_libro(self._html)
        if libro.ident != self.cfg.ident:
            raise LibroIlegible(
                f"pedí la subasta {self.cfg.ident} y el libro dice {libro.ident}")
        self.libro = libro
        huella = tuple((o.id, o.tasa, o.ingreso) for o in libro.ofertas)
        if huella != self.huella:
            self.huella = huella
            self.ultimo_cambio_s = reloj.monotonic()
            mia = libro.mejor_propia()
            ajena = libro.mejor_ajena()
            self.log("libro", f"[{self.cfg.ident}] mía="
                              f"{formatear_tasa(mia.tasa) if mia else '-'} "
                              f"ajena={formatear_tasa(ajena.tasa) if ajena else '-'}")
        return libro

    def _mirar_estado(self, ahora_s: float) -> None:
        """Activa, negociada o desierta. Fuera de activa, el bot no opera."""
        self.visto_estado_s = ahora_s
        ficha = self.sesion.estado_subasta(self.cfg.ident)
        if not ficha:
            return                      # informativo; si no llega, se sigue
        self.estado_subasta = str(ficha.get("estado") or "").strip()
        self.segmento = str(ficha.get("segmento") or "").strip()
        if self.estado_subasta and \
                self.estado_subasta.lower() not in ESTADOS_VIVOS:
            self._fase(Fase.CERRADA, f"la subasta está {self.estado_subasta.lower()}")

    def _fase(self, fase: Fase, detalle: str) -> None:
        if (fase, detalle) != (self.fase, self.detalle):
            self.log("fase", f"[{self.cfg.ident}] {fase.value}: {detalle}")
        self.fase = fase
        self.detalle = detalle

    # -- para la interfaz --------------------------------------------------

    def vista(self, ahora_s: float) -> Vista:
        mia = self.libro.mejor_propia() if self.libro else None
        ajena = self.libro.mejor_ajena() if self.libro else None
        return Vista(
            ident=self.cfg.ident,
            fase=self.fase.value,
            detalle=self.detalle,
            vivo=self.vivo,
            recotizaciones=self.estado.recotizaciones.get(self.cfg.ident, 0),
            mia=formatear_tasa(mia.tasa) if mia else None,
            mejor_ajena=formatear_tasa(ajena.tasa) if ajena else None,
            gano=bool(mia and (not ajena or mia.tasa < ajena.tasa)),
            estado=self.estado_subasta,
            segmento=self.segmento,
            ofertas=[
                {"id": o.id, "agente": o.agente, "tasa": formatear_tasa(o.tasa),
                 "hora": o.ingreso.strftime("%H:%M:%S"), "propia": o.propia}
                for o in sorted(self.libro.ofertas, key=lambda o: (o.tasa, o.ingreso))
            ] if self.libro else [],
            falta_s=max(0.0, (self.cotizar_en_s or 0.0) - ahora_s),
        )
