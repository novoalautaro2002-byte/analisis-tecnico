"""Tests de la conversacion con la plataforma y del ingreso."""

import unittest

from motor.sesion import (
    CODIFICACION,
    ErrorDePlataforma,
    IngresoRechazado,
    Pendiente,
    Sesion,
    es_pantalla_de_login,
    pide_codigo,
)

# La pantalla real: form a validar2.r con dos campos visibles y varios hidden.
LOGIN = """<html><head><title>Inicio de Sesi&oacute;n</title></head><body>
<form method="POST" name="form" action="validar2.r" autocomplete="off">
<input type="hidden" name="destino" value="/cgi-bin/x/mvr-usuarios.r">
<input type="hidden" name="login" value="true">
<input type="text" name="id" size="20" value="">
<input type="password" name="password" size="20">
<input type="hidden" name="validarcodigo" value="true">
<input type="hidden" name="metodo2fa" value="">
</form></body></html>"""

# El paso del codigo. Sigue siendo la pantalla de login y CONSERVA el campo
# password: por eso hace falta el marcador positivo (los ids del contador y el
# boton de reenvio, que el login inicial no trae).
CODIGO = """<html><head><title>Inicio de Sesi&oacute;n</title></head><body>
<form method="POST" action="validar2.r">
<input type="hidden" name="destino" value="/cgi-bin/x/mvr-usuarios.r">
<input type="hidden" name="login" value="true">
<input type="hidden" name="metodo2fa" value="mail">
<input type="text" name="id" value="lautaro">
<input type="password" name="password" value="">
<div id="ingresarcodigo">Ingresá el código</div>
<div id="tiempo"></div><input type="hidden" id="contador" value="120">
<input type="text" name="codigo" value="">
<button id="reenviar">Reenviar</button>
</form></body></html>"""

ADENTRO = ('<html><script>var myData = [];var myColumns=["Oferta"];</script>'
           '<input type="hidden" name="ident" value="900"></html>')

INHIBIDO = LOGIN.replace("<body>", "<body>Su usuario se encuentra inhibido.")
MAL = LOGIN.replace("<body>", "<body>Usuario o contrase&ntilde;a incorrectos.")


class SesionFalsa(Sesion):
    """Sesion con la red reemplazada por un guion de respuestas."""

    def __init__(self, guion, **kw):
        super().__init__(**kw)
        self.guion = list(guion)
        self.enviados = []

    def _pedir(self, pedido, en_login=False):
        if pedido.data:
            self.enviados.append(pedido.data.decode(CODIFICACION))
        return self.guion.pop(0) if self.guion else ADENTRO


class TestCookiePegada(unittest.TestCase):
    def test_acepta_el_volcado_entero_de_document_cookie(self):
        s = Sesion.desde_texto(
            "_ga=GA1.2.999; mvrcookie=ABC123; mvrusername=lautaro; otra=x")
        self.assertIn("mvrcookie=ABC123", s.cabecera_cookie)
        self.assertNotIn("_ga", s.cabecera_cookie)

    def test_sin_mvrcookie_avisa_claro(self):
        for basura in ("", "mvrusername=lautaro", "hola"):
            with self.assertRaises(ErrorDePlataforma) as e:
                Sesion.desde_texto(basura)
            self.assertIn("mvrcookie", str(e.exception))


class TestIngreso(unittest.TestCase):
    def test_pide_el_codigo_sin_saber_como_se_llama(self):
        # El nombre del campo sale del formulario que manda el servidor, no de
        # una constante nuestra.
        s = SesionFalsa([LOGIN, CODIGO])
        pendiente = s.ingresar("lautaro", "secreta")
        self.assertIsInstance(pendiente, Pendiente)
        self.assertEqual(pendiente.campos, ("codigo",))

    def test_manda_usuario_y_clave_con_los_hidden_intactos(self):
        s = SesionFalsa([LOGIN, CODIGO])
        s.ingresar("lautaro", "secreta")
        enviado = s.enviados[0]
        self.assertIn("id=lautaro", enviado)
        self.assertIn("password=secreta", enviado)
        self.assertIn("validarcodigo=true", enviado)
        self.assertIn("login=true", enviado)

    def test_completa_el_codigo_y_entra(self):
        s = SesionFalsa([LOGIN, CODIGO, ADENTRO, ADENTRO])
        s.ingresar("lautaro", "secreta")
        self.assertIsNone(s.continuar({"codigo": "123456"}))
        self.assertIn("codigo=123456", s.enviados[1])
        # Y reenvia los hidden del paso del codigo, sin inventarlos.
        self.assertIn("metodo2fa=mail", s.enviados[1])

    def test_entra_directo_si_no_hay_segundo_paso(self):
        s = SesionFalsa([LOGIN, ADENTRO, ADENTRO])
        self.assertIsNone(s.ingresar("lautaro", "secreta"))

    def test_continuar_sin_ingreso_previo(self):
        with self.assertRaises(IngresoRechazado):
            SesionFalsa([]).continuar({"codigo": "1"})


class TestRechazos(unittest.TestCase):
    def test_credenciales_mal(self):
        s = SesionFalsa([LOGIN, MAL])
        with self.assertRaises(IngresoRechazado) as e:
            s.ingresar("lautaro", "mala")
        self.assertIn("incorrect", str(e.exception).lower())

    def test_usuario_inhibido_explica_que_hacer(self):
        # Es el caso que deja al trader afuera en medio de la rueda: el mensaje
        # tiene que decirle a quien recurrir.
        s = SesionFalsa([LOGIN, INHIBIDO])
        with self.assertRaises(IngresoRechazado) as e:
            s.ingresar("lautaro", "secreta")
        self.assertIn("Master", str(e.exception))


class TestDeteccionDelPasoDelCodigo(unittest.TestCase):
    """El paso del codigo conserva el campo password, asi que no sirve de
    discriminante. Se detecta por los elementos que solo existen ahi."""

    def test_el_login_inicial_no_pide_codigo(self):
        self.assertFalse(pide_codigo(LOGIN))

    def test_el_paso_del_codigo_se_reconoce(self):
        self.assertTrue(pide_codigo(CODIGO))

    def test_lo_reconoce_aunque_conserve_el_password(self):
        self.assertIn('name="password"', CODIGO)
        s = SesionFalsa([LOGIN, CODIGO])
        self.assertEqual(s.ingresar("lautaro", "secreta").campos, ("codigo",))


class TestDeteccionDeLogin(unittest.TestCase):
    def test_reconoce_la_pantalla_de_login(self):
        self.assertTrue(es_pantalla_de_login(LOGIN))
        self.assertTrue(es_pantalla_de_login(
            '<form method="POST" name="form" action="validar2.r">'))

    def test_no_confunde_una_subasta(self):
        self.assertFalse(es_pantalla_de_login(ADENTRO))

    def test_solo_mira_el_principio(self):
        # La palabra puede aparecer al pie de cualquier pagina larga; el
        # formulario de login esta siempre arriba.
        self.assertFalse(es_pantalla_de_login("x" * 5000 + "validar2.r"))


class TestCodificacion(unittest.TestCase):
    def test_es_la_de_la_plataforma(self):
        # MAV emite y espera ISO-8859-1: mandar UTF-8 rompe los acentos.
        self.assertEqual(CODIFICACION, "latin-1")


if __name__ == "__main__":
    unittest.main()
