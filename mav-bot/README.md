# Bot de ejecución MAV

Automatización de cotización en subastas de cheques (CPD / echeq) en la
Plataforma Trading MAV.

**Estado: relevamiento.** Todavía no hay bot. Lo único que hay acá son las
herramientas para capturar las pantallas de la plataforma, que es el insumo
para escribir el parser y la máquina de estados.

## Reglas que no se negocian

- El bot **nunca** confirma ni envía órdenes en producción sin que un humano lo
  haya habilitado explícitamente para ese lote, y nunca durante el relevamiento.
- El login lo hace el trader **a mano**. Las credenciales no se guardan, no se
  escriben en ningún archivo y no viajan a ningún lado.
- Las herramientas de esta carpeta son **read-only** contra la plataforma.

## Por qué el login es a mano

La plataforma tiene 2FA con código que expira. Eso no es solo un tema de
credenciales: significa que **el bot no puede re-loguearse solo**. Si la sesión
se cae en medio de una subasta, el bot queda ciego con órdenes vivas en el libro
que no puede modificar.

De ahí salen dos requisitos del diseño final: detección de sesión caída con kill
switch inmediato, y la regla operativa de que el trader no se aleja de la
máquina mientras el bot corre.

## Captura de pantallas

### 1. Instalar

```
pip install playwright
```

Alcanza con eso. No hace falta `playwright install`: nos enganchamos a tu Chrome,
no descargamos uno. (Si igual se queja, corré `playwright install chromium`.)

### 2. Abrir Chrome con el puerto de debug

Cerrá esto si ya lo tenías abierto y corré, en `cmd`:

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\mav-chrome"
```

El `--user-data-dir` es un perfil aparte, así que este Chrome convive con el que
usás normalmente. Sin ese flag, Chrome se cuelga del proceso que ya está
corriendo y el puerto de debug queda sin abrir.

Con Edge es igual, cambiando la ruta por
`C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe`.

> Mientras ese puerto esté abierto, cualquier programa que corra en esa misma
> máquina puede manejar ese Chrome. Usalo solo para esto y cerralo cuando
> termines.

### 3. Loguearte a mano

En esa ventana, entrar a la plataforma y hacer el login con tu 2FA como siempre.
El perfil queda guardado, así que la próxima vez arrancás de más cerca.

### 4. Capturar

Con la pantalla de subasta abierta:

```
python captura\capturar.py
```

Guarda un snapshot cada vez que la pantalla cambia — no cada N segundos, así que
no te llena el disco de capturas idénticas. Sondea cada 5 segundos cuando está
tranquilo y acelera a 2 cuando el libro se mueve.

Toma **todas** las pestañas de la plataforma que tengas abiertas, así que podés
ir navegando a *modificar orden*, su preview y *consulta de órdenes* mientras
corre, y se captura solo. `Ctrl+C` para cortar.

Variantes:

```
python captura\capturar.py --once              un solo snapshot de lo que haya abierto
python captura\capturar.py --filtro subasta    solo las URLs que digan "subasta"
```

Para las pantallas sueltas también sirve **Ctrl+S → "Página web, solo HTML"**,
que preserva los bytes originales tal cual (la plataforma es ISO-8859-1). Las dos
formas son útiles: el Ctrl+S tiene más fidelidad, el script tiene la línea de
tiempo.

### 5. Anonimizar antes de compartir

Armá un `terminos.txt` con los nombres de comitente que aparezcan en pantalla:

```
# uno por linea
Juan Perez S.A.
ACME SRL => COMITENTE_ACME
```

Y corré:

```
python captura\scrub.py capturas\20260921_143000 --terminos terminos.txt
```

Deja una carpeta `_limpio` al lado. Reemplaza los nombres por placeholders
estables (el mismo comitente es siempre el mismo placeholder en las 200
capturas), tapa CUITs y tapa los valores de campos hidden con pinta de token.

**Deja intactos a propósito:** los nombres de los campos hidden, los números de
agente de las contrapartes, y las tasas y horarios de las puntas. Los tres hacen
falta para escribir el bot.

El archivo `MAPEO_NO_COMPARTIR.json` queda en la carpeta original. Es la llave
para des-anonimizar: no se comparte y no se commitea.

Abrí un par de archivos de `_limpio` y verificá antes de mandarlos.

## Qué falta relevar

- [ ] Pantalla de subasta, línea de tiempo completa de una guerra de tasas
- [ ] Pantalla de modificar orden y su preview
- [ ] Pantalla de consulta de órdenes
- [ ] Cómo se ve un rechazo (límite, tasa fuera de rango, instrumento caído)
- [ ] Si la pantalla se auto-refresca sola y cada cuánto
- [ ] Cuánto sobrevive el estado pre-confirm antes de expirar
- [ ] Si el libro muestra más de un nivel de puntas o solo la mejor
- [ ] Si hay algo más fino que el número de agente para distinguir órdenes
      propias de las de un compañero de mesa

## Lo que ya sabemos

Del HTML de la pantalla de login:

- Progress OpenEdge WebSpeed. Respuestas en HTML, sin API documentada.
- El form postea a `validar2.r` con `id`, `password` y los hidden `destino`,
  `login=true`, `validarcodigo=true`, `metodo2fa`.
- Cookies de sesión: `mvrcookie`, `mvrusername`.
- Encoding ISO-8859-1.
- 2FA obligatorio con código que expira, teclado virtual anti-keylogger, y un
  mecanismo de inhibición de usuario que solo un usuario Master de la oficina
  puede levantar.

Ese último punto es la razón del backoff agresivo ante errores: una ráfaga de
requests fallidos es exactamente cómo te quedás afuera en medio de la rueda.
