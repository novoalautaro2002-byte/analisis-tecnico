"""Log de todo lo que el bot ve y decide.

Una linea JSON por evento. El log es la mitad del valor del modo sombra: es
donde se compara, despues, lo que el bot habria hecho contra lo que hiciste vos.
"""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime
from pathlib import Path


class Registro:
    def __init__(self, ruta: Path, eco: bool = True, memoria: int = 500):
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.ruta = ruta
        self.fh = ruta.open("a", encoding="utf-8")
        self.eco = eco
        # Lo ultimo, para que la interfaz lo muestre sin releer el archivo.
        self.recientes: deque[dict] = deque(maxlen=memoria)

    def __call__(self, evento: str, mostrar: str | None = None, **datos) -> None:
        ahora = datetime.now()
        # El texto va al archivo tambien. Guardar solo el nombre del evento
        # dejaba un log inservible para reconstruir una rueda despues: decia
        # "libro" cincuenta veces sin decir que habia en el libro.
        fila = {"t": ahora.isoformat(timespec="seconds"), "evento": evento}
        if mostrar:
            fila["texto"] = mostrar
        fila.update(datos)
        self.fh.write(json.dumps(fila, ensure_ascii=False, default=str) + "\n")
        # Sin flush, un corte se lleva justamente los eventos que interesan.
        self.fh.flush()
        if mostrar:
            self.recientes.append({"t": f"{ahora:%H:%M:%S}",
                                   "evento": evento, "texto": mostrar})
            if self.eco:
                print(f"{ahora:%H:%M:%S}  {mostrar}", flush=True)

    def cerrar(self) -> None:
        self.fh.close()
