"""Lectura de PDFs de resumen.

Separa deliberadamente dos cosas que conviene no mezclar: sacar el contenido
del PDF (esto) e interpretarlo (los parsers). Un resumen protegido con clave,
uno escaneado y uno nativo se leen distinto, pero todos terminan siendo un
Documento con texto y tablas.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber


@dataclass
class Pagina:
    numero: int
    texto: str
    tablas: list[list[list[str | None]]] = field(default_factory=list)

    @property
    def lineas(self) -> list[str]:
        return [ln.strip() for ln in self.texto.splitlines() if ln.strip()]


@dataclass
class Documento:
    ruta: Path
    sha256: str
    paginas: list[Pagina]

    @property
    def texto(self) -> str:
        return "\n".join(p.texto for p in self.paginas)

    @property
    def lineas(self) -> list[str]:
        return [ln for p in self.paginas for ln in p.lineas]

    @property
    def tablas(self) -> list[list[list[str | None]]]:
        return [t for p in self.paginas for t in p.tablas]

    @property
    def esta_vacio(self) -> bool:
        """Un PDF escaneado extrae cero texto. Necesita OCR, no un parser."""
        return len(self.texto.strip()) < 50


class PdfProtegido(Exception):
    """El PDF pide clave y la que se dio no sirve (o no se dio ninguna)."""


def hash_archivo(ruta: Path) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def leer(ruta: Path | str, claves: list[str] | None = None) -> Documento:
    """Abre un PDF y extrae texto y tablas de cada pagina.

    Los resumenes de tarjeta suelen venir con clave (el DNI, a veces los
    ultimos digitos de la tarjeta). Se prueban todas las claves candidatas
    antes de darse por vencido, asi no hay que decirle a mano cual va con cual.
    """
    ruta = Path(ruta)
    candidatas: list[str | None] = [None, *(claves or [])]

    ultimo_error: Exception | None = None
    for clave in candidatas:
        try:
            with pdfplumber.open(ruta, password=clave or "") as pdf:
                paginas = [
                    Pagina(
                        numero=i,
                        texto=pag.extract_text() or "",
                        tablas=pag.extract_tables() or [],
                    )
                    for i, pag in enumerate(pdf.pages, start=1)
                ]
            return Documento(ruta=ruta, sha256=hash_archivo(ruta), paginas=paginas)
        except Exception as exc:  # pdfplumber envuelve el error de clave
            ultimo_error = exc
            if not _parece_error_de_clave(exc):
                raise

    raise PdfProtegido(
        f"{ruta.name} esta protegido y ninguna clave sirvio. "
        f"Pasa la clave con --clave (podes repetir la opcion). Detalle: {ultimo_error}"
    )


def _parece_error_de_clave(exc: Exception) -> bool:
    texto = str(exc).lower()
    return any(p in texto for p in ("password", "decrypt", "encrypted", "clave"))
