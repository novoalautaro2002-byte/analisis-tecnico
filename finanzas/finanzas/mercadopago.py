"""Conector de Mercado Pago.

Usa el reporte "Todas las transacciones" (settlement_report), que es el unico
que cubre todos los movimientos de la cuenta y no solo los cobros.

Para tu propia cuenta no hace falta el flujo OAuth: se crea una aplicacion en
el panel de desarrolladores y se usa el access token directo. OAuth existe
para acceder a cuentas de terceros, que no es este caso.

    https://www.mercadopago.com.ar/developers/panel/app

Endpoints (verificados contra la documentacion oficial):
    POST /v1/account/settlement_report              crea el reporte
    GET  /v1/account/settlement_report/list         lista los disponibles
    GET  /v1/account/settlement_report/{archivo}    descarga el CSV

ADVERTENCIA IMPORTANTE: la API de Mercado Pago esta disenada alrededor del
lado cobrador. Que aparezca con detalle cada movimiento de consumo (un pago
con QR, una factura que pagaste) es lo primero que hay que verificar contra
la cuenta real. Para eso esta 'finanzas mp-probar'. Si no aparecen, Mercado
Pago pasa a ser una fuente de PDF como cualquier otra.
"""

from __future__ import annotations

import csv
import io
import os
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import httpx

from .modelo import Moneda, Movimiento, Tipo, parse_monto

BASE = "https://api.mercadopago.com"
CUENTA = "mercadopago"


class ErrorMercadoPago(Exception):
    pass


def token_desde_entorno() -> str:
    token = os.environ.get("MP_ACCESS_TOKEN", "").strip()
    if not token:
        raise ErrorMercadoPago(
            "Falta MP_ACCESS_TOKEN. Creá una aplicación en "
            "https://www.mercadopago.com.ar/developers/panel/app y exportá "
            "el access token de produccion:\n\n    export MP_ACCESS_TOKEN='APP_USR-...'\n"
        )
    return token


class ClienteMP:
    def __init__(self, token: str | None = None, timeout: float = 60.0):
        self.token = token or token_desde_entorno()
        self._http = httpx.Client(
            base_url=BASE,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
        )

    def __enter__(self) -> "ClienteMP":
        return self

    def __exit__(self, *exc) -> None:
        self._http.close()

    # ------------------------------------------------------------------
    # Identidad
    # ------------------------------------------------------------------

    def quien_soy(self) -> dict:
        """Valida el token y devuelve los datos de la cuenta."""
        r = self._http.get("/users/me")
        if r.status_code == 401:
            raise ErrorMercadoPago(
                "El access token fue rechazado (401). Verificá que sea el de "
                "produccion y que no haya expirado."
            )
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Reportes
    # ------------------------------------------------------------------

    def crear_reporte(self, desde: date, hasta: date) -> None:
        cuerpo = {
            "begin_date": f"{desde.isoformat()}T00:00:00Z",
            "end_date": f"{hasta.isoformat()}T23:59:59Z",
        }
        r = self._http.post("/v1/account/settlement_report", json=cuerpo)
        if r.status_code >= 400:
            raise ErrorMercadoPago(
                f"No se pudo crear el reporte ({r.status_code}): {r.text[:300]}"
            )

    def listar_reportes(self) -> list[dict]:
        r = self._http.get("/v1/account/settlement_report/list")
        r.raise_for_status()
        datos = r.json()
        return datos if isinstance(datos, list) else datos.get("results", [])

    def descargar(self, nombre_archivo: str) -> str:
        r = self._http.get(f"/v1/account/settlement_report/{nombre_archivo}")
        r.raise_for_status()
        return r.text

    def reporte_del_periodo(
        self, desde: date, hasta: date, espera_maxima: int = 180
    ) -> str:
        """Pide el reporte y espera a que este disponible.

        La generacion es asincronica: se crea, se consulta la lista hasta que
        aparece uno nuevo, y recien ahi se descarga.
        """
        previos = {r.get("file_name") for r in self.listar_reportes()}
        self.crear_reporte(desde, hasta)

        limite = time.monotonic() + espera_maxima
        espera = 3.0
        while time.monotonic() < limite:
            time.sleep(espera)
            espera = min(espera * 1.5, 20.0)
            actuales = self.listar_reportes()
            nuevos = [r for r in actuales if r.get("file_name") not in previos]
            if nuevos:
                nuevos.sort(key=lambda r: r.get("date_created", ""), reverse=True)
                return self.descargar(nuevos[0]["file_name"])

        raise ErrorMercadoPago(
            f"El reporte no estuvo listo en {espera_maxima}s. Volvé a correrlo: "
            "si ya se generó, se descarga sin volver a esperar."
        )


# ----------------------------------------------------------------------
# Conversion del CSV al modelo canonico
# ----------------------------------------------------------------------

# El CSV de settlement trae muchas columnas y el nombre exacto cambia segun
# la configuracion de la cuenta, asi que se buscan por alias.
_ALIAS_FECHA = ("date_created", "money_release_date", "date_approved", "transaction_date")
_ALIAS_MONTO = ("net_credit_amount", "net_debit_amount", "transaction_amount", "net_received_amount")
_ALIAS_DESC = ("description", "reason", "payment_method_type", "transaction_type", "operation_type")
_ALIAS_ID = ("source_id", "transaction_id", "operation_id", "external_reference")
_ALIAS_MONEDA = ("currency_id", "currency")


def parsear_csv(texto: str, fuente: str = "api:mercadopago") -> list[Movimiento]:
    """Convierte el CSV del reporte en Movimientos."""
    filas = list(csv.DictReader(io.StringIO(texto)))
    movimientos: list[Movimiento] = []

    for fila in filas:
        normal = { (k or "").strip().lower(): (v or "").strip() for k, v in fila.items() }

        fecha = _primer_valor(normal, _ALIAS_FECHA)
        if not fecha:
            continue
        try:
            f = _parse_fecha_iso(fecha)
        except ValueError:
            continue

        monto = _monto_de_fila(normal)
        if monto is None:
            continue

        desc = _primer_valor(normal, _ALIAS_DESC) or "Movimiento Mercado Pago"
        ident = _primer_valor(normal, _ALIAS_ID)
        if ident:
            desc = f"{desc} ({ident})"

        moneda_txt = (_primer_valor(normal, _ALIAS_MONEDA) or "ARS").upper()
        moneda = Moneda.USD if moneda_txt.startswith("USD") else Moneda.ARS

        movimientos.append(
            Movimiento(
                fecha=f,
                cuenta=CUENTA,
                descripcion=desc,
                monto=monto,
                moneda=moneda,
                tipo=_tipo_de_fila(normal, monto),
                fuente=fuente,
            )
        )

    return movimientos


def _primer_valor(fila: dict, alias: tuple[str, ...]) -> str:
    for a in alias:
        if fila.get(a):
            return fila[a]
    return ""


def _monto_de_fila(fila: dict) -> Decimal | None:
    """El neto de la fila, con signo.

    El reporte separa creditos y debitos en columnas distintas. Si vienen las
    dos, el movimiento es la diferencia.
    """
    credito = _decimal_o_none(fila.get("net_credit_amount"))
    debito = _decimal_o_none(fila.get("net_debit_amount"))

    if credito is not None or debito is not None:
        return (credito or Decimal(0)) - (debito or Decimal(0))

    for alias in _ALIAS_MONTO:
        valor = _decimal_o_none(fila.get(alias))
        if valor is not None:
            return valor
    return None


def _decimal_o_none(texto: str | None) -> Decimal | None:
    if not texto:
        return None
    try:
        return parse_monto(texto)
    except ValueError:
        return None


def _parse_fecha_iso(texto: str) -> date:
    limpio = texto.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(limpio).date()
    except ValueError:
        pass
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(texto.strip()[:10], formato).date()
        except ValueError:
            continue
    raise ValueError(f"fecha no reconocida: {texto!r}")


def _tipo_de_fila(fila: dict, monto: Decimal) -> Tipo:
    texto = " ".join(fila.get(k, "") for k in _ALIAS_DESC).lower()
    if any(p in texto for p in ("transfer", "money_transfer", "envio")):
        return Tipo.TRANSFERENCIA
    if any(p in texto for p in ("fee", "comision", "tax", "impuesto")):
        return Tipo.COMISION
    if any(p in texto for p in ("payment", "pago", "qr", "point")):
        return Tipo.COMPRA if monto < 0 else Tipo.CREDITO
    return Tipo.CREDITO if monto > 0 else Tipo.DEBITO


def periodo_por_defecto(dias: int = 90) -> tuple[date, date]:
    hoy = datetime.now(timezone.utc).date()
    return hoy - timedelta(days=dias), hoy
