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

El ritmo lo manda la orden, no el vigilante: lo único que decide cada cuánto se
mira es el `sondeo_s` que se configuró al activar. No hay nada acá que acelere
ni afloje solo según cómo venga la puja.

Lo que sí hay es una forma de no gastar pedidos al pedo: el tablero (la fila del
listado, ver `ficha.py`) dice si la punta compradora se movió. Cuando no se
movió y la última lectura concluyó algo que no depende del azar, se saltea la
relectura del libro. Es plomería, no criterio: la oferta la sigue decidiendo el
libro, y el atajo se apaga solo si alguna vez el tablero no coincide con él.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .config import ConfigSubasta
from .decision import Accion, decidir
from .ficha import Ficha, leer_ficha
from .formulario import CampoProhibido, FormularioIlegible, armar_oferta
from .libro import Libro, LibroIlegible, formatear_tasa, parsear_libro
from .riesgo import EstadoSesion, evaluar, verificar_despues
from .sesion import ErrorDePlataforma, Sesion, SesionCaida

# Cada cuánto se vuelve a preguntar el estado de la subasta (activa, negociada,
# desierta) cuando el tablero de la mesa no lo esta trayendo. Es un request
# extra, así que no va en cada vuelta.
CADA_ESTADO_S = 45.0

# Tope de confianza en el tablero. Aunque el radar jure que nada se movio, el
# libro se relee cada tanto: un radar congelado no puede dejar ciego al bot.
RELECTURA_S = 15.0

# Fallas de red seguidas antes de darse por vencido. Con backoff, esto da
# alrededor de un minuto de insistencia antes de frenar.
FALLAS_TOLERADAS = 6

# Cuanto se le da a la plataforma para mostrar una oferta recien cargada, y cada
# cuanto se vuelve a mirar mientras tanto.
#
# Esto existe porque el bot "se cortaba solo apenas cargaba una tasa": el POST
# entraba bien, pero la relectura salia tan pegada que MAV todavia contestaba con
# el libro viejo, y el control posterior lo leia como "no entro lo que queria" y
# frenaba. Releer no es reintentar una orden: insistir con la *lectura* es
# gratis y seguro. Lo que no se hace nunca es asumir que entro.
VENTANA_CONFIRMACION_S = 6.0
REINTENTO_CONFIRMACION_S = 0.5


class Fase(Enum):
    MIRANDO = "mirando"          # leyendo el libro, sin nada que hacer
    ESPERANDO = "esperando"      # decidió cotizar, cumpliendo la demora
    CONFIRMANDO = "confirmando"  # cotizó, esperando verla en el libro
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
    tmin: str | None = None       # T.Min: desde ahi se arma el reloj
    cierre: str | None = None     # limite duro del dia, NO la cuenta regresiva
    cheques: int | None = None
    agente_vdr: str | None = None
    tasa_vdr: str | None = None   # la que cargo el vendedor: el techo de la puja


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
        self.huella = None

        # Radar: la fila del listado. Dice si el libro pudo haber cambiado sin
        # gastar un pedido por subasta. Nunca decide una oferta.
        self.ficha: Ficha | None = None
        self.radar_confiable = True
        self.radar_probado = False    # hasta que no coincida una vez, no se usa
        self._huella_leida = None     # la huella que tenia el radar al leer
        self._leido_s = 0.0
        self._estable = False

        # Confirmacion pendiente: (tasa, hasta cuando, cuantas propias habia).
        self.confirmar: tuple[Decimal, float, int] | None = None

    # -- control -----------------------------------------------------------

    def parar(self, motivo: str = "parado a mano") -> None:
        self.estado.detener()
        self._fase(Fase.DETENIDO, motivo)

    @property
    def terminado(self) -> bool:
        return self.fase.terminal

    def listo_para(self, ahora_s: float) -> bool:
        return ahora_s >= self.proxima_s

    def recibir_ficha(self, fila: dict, ahora_s: float) -> None:
        """La mesa reparte lo que trajo el tablero. Un pedido para todas."""
        if self.terminado:
            return
        ficha = leer_ficha(fila)
        if ficha is None or ficha.ident != self.cfg.ident:
            return
        self.ficha = ficha
        self.visto_estado_s = ahora_s
        self.estado_subasta = ficha.estado
        self.segmento = ficha.segmento
        if ficha.estado and not ficha.viva:
            self._fase(Fase.CERRADA, f"la subasta está {ficha.estado.lower()}")

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

        # Una oferta recién mandada se confirma antes que cualquier otra cosa:
        # hasta no verla en el libro no se decide nada nuevo.
        if self.confirmar is not None:
            self._confirmar(ahora_s)
            return

        # Si hay una cotización agendada y todavía no es hora, no gastamos un
        # request: el libro se relee recién cuando toca actuar.
        if self.cotizar_en_s is not None and ahora_s < self.cotizar_en_s:
            self.proxima_s = min(self.cotizar_en_s, ahora_s + self.cfg.sondeo_s)
            return

        # Si el tablero dice que la punta compradora sigue igual y la última
        # lectura concluyó algo que no depende del azar, no hay nada nuevo que
        # leer. El libro sigue mandando: esto solo evita releerlo al pedo.
        if self._sin_novedad(ahora_s):
            self.proxima_s = ahora_s + self.cfg.sondeo_s
            return

        libro = self._leer(ahora_s)
        decision = decidir(libro, self.cfg, self.azar)
        self._estable = decision.estable
        self._huella_leida = self.ficha.huella if self.ficha else None

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

    def _sin_novedad(self, ahora_s: float) -> bool:
        """¿El tablero garantiza que releer el libro no aporta nada?"""
        if not (self.radar_confiable and self.radar_probado and self.ficha
                and self.libro):
            return False
        if self.ficha.tasa_cpr is None:
            # Sin punta compradora en el tablero no hay nada que comparar. Se
            # lee el libro igual: el atajo se gana, no se presume.
            return False
        if self._huella_leida is None or not self._estable:
            return False
        if self.ficha.huella != self._huella_leida:
            return False
        return (ahora_s - self._leido_s) < RELECTURA_S

    def _cotizar(self, decision, libro: Libro, ahora_s: float) -> None:
        # Después de tocar el libro no se saltea nada: la vuelta que viene se
        # lee de nuevo, pase lo que pase con el POST.
        self._huella_leida = None
        # La antiguedad real de la lectura, no un cero puesto a mano. Hoy el
        # libro se lee siempre justo antes de decidir, asi que da ~0 — pero
        # cablearlo hacia que el control de "nunca cotizar sobre un libro
        # viejo" fuera decorativo, y el dia que el flujo cambie tiene que
        # seguir siendo cierto.
        antiguedad = max(0.0, ahora_s - self._leido_s)
        veredicto = evaluar(decision, self.cfg, self.estado, ahora_s, antiguedad)
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

        # La oferta no se da por buena hasta verla en el libro, pero tampoco se
        # frena por no verla en el primer intento: se abre una ventana.
        self.confirmar = (decision.tasa, ahora_s + VENTANA_CONFIRMACION_S,
                          len(libro.propias))
        self._fase(Fase.CONFIRMANDO, f"mandé {texto}, esperando verla en el libro")
        self.proxima_s = ahora_s + REINTENTO_CONFIRMACION_S

    def _confirmar(self, ahora_s: float) -> None:
        """Releer hasta ver la oferta propia, o hasta que se acabe la paciencia.

        Insistir acá es insistir con una *lectura*, no con una orden: no puede
        duplicar nada. Si al final de la ventana la oferta no aparece, ahí sí se
        frena, porque no saber qué entró es la única situación que no se arregla
        mirando de nuevo.
        """
        tasa, hasta_s, propias_antes = self.confirmar
        libro = self._leer(ahora_s)
        v = verificar_despues(libro, self.cfg, tasa, propias_antes)
        if v:
            self.confirmar = None
            self.log("verificacion", f"[{self.cfg.ident}] ok: {v.motivo}", ok=True)
            self._fase(Fase.MIRANDO, f"cotizada en {formatear_tasa(tasa)}")
            self.proxima_s = ahora_s + self.cfg.sondeo_s
            return

        if ahora_s < hasta_s:
            self.proxima_s = ahora_s + REINTENTO_CONFIRMACION_S
            return

        self.confirmar = None
        self.log("verificacion",
                 f"[{self.cfg.ident}] PARO tras {VENTANA_CONFIRMACION_S:.0f}s: "
                 f"{v.motivo}", ok=False)
        self._fase(Fase.DETENIDO, v.motivo)

    # -- lecturas ----------------------------------------------------------

    def _leer(self, ahora_s: float | None = None) -> Libro:
        self._html = self.sesion.subasta(self.cfg.ident)
        libro = parsear_libro(self._html, self.cfg.mi_agente)
        if libro.ident != self.cfg.ident:
            raise LibroIlegible(
                f"pedí la subasta {self.cfg.ident} y el libro dice {libro.ident}")
        self.libro = libro
        if ahora_s is not None:
            self._leido_s = ahora_s
        huella = tuple((o.id, o.tasa, o.ingreso) for o in libro.ofertas)
        self._controlar_radar(libro, quieto=(huella == self.huella))
        if huella != self.huella:
            self.huella = huella
            mia = libro.mejor_propia()
            ajena = libro.mejor_ajena()
            # El libro entero, no solo las puntas: es lo unico que permite
            # reconstruir despues una rueda movimiento por movimiento, que es
            # para lo que existe el modo sombra.
            self.log("libro", f"[{self.cfg.ident}] mía="
                              f"{formatear_tasa(mia.tasa) if mia else '-'} "
                              f"ajena={formatear_tasa(ajena.tasa) if ajena else '-'}",
                     ident=self.cfg.ident,
                     mia=formatear_tasa(mia.tasa) if mia else None,
                     ajena=formatear_tasa(ajena.tasa) if ajena else None,
                     estado=self.estado_subasta,
                     cierre=self.ficha.hora_cierre if self.ficha else None,
                     libro=[{"id": o.id, "ag": o.agente,
                             "tasa": formatear_tasa(o.tasa),
                             "hora": o.ingreso.strftime("%H:%M:%S"),
                             "propia": o.propia}
                            for o in sorted(libro.ofertas,
                                            key=lambda o: (o.tasa, o.ingreso))])
        return libro

    def _controlar_radar(self, libro: Libro, quieto: bool) -> None:
        """El libro es la verdad; el tablero, una promesa. Se contrastan.

        `tasa-cpr` del listado tendría que ser la mejor punta compradora, que
        es la mejor oferta del libro. Nunca lo vimos fallar, pero tampoco está
        documentado: si alguna vez no coincide, el atajo se apaga para el resto
        de la sesión y el bot vuelve a abrir la subasta en cada vuelta.

        `quieto` dice que el libro no cambió desde la lectura anterior, y es la
        única condición en la que el contraste significa algo. El tablero es
        una foto de hasta un par de segundos atrás: en plena guerra difiere del
        libro todo el tiempo, y no porque mienta sino porque el libro se movió
        en el medio. Contrastarlo igual apagaba el atajo en la primera
        recotización — justo donde tiene que servir.
        """
        if not quieto:
            return
        if not (self.radar_confiable and self.ficha and self.ficha.tasa_cpr):
            return
        mejor = min((o.tasa for o in libro.ofertas), default=None)
        if mejor is None:
            return
        if mejor == self.ficha.tasa_cpr:
            # Coincidieron con el libro quieto: recién ahora el atajo sirve.
            self.radar_probado = True
            return
        self.radar_confiable = False
        self.log("radar",
                 f"[{self.cfg.ident}] el tablero dice {self.ficha.tasa_cpr} y el "
                 f"libro {mejor}: dejo de confiar en el tablero y releo siempre")

    def _mirar_estado(self, ahora_s: float) -> None:
        """Respaldo cuando la mesa no pudo traer el tablero.

        Activa, negociada o desierta. Fuera de activa, el bot no opera.
        """
        self.visto_estado_s = ahora_s
        fila = self.sesion.estado_subasta(self.cfg.ident)
        if not fila:
            return                      # informativo; si no llega, se sigue
        self.recibir_ficha(fila, ahora_s)

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
            tmin=self.ficha.tiempo_minimo if self.ficha else None,
            cierre=self.ficha.hora_cierre if self.ficha else None,
            cheques=self.ficha.cantidad_cheques if self.ficha else None,
            agente_vdr=self.ficha.agente_vdr if self.ficha else None,
            tasa_vdr=(formatear_tasa(self.ficha.tasa_vdr)
                      if self.ficha and self.ficha.tasa_vdr is not None else None),
        )
