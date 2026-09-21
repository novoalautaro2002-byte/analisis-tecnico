"""Tests del armado del POST.

La regla que se prueba acá es una sola: el bot copia, no escribe. Si algun
campo que no sea la tasa o la accion termina con un valor que el servidor no
mando, tiene que explotar.
"""

import unittest

from motor.formulario import (
    ALTA_COMPRA,
    CAMPOS_EDITABLES,
    CampoProhibido,
    FormularioIlegible,
    armar_baja,
    armar_oferta,
    parsear_campos,
    verificar,
)

CHEQUES = ("02579750", "02579751")


def html_subasta(ident=1556714, cheques=CHEQUES):
    """Como lo manda cpd-versubasta.r: los campos por cheque vienen VACIOS."""
    porcheque = "\n".join(
        f'<input type="hidden" name="comitcpr{c}">\n'
        f'<input type="hidden" name="cuitcpr{c}">\n'
        f'<input type="hidden" name="excepcpr{c}" value"No">\n'
        f'<input type="hidden" name="condcpr{c}">'
        for c in cheques
    )
    return f"""<form method="post">
<input type="hidden" name="action">
<input type="hidden" name="id">
<input type="hidden" name="ident" value="{ident}">
{porcheque}
<input type="hidden" id="tipoinstrumento" name="tipoinstrumento" value="ECHEQ">
<input style="text-align: right" type="text" name="tasa" size="6" value="">
</form>"""


def html_cheques(cheques=CHEQUES, comitente="51414", cuit="30-11111111-1"):
    """Como lo manda cpd-ch-subasta-i-v2.r, con el name= repetido y todo."""
    return "\n".join(
        f"""<input data-role='none' type='text' size='9' value='{comitente}'
              name='comit-cpr{c}' id='idcomitcpr{c}'>
<input data-role='none' type="hidden" value="{comitente}" name="comitcpr{c}" name="hidcomitcpr{c}">
<input data-role='none' type="hidden" value="{cuit}" name="cuitcpr{c}" id="hidcuitcpr{c}">
<input data-role='none' type="hidden" value="Si" name="excepcpr{c}" name="hidexcepcpr{c}">
<input data-role='none' type="hidden" value="EX" name="condcpr{c}" name="hidcondcpr{c}">"""
        for c in cheques
    )


class TestParseoDeCampos(unittest.TestCase):
    def test_el_primer_name_gana(self):
        # El iframe de MAV trae name= dos veces. El navegador se queda con el
        # primero; quedarse con el ultimo postearia sin comitente.
        campos = parsear_campos(
            '<input type="hidden" value="51414" name="comitcpr1" name="hidcomitcpr1">'
        )
        self.assertEqual(campos, {"comitcpr1": "51414"})

    def test_input_sin_value(self):
        self.assertEqual(parsear_campos('<input type="hidden" name="action">'),
                         {"action": ""})

    def test_value_mal_escrito_se_lee_vacio(self):
        # En la pantalla real hay varios `value"No"`, sin el igual.
        self.assertEqual(parsear_campos('<input name="excepcpr1" value"No">'),
                         {"excepcpr1": ""})

    def test_comillas_simples_y_dobles(self):
        campos = parsear_campos(
            "<input name='a' value='1'><input name=\"b\" value=\"2\">")
        self.assertEqual(campos, {"a": "1", "b": "2"})

    def test_ignora_inputs_sin_nombre(self):
        self.assertEqual(parsear_campos('<input type="button" value="Filtrar">'), {})


class TestArmarOferta(unittest.TestCase):
    def test_trae_los_comitentes_del_iframe(self):
        # El form padre los manda vacios; el valor real vive en el iframe.
        p = armar_oferta(html_subasta(), html_cheques(), "25,44")
        self.assertEqual(p.campos["comitcpr02579750"], "51414")
        self.assertEqual(p.campos["cuitcpr02579750"], "30-11111111-1")
        self.assertEqual(p.campos["excepcpr02579750"], "Si")
        self.assertEqual(p.campos["condcpr02579750"], "EX")

    def test_pone_tasa_y_accion(self):
        p = armar_oferta(html_subasta(), html_cheques(), "25,44")
        self.assertEqual(p.campos["tasa"], "25,44")
        self.assertEqual(p.campos["action"], ALTA_COMPRA)

    def test_conserva_el_resto_tal_cual(self):
        p = armar_oferta(html_subasta(ident=900), html_cheques(), "25,44")
        self.assertEqual(p.campos["ident"], "900")
        self.assertEqual(p.campos["tipoinstrumento"], "ECHEQ")

    def test_no_agrega_campos_que_la_pantalla_no_tenia(self):
        p = armar_oferta(html_subasta(), html_cheques(), "25,44")
        de_mas = set(p.campos) - set(parsear_campos(html_subasta()))
        self.assertEqual(de_mas, set())

    def test_solo_tasa_y_accion_cambian(self):
        original = parsear_campos(html_subasta())
        p = armar_oferta(html_subasta(), html_cheques(), "25,44")
        cambiados = {k for k, v in p.campos.items() if original.get(k) != v}
        # Los de cheque cambian porque se llenan desde el iframe; el resto no.
        self.assertTrue(cambiados <= (CAMPOS_EDITABLES | {
            k for k in original if k.startswith(
                ("comitcpr", "cuitcpr", "excepcpr", "condcpr"))}))


class TestFallaCerrado(unittest.TestCase):
    def test_sin_ident(self):
        html = html_subasta().replace('name="ident"', 'name="otracosa"')
        with self.assertRaises(FormularioIlegible):
            armar_oferta(html, html_cheques(), "25,44")

    def test_sin_campo_tasa(self):
        html = html_subasta().replace('name="tasa"', 'name="tasax"')
        with self.assertRaises(FormularioIlegible):
            armar_oferta(html, html_cheques(), "25,44")

    def test_iframe_sin_comitentes(self):
        with self.assertRaises(FormularioIlegible):
            armar_oferta(html_subasta(), "<html>nada</html>", "25,44")

    def test_comitente_vacio_no_se_completa(self):
        # Si el servidor lo manda vacio es que algo no esta cargado. El bot no
        # inventa un numero de comitente.
        iframe = html_cheques(comitente="")
        with self.assertRaises(FormularioIlegible):
            armar_oferta(html_subasta(), iframe, "25,44")


class TestVerificar(unittest.TestCase):
    """La red que atrapa al bot si alguna vez inventa un valor."""

    def test_acepta_lo_que_mando_el_servidor(self):
        pagina = {"ident": "900", "comitcpr1": "", "tasa": ""}
        iframe = {"comitcpr1": "51414"}
        verificar({"ident": "900", "comitcpr1": "51414", "tasa": "25,44",
                   "action": "altaCompra"}, pagina, iframe)

    def test_rechaza_un_comitente_inventado(self):
        pagina = {"ident": "900", "comitcpr1": ""}
        iframe = {"comitcpr1": "51414"}
        with self.assertRaises(CampoProhibido) as e:
            verificar({"ident": "900", "comitcpr1": "99999"}, pagina, iframe)
        self.assertIn("comitcpr1", str(e.exception))

    def test_rechaza_cambiar_el_numero_de_subasta(self):
        with self.assertRaises(CampoProhibido):
            verificar({"ident": "111"}, {"ident": "900"}, {})

    def test_rechaza_un_campo_nuevo(self):
        with self.assertRaises(CampoProhibido):
            verificar({"ident": "900", "colado": "x"}, {"ident": "900"}, {})

    def test_tasa_y_accion_son_libres(self):
        verificar({"tasa": "1,23", "action": "altaCompra", "id": "7"}, {}, {})


class TestBaja(unittest.TestCase):
    def test_arma_la_baja_con_el_id(self):
        p = armar_baja(html_subasta(), 2046207)
        self.assertEqual(p.campos["action"], "bajaCompra")
        self.assertEqual(p.campos["id"], "2046207")
        self.assertEqual(p.campos["ident"], "1556714")


class TestValidacionesDelCliente(unittest.TestCase):
    """Las que hace el JS de la pantalla y el bot tiene que repetir.

    El bot postea directo, sin pasar por `ofertaCompra()`. Todo lo que esa
    funcion valida antes de mandar, lo tiene que validar el bot o manda cosas
    que el servidor puede llegar a aceptar mal.
    """

    def test_el_comitente_en_cero_no_pasa(self):
        # No esta vacio, asi que el control de campos en blanco no lo agarra.
        # Y es exactamente como se ve un comitente que no quedo cargado.
        with self.assertRaises(FormularioIlegible):
            armar_oferta(html_subasta(), html_cheques(comitente="0"), "26,98")

    def test_un_comitente_que_no_es_numero_no_pasa(self):
        with self.assertRaises(FormularioIlegible):
            armar_oferta(html_subasta(), html_cheques(comitente="ACME"), "26,98")

    def test_un_comitente_negativo_no_pasa(self):
        with self.assertRaises(FormularioIlegible):
            armar_oferta(html_subasta(), html_cheques(comitente="-5"), "26,98")

    def test_el_comitente_normal_pasa(self):
        p = armar_oferta(html_subasta(), html_cheques(comitente="51414"), "26,98")
        self.assertEqual(dict(p.pares())[f"comitcpr{CHEQUES[0]}"], "51414")

    def test_la_tasa_viaja_con_coma(self):
        # Con punto, el JS de la plataforma rechaza antes de postear.
        p = armar_oferta(html_subasta(), html_cheques(), "26,98")
        self.assertEqual(dict(p.pares())["tasa"], "26,98")


if __name__ == "__main__":
    unittest.main()
