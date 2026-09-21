"""Tests del parser del libro.

Los fixtures reproducen el formato exacto de cpd-versubasta.r, incluido el
espacio adelante del id de oferta y el "[X]" adentro de la celda de baja, que es
lo que rompe cualquier parser que cuente corchetes.
"""

import pathlib
import unittest
from datetime import time
from decimal import Decimal

from motor.libro import (
    LibroIlegible,
    formatear_tasa,
    parsear_libro,
    parsear_tasa,
)

BAJA = "<a href='#' onClick='bajaOferta({id})'>[X]</a>"


def armar_html(ident, filas, columnas=("Oferta", "Ag.", "Desc.", "Ingreso", "Baja")):
    """Construye una pantalla con la forma de la real."""
    cuerpo = ",\n".join(
        "[ \"{id:>8}\",\"{ag}\", \"{tasa}\", \"{hora}\", \"{baja}\"]".format(
            id=f["id"], ag=f["ag"], tasa=f["tasa"], hora=f["hora"],
            baja=BAJA.format(id=f["id"]) if f["propia"] else "",
        )
        for f in filas
    )
    cols = ", ".join(f'"{c}"' for c in columnas)
    return f"""<html><head><script>
                var myData = [
{cuerpo}
                ];
                var myColumns = [ {cols} ];
</script></head><body>
<form method="post">
<input type="hidden" name="action">
<input type="hidden" name="ident" value="{ident}">
</form></body></html>"""


class TestParseoDeTasa(unittest.TestCase):
    def test_coma_decimal(self):
        self.assertEqual(parsear_tasa("10,00"), Decimal("10.00"))
        self.assertEqual(parsear_tasa("26,99"), Decimal("26.99"))

    def test_punto_de_miles(self):
        self.assertEqual(parsear_tasa("1.234,56"), Decimal("1234.56"))

    def test_negativa(self):
        # Se admite en PAGARE y FCE; la plataforma valida el instrumento, no el parser.
        self.assertEqual(parsear_tasa("-1,50"), Decimal("-1.50"))

    def test_ilegible_no_adivina(self):
        for basura in ("", "ocho", "--3", "1,2,3"):
            with self.assertRaises(LibroIlegible):
                parsear_tasa(basura)

    def test_ida_y_vuelta(self):
        self.assertEqual(formatear_tasa(Decimal("26.99")), "26,99")
        self.assertEqual(formatear_tasa(Decimal("27")), "27,00")
        self.assertEqual(parsear_tasa(formatear_tasa(Decimal("26.99"))), Decimal("26.99"))


class TestParseoDelLibro(unittest.TestCase):
    def test_una_oferta_propia(self):
        html = armar_html(1556714, [
            {"id": 2046207, "ag": "442", "tasa": "10,00", "hora": "10:51:52", "propia": True},
        ])
        libro = parsear_libro(html)
        self.assertEqual(libro.ident, 1556714)
        self.assertEqual(len(libro.ofertas), 1)
        o = libro.ofertas[0]
        self.assertEqual(o.id, 2046207)
        self.assertEqual(o.agente, "442")
        self.assertEqual(o.tasa, Decimal("10.00"))
        self.assertEqual(o.ingreso, time(10, 51, 52))
        self.assertTrue(o.propia)

    def test_la_baja_distingue_propias_de_ajenas(self):
        # El numero de agente lo comparte toda la mesa, asi que dos ofertas del
        # mismo agente pueden no ser ambas del bot. El link de baja si distingue.
        html = armar_html(900, [
            {"id": 1, "ag": "442", "tasa": "27,00", "hora": "10:00:00", "propia": True},
            {"id": 2, "ag": "442", "tasa": "26,99", "hora": "10:00:05", "propia": False},
        ])
        libro = parsear_libro(html)
        self.assertEqual([o.id for o in libro.propias], [1])
        self.assertEqual([o.id for o in libro.ajenas], [2])

    def test_mejores_puntas(self):
        html = armar_html(900, [
            {"id": 1, "ag": "442", "tasa": "27,00", "hora": "10:00:00", "propia": True},
            {"id": 2, "ag": "999", "tasa": "26,50", "hora": "10:01:00", "propia": False},
            {"id": 3, "ag": "777", "tasa": "26,99", "hora": "10:00:30", "propia": False},
        ])
        libro = parsear_libro(html)
        self.assertEqual(libro.mejor_propia().id, 1)
        self.assertEqual(libro.mejor_ajena().id, 2)   # gana la tasa mas baja

    def test_empate_de_tasa_desempata_por_hora(self):
        html = armar_html(900, [
            {"id": 7, "ag": "999", "tasa": "26,00", "hora": "10:05:00", "propia": False},
            {"id": 8, "ag": "777", "tasa": "26,00", "hora": "10:02:00", "propia": False},
        ])
        self.assertEqual(parsear_libro(html).mejor_ajena().id, 8)

    def test_libro_vacio(self):
        libro = parsear_libro(armar_html(900, []))
        self.assertEqual(libro.ofertas, ())
        self.assertIsNone(libro.mejor_propia())
        self.assertIsNone(libro.mejor_ajena())

    def test_corchete_adentro_de_la_celda_de_baja(self):
        # El "[X]" del link rompe a cualquiera que corte filas por corchetes.
        html = armar_html(900, [
            {"id": 1, "ag": "442", "tasa": "27,00", "hora": "10:00:00", "propia": True},
            {"id": 2, "ag": "442", "tasa": "26,00", "hora": "10:00:01", "propia": True},
        ])
        self.assertEqual(len(parsear_libro(html).ofertas), 2)


class TestFallaCerrado(unittest.TestCase):
    def test_sin_ident(self):
        html = armar_html(900, []).replace('name="ident" value="900"', 'name="otro"')
        with self.assertRaises(LibroIlegible):
            parsear_libro(html)

    def test_sin_mydata(self):
        with self.assertRaises(LibroIlegible):
            parsear_libro('<input type="hidden" name="ident" value="900">')

    def test_columnas_inesperadas(self):
        # Si MAV renombra o agrega columnas, se para en vez de leer corrido.
        html = armar_html(900, [
            {"id": 1, "ag": "442", "tasa": "27,00", "hora": "10:00:00", "propia": True},
        ], columnas=("Oferta", "Ag.", "Desc.", "Ingreso", "Baja", "Nueva"))
        with self.assertRaises(LibroIlegible):
            parsear_libro(html)

    def test_celdas_que_no_cierran_filas(self):
        html = armar_html(900, [
            {"id": 1, "ag": "442", "tasa": "27,00", "hora": "10:00:00", "propia": True},
        ]).replace(', "10:00:00"', "")
        with self.assertRaises(LibroIlegible):
            parsear_libro(html)


class TestContraElHtmlReal(unittest.TestCase):
    """Regresion contra un recorte literal de la pantalla de MAV."""

    def test_recorte_real(self):
        ruta = pathlib.Path(__file__).parent / "fixtures" / "versubasta_recorte.html"
        libro = parsear_libro(ruta.read_text(encoding="utf-8"))
        self.assertEqual(libro.ident, 1000001)
        self.assertEqual(len(libro.ofertas), 1)
        o = libro.ofertas[0]
        self.assertEqual(o.id, 2046207)          # el id viene con un espacio adelante
        self.assertEqual(o.tasa, Decimal("10.00"))
        self.assertEqual(o.ingreso, time(10, 51, 52))
        self.assertTrue(o.propia)


if __name__ == "__main__":
    unittest.main()
