"""El ciclo del bot hablando directo con la plataforma, sin navegador.

Misma secuencia que la version con Chrome — leer, decidir, gate, cotizar,
verificar — pero las lecturas son GET y la cotizacion es un POST armado por
motor.formulario, que copia todos los campos del servidor y solo decide la tasa
y la accion.
"""

from __future__ import annotations

import random
import time as reloj
from dataclasses import dataclass
from enum import Enum

from .config import ConfigSubasta
from .decision import Accion, decidir
from .formulario import CampoProhibido, FormularioIlegible, armar_oferta
from .libro import Libro, LibroIlegible, formatear_tasa, parsear_libro
from .riesgo import EstadoSesion, evaluar, verificar_despues
from .sesion import ErrorDePlataforma, Sesion, SesionCaida

INTERVALO_TRANQUILO_S = 5.0
INTERVALO_ACTIVO_S = 2.0
SEGUIR_ACTIVO_S = 120.0


class Resultado(Enum):
    MIRANDO = "mirando"
    HARIA = "haria"
    COTIZO = "cotizo"
    BLOQUEADO = "bloqueado"
    CEDIDO = "cedido"
    DETENIDO = "detenido"


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
    def __init__(self, cfg: ConfigSubasta, sesion: Sesion, vivo: bool, log, azar=None):
        self.cfg = cfg
        self.sesion = sesion
        self.vivo = vivo
        self.log = log
        self.azar = azar or random.Random()
        self.estado = EstadoSesion()
        self.ultimo_cambio = 0.0
        self.huella = None

    def parar(self) -> None:
        self.estado.detener()

    @property
    def detenido(self) -> bool:
        return self.estado.kill

    def dormir(self, segundos: float) -> None:
        """Espera mirando el kill switch: un boton de parar que tarda un minuto
        en hacer efecto no es un boton de parar."""
        fin = reloj.monotonic() + segundos
        while reloj.monotonic() < fin and not self.detenido:
            reloj.sleep(min(0.2, fin - reloj.monotonic()))

    @property
    def pausa_sugerida(self) -> float:
        caliente = (reloj.monotonic() - self.ultimo_cambio) < SEGUIR_ACTIVO_S
        return INTERVALO_ACTIVO_S if caliente else INTERVALO_TRANQUILO_S

    # -- una vuelta --------------------------------------------------------

    def tick(self) -> Paso:
        if self.detenido:
            return Paso(Resultado.DETENIDO, "detenido")

        try:
            html = self.sesion.subasta(self.cfg.ident)
        except SesionCaida as e:
            # El 2FA impide re-loguearse solo. Siempre termina en parar y avisar.
            self.parar()
            return Paso(Resultado.DETENIDO, str(e))
        except ErrorDePlataforma as e:
            self.log("lectura_fallida", error=str(e))
            return Paso(Resultado.MIRANDO, f"no pude leer: {e}")

        libro = self._leer(html)
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

        leido_s = reloj.monotonic()
        espera = self.azar.uniform(self.cfg.espera_min_s, self.cfg.espera_max_s)
        if espera > 0:
            self.log("espera", f"espero {espera:.0f}s antes de mover", segundos=espera)
            self.dormir(espera)
            if self.detenido:
                return Paso(Resultado.DETENIDO, "detenido durante la espera")
            try:
                html = self.sesion.subasta(self.cfg.ident)
            except SesionCaida as e:
                self.parar()
                return Paso(Resultado.DETENIDO, str(e))
            except ErrorDePlataforma as e:
                return Paso(Resultado.MIRANDO, f"no pude releer: {e}")
            libro = self._leer(html)
            if libro is None:
                self.parar()
                return Paso(Resultado.DETENIDO, "no puedo leer el libro con confianza")
            self._anotar_cambio(libro)
            leido_s = reloj.monotonic()
            decision = decidir(libro, self.cfg, self.azar)
            if decision.accion is not Accion.RECOTIZAR:
                return Paso(Resultado.MIRANDO,
                            f"tras esperar ya no hace falta: {decision.motivo}",
                            libro=libro)

        veredicto = evaluar(decision, self.cfg, self.estado,
                            reloj.monotonic(), reloj.monotonic() - leido_s)
        if not veredicto:
            self.log("bloqueado", f"gate: {veredicto.motivo}")
            return Paso(Resultado.BLOQUEADO, veredicto.motivo, libro=libro)

        texto = formatear_tasa(decision.tasa)

        # El payload se arma siempre, tambien en sombra: es donde corre la
        # verificacion de que no se invento ningun campo, y queremos que eso se
        # ejercite antes de habilitar el modo vivo.
        try:
            cheques = self.sesion.cheques(self.cfg.ident)
            payload = armar_oferta(html, cheques, texto)
        except (FormularioIlegible, CampoProhibido) as e:
            self.parar()
            return Paso(Resultado.DETENIDO, f"no pude armar la oferta: {e}", libro=libro)
        except SesionCaida as e:
            self.parar()
            return Paso(Resultado.DETENIDO, str(e), libro=libro)
        except ErrorDePlataforma as e:
            return Paso(Resultado.MIRANDO, f"no pude leer los cheques: {e}", libro=libro)

        if not self.vivo:
            self.log("sombra", f"HARIA: cotizar {texto} ({decision.motivo})",
                     tasa=texto, campos=len(payload.campos))
            self.estado.registrar(self.cfg.ident, reloj.monotonic())
            return Paso(Resultado.HARIA, decision.motivo, libro=libro, tasa=texto)

        self.log("cotizando", f"cotizo {texto}", tasa=texto,
                 campos=len(payload.campos))
        try:
            self.sesion.postear("cpd-versubasta.r", payload.pares(),
                                referer_ident=self.cfg.ident)
        except (ErrorDePlataforma, SesionCaida) as e:
            # No se reintenta: no sabemos si entro. Se relee el libro y decide
            # la pantalla, no nosotros.
            self.log("cotizacion_dudosa", f"no se si entro: {e}")
        self.estado.registrar(self.cfg.ident, reloj.monotonic())

        return self._confirmar(decision.tasa, texto)

    def _confirmar(self, tasa, texto: str) -> Paso:
        """Relee el libro y confirma que entro lo que queriamos.

        Reemplaza al preview que esta plataforma no tiene. Si no coincide, se
        para: puede que el POST no haya entrado, que haya entrado otra cosa, o
        que alguien se haya metido en el medio, y ninguna de las tres se
        resuelve reintentando a ciegas.
        """
        try:
            libro = self._leer(self.sesion.subasta(self.cfg.ident))
        except (ErrorDePlataforma, SesionCaida) as e:
            self.parar()
            return Paso(Resultado.DETENIDO, f"no pude releer despues de cotizar: {e}")
        if libro is None:
            self.parar()
            return Paso(Resultado.DETENIDO, "no pude releer el libro despues de cotizar")

        v = verificar_despues(libro, self.cfg, tasa)
        self.log("verificacion", f"{'ok' if v else 'PARO'}: {v.motivo}", ok=v.ok)
        if not v:
            self.parar()
            return Paso(Resultado.DETENIDO, v.motivo, libro=libro)
        self._anotar_cambio(libro)
        return Paso(Resultado.COTIZO, v.motivo, libro=libro, tasa=texto)

    def _leer(self, html: str) -> Libro | None:
        try:
            return parsear_libro(html)
        except LibroIlegible as e:
            self.log("libro_ilegible", f"NO ENTIENDO LA PANTALLA: {e}", error=str(e))
            return None

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
