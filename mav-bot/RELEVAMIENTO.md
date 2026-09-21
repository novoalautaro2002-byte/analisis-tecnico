# Relevamiento de la plataforma

Lo que sabemos hasta ahora, y de dónde salió.

## Lo más importante: hay una API JSON

El listado de subastas **no se renderiza en el servidor**. El `<tbody id="table-result">`
llega vacío y lo llena JavaScript desde un endpoint que devuelve JSON.

De `/controllers/subastas/cpd-subastas-controller.js`, línea 339:

```js
const url = `cpd-subastas-api.p?${params.toString()}`;
const response = await fetch(url, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' }
});
const data = await response.json();
const datosJSON = data['work-json'];
```

Esto responde la pregunta bloqueante del brief original: es el **caso 2**, un
endpoint interno no documentado que devuelve datos crudos. No es una API formal
ni contractual — puede cambiar sin aviso — pero para el listado significa que no
hay que parsear HTML.

### `cpd-subastas-api.p`

`GET`, autenticado por la cookie de sesión. Devuelve `{"work-json": [...]}`.

Parámetros (de `construirParametrosURL()`):

| Parámetro | Contenido |
|---|---|
| `p-subasta` | Tipo: `Estandar`, `Solo Exentos` |
| `p-segmento` | `Avalado`, `Garantizado`, `No Garantizado`, `Warrant`, `Granos a Fijar`, y las variantes `Calificado` |
| `p-instrumento` | `CPD`, `ECHEQ`, `FCE`, `PAGARE`, `PAGARE AJUSTE SOJA`, `PAGARE AJUSTE BADLAR`, `PAGARE AJUSTE TAMAR` |
| `p-estado` | `Todas`, `Activas`, `Concertadas`, `Desiertas`, `Propias`, `ComprasPropias`, `Favoritos` |
| `p-moneda` | `1` ARS, `2` BNAC, `3` EUR, `4` BCRA, `5` DOLA |
| `p-montoDesde` / `p-montoHasta` | Decimal |
| `p-ppvdesde` / `p-ppvhasta` | Promedio Ponderado de Vida, en días |
| `p-sgr` | Código de SGR (hay ~120 en el select) |
| `p-plazo` | `""` todos, `00` C.I., `24` 24hs |
| `p-ident` | Número de subasta |
| `p-cuit` | CUIT librador/deudor |

Listas múltiples: valores separados por coma, sin espacios.

Campos que el controller lee de cada elemento: `ident`, `segmento`, `pyme`,
`caracter`, `certificantes`, `ciega`, `estado`, `favorita`, `monto`, `ppv`,
`sinrecurso`, `svs`.

#### CONFIRMADO: la respuesta trae mucho más que lo que el controller lee

Diagnóstico del 21/09 sobre la subasta 1556714. Campos reales de cada fila:

```
agente-cpr, agente-vdr, benef-cuit, benef-razon, cantidad-cheques, caracter,
cert-pyme, certificantes, ciega, clausula-nalo, con-tasa-v, custodio, depmav,
detalle-sello, err, es-producto, estado, expone-libr, favorita, fecha-sub,
hora-cierre, hora-cierre-ss, hora-concer, hora-concer-ss, ident, informa-benef,
lote-id, mensaje, moneda, moneda-paridad, moneda-signo, monto, perfil-cpr,
perfil-vdr, plazo-liquidacion, ppv, primera-neg, pyme, razon-cpr, razon-vdr,
resp-codigo, resp-cuit, resp-nombre, segmento, sinrecurso, solo-exentos, status,
svs, tasa-cpr, tasa-vdr, tiempo-minimo, tiempo-minimo-ss, tipo-instrumento,
tipo-sello, usuario, usuario-cpr, vn-cpr, vn-vdr, x-cod-op, x-perfil,
x-sb-graficador, x-usuario, ya-negociado
```

Mapeo de las columnas de la pantalla:

| Columna | Campo |
|---|---|
| `T.Min.` | `tiempo-minimo` (+ `tiempo-minimo-ss`, segundos) |
| `Cierre` | `hora-cierre` (+ `hora-cierre-ss`) |
| `Of.C.` | `tasa-cpr` — la mejor tasa compradora |
| `Cpr` | `agente-cpr` — el agente que tiene esa punta |
| `Of.V.` | `tasa-vdr` |
| `Vdr` | `agente-vdr` |
| `H.Conc.` | `hora-concer` (+ `hora-concer-ss`) |
| `CH` | `cantidad-cheques` |
| `1 Neg.` | `primera-neg` |
| `Est.` | `estado` (+ `ya-negociado`) |

**Por qué importa.** `tasa-cpr` + `agente-cpr` es exactamente el estado de la
guerra, y viene por subasta en un solo pedido sin filtrar por `p-ident`. O sea:
un request alcanza para saber el estado de las N subastas vigiladas. El bot lo
usa como radar (`motor/ficha.py`, `Mesa._refrescar_tablero`) para dos cosas:
saber si la subasta sigue activa sin gastar un pedido por subasta, y saber si
vale la pena abrir el libro.

**Lo que NO hace.** El radar no decide ofertas. La tasa que se carga sale
siempre del libro de `cpd-versubasta.r`. Que `tasa-cpr` sea la punta compradora
es una lectura de las columnas, no algo documentado, así que el atajo hay que
ganárselo: no se usa hasta que el tablero coincide con el libro al menos una
vez, y si después difieren se apaga para el resto de la sesión.

El contraste se hace **solo con el libro quieto** — cuando no cambió desde la
lectura anterior. El tablero es una foto de hasta dos segundos atrás: en plena
guerra difiere del libro casi siempre, y no porque mienta sino porque el libro
se movió en el medio. Sin esa condición el atajo se apagaba solo en la primera
recotización, que es justo donde sirve.

### `cpd-api-infosubasta.r?ident=<n>`

También `GET` y también JSON (`response.json()` en la línea 828). Detalle de una
subasta: situación, banco, comitente, endosos, MTAC, TAV, carácter, ciega.

## Mapa de la aplicación

Frameset de cinco documentos. El contenido real va en `FS_main`:

| Frame | Programa |
|---|---|
| `FS_header` | `k-cabeceranew.r` |
| `FS_left` | `lateral_izq_neutral.r` |
| `FS_main` | la pantalla que se esté viendo |
| `FS_right` | `lateral_der_neutral.r` |
| `FS_footer` | `k-pie.r` |

Guardar la pestaña con Ctrl+S trae solo el `<frameset>`. Hay que abrir el
programa directamente por URL, o guardar el marco.

### Programas, del menú de navegación

**Cheques:** `cpd-instrumentos-listado.r`, `cpd-autorizados.r`, `cpd-remesa.r`,
`cpd-remesa-war.r`, `cpd-remesa-gafi.r`, `cpd-remesarescate.r`,
`cpd-editcheque.r`, `cpd-cambio-segmento.r`, `cpd-cambio-segmento-masivo.r`,
`cpd-informar-segmento.r`, `cpd-marcado-manual-infra.r`

**Lotes:** `cpd-lotes-listado.r?estado=Negociable|EnSubasta|Negociado|Eliminado|Todos`

**Subastas:** `cpd-subastas-listado.r?estado=...`, **`cpd-versubasta.r?ident=<n>`**,
`cpd-bajasubasta.r`, `cpd-subastas-listado-historico.r`

**Ofertas:** `cpd-ofertas.r?estado=Todas|Activas|Concertadas|Canceladas`

**Int. Compra:** `cpd-intcompras.r?filtro=Todas|Propias|Terceros`, `cpd-creaintcompra.r`

**Operado:** `cpd-instrumentos-operados.r`, `cpd-totales-operados-comitentes.r`,
`cpd-cambio-comit.r`, `cpd-totales.r`, `cpd-instrumentos-comprados.r`,
`cpd-instrumentos-vendidos.r`, `cpd-concertacion.r`, `cpd-registro-operaciones.r`

## `cpd-versubasta.r?ident=<n>` — la pantalla del bot

HTML renderizado en el servidor, sin controller JS. jQuery 1.9.1 y la grilla
Active Widgets (`/runtimex/lib/aw.js`).

### El libro viene en un array de JavaScript

No hay que raspar celdas de una tabla: las ofertas están en un array embebido,
que Active Widgets después dibuja.

```js
var myData = [
[ " 2046207","442", "10,00", "10:51:52", "<a href='#' onClick='bajaOferta(2046207)'>[X]</a>"]
];
var myColumns = [ "Oferta", "Ag.", "Desc.", "Ingreso", "Baja" ];
```

| Columna | Qué es |
|---|---|
| `Oferta` | ID de la oferta. Viene con un espacio adelante. |
| `Ag.` | Número de agente |
| `Desc.` | La tasa, con **coma** decimal |
| `Ingreso` | Hora `hh:mm:ss` |
| `Baja` | Link `[X]` con `bajaOferta(<id>)` |

**La propiedad se decide por el número de agente, no por el link de baja.**
Primero deduje lo contrario — que el `[X]` de la columna `Baja` era la marca más
confiable — y estaba mal. Lo corrigió el trader: *"yo soy el 442 siempre, no se
opera por usuario, se opera por número de agente"*. Una oferta es propia cuando
`Ag.` coincide con el agente del ALyC, y punto; que un compañero de mesa la haya
cargado no la hace ajena. El link de baja se usa solo como respaldo para una
mirada suelta, porque la plataforma lo saca cuando la subasta cierra.

### Alta, modificación y baja son un solo POST

No hay flujo de varias pantallas ni pantalla de preview. Un `<form method="post">`
sin `action`, o sea que postea contra la misma URL, con estos campos:

| Campo | Contenido |
|---|---|
| `action` | `altaCompra`, `bajaCompra`, `altaVenta`, `bajaVenta` |
| `id` | ID de la oferta, solo en las bajas |
| `ident` | Número de subasta |
| `tasa` | La tasa, con coma decimal |
| `comitcpr<idCheque>` | Comitente comprador, uno por cheque |
| `cuitcpr<idCheque>` | CUIT del comitente |
| `excepcpr<idCheque>` | `No` por defecto |
| `condcpr<idCheque>` | Condición |

El botón dice **"Modificar Tasa Cpr."** y llama a `ofertaCompra()`, que valida y
hace `action.value="altaCompra"` + `submit()`. O sea que **re-cotizar es el mismo
POST que dar de alta**: no hay un flujo aparte de modificación, y no existe la
ventana de quedarse sin punta en el libro.

La baja es todavía más directa:

```js
function bajaOferta(ident){
   if(confirm("Confirma baja de Oferta de Compra?")){
      document.forms[0].action.value="bajaCompra";
      document.forms[0].id.value=ident;
      document.forms[0].submit();
   }
}
```

### Consecuencias para el diseño

- **El staging del brief original no hace falta.** Se planteó para adelantar los
  round-trips de un flujo de varias pantallas; acá hay uno solo.
- **No hay preview contra el cual verificar.** Era el control de riesgo más
  valioso del plan y no existe. Hay que reemplazarlo por: validar el payload
  construido antes de postear, releer el libro inmediatamente antes, y verificar
  después releyendo `myData`.
- El cartel de confirmación es un `window.confirm()` del navegador. Un POST
  armado por el bot no lo dispara.

### Validaciones del cliente, a replicar

- La tasa usa **coma** decimal. Con punto, el JS rechaza antes de postear.
- Tasa negativa solo en `PAGARE` y `FCE`.
- Comitente comprador obligatorio, numérico, distinto de cero y no negativo.
- CUIT del comitente obligatorio.

Todo eso es validación de cliente: el bot arma el POST directo, así que tiene que
replicarlas él mismo o va a mandar cosas que el servidor puede aceptar mal.

### `La oferta de compra no ha sido ingresada`

Es el `else` del `window.confirm()` de `ofertaCompra()`, no un rechazo del
servidor. Si aparece, es que se canceló el cartel: no se mandó nada.

### Decisión de arquitectura: el bot solo toca la tasa

El trader carga a mano el comitente y la primera oferta. El bot se engancha a esa
página ya abierta y lo único que hace es, cuando alguien lo supera, escribir la
tasa nueva en el input `tasa` y apretar "Modificar Tasa Cpr.", aceptando el
`confirm()`.

No arma el POST. No escribe un solo campo de comitente.

Esto es posible porque `cpd-ch-subasta-i-v2.r` trae los comitentes ya resueltos
desde el servidor, en campos ocultos paralelos a los visibles:

```html
<input type='text'   name='comit-cpr<idCheque>' value='<comitente>'>   <!-- visible -->
<input type="hidden" name="comitcpr<idCheque>"  value="<comitente>">   <!-- el que viaja -->
<input type="hidden" name="cuitcpr<idCheque>"   value="<cuit>">
<input type="hidden" name="excepcpr<idCheque>"  value="Si">
<input type="hidden" name="condcpr<idCheque>"   value="EX">
```

Los visibles copian al oculto por `onKeyUp` / `onBlur`, pero el oculto **ya viene
con valor del servidor**, así que sobrevive a los recargos de página que provoca
cada POST. El bot no necesita que el trader vuelva a tipear nada.

Lo que gana este diseño:

- **El comitente nunca lo escribe el bot.** Es el campo donde un error significa
  comprar para el cliente equivocado, y queda fuera de su alcance por completo.
- **Corren las validaciones de la plataforma.** Coma decimal, tasa negativa solo
  en PAGARE y FCE, comitente obligatorio: las hace el JS de la página, no hay que
  replicarlas ni mantenerlas sincronizadas.
- **La whitelist es física.** El bot solo puede actuar sobre una subasta donde el
  trader ya cargó una oferta a mano. Si no hay oferta, no hay nada que modificar.
  No hay una lista de IDs en un archivo de configuración que pueda estar mal.
- **Falla cerrado por construcción.** Sin página abierta y sin oferta viva, el bot
  no tiene por dónde actuar.

Lo que cuesta: el bot depende de dos selectores del DOM (el input `tasa` y el
botón). Es una dependencia de scraping, pero de dos elementos, contra armar un
POST entero de 60 campos. El cambio es muy favorable.

El `window.confirm()` se acepta automáticamente desde el driver. Queda como punto
de decisión explícito y logueado.

### iframes anidados

Dentro de la pantalla hay dos más:

- `cpd-ch-subasta-i-v2.r?ident=<n>` (name `fcheques`) — los cheques del lote y los
  campos de comitente. `ofertaCompra()` copia los valores desde ahí al form
  principal con `window.frames.fcheques.document.forms.formul`.
- `cpd-of-compra-i.r?ident=<n>` (name `fofertasc`) — ofertas de compra.

O sea que la captura tiene que bajar frames anidados, no solo los cinco del
frameset de primer nivel.

## Login

- Form a `validar2.r`: `id`, `password`, y los hidden `destino`, `login=true`,
  `validarcodigo=true`, `metodo2fa`.
- Cookies: `mvrcookie`, `mvrusername`.
- 2FA con código que expira, una vez por día.
- Teclado virtual anti-keylogger.
- Inhibición de usuario que solo levanta un usuario Master de la oficina.

## Detalles técnicos

- Progress OpenEdge WebSpeed. Los `.r` son programas compilados; el `.p` de
  `cpd-subastas-api.p` es el que sirve JSON.
- Encoding **ISO-8859-1**, no UTF-8.
- jQuery 3.4.1, multiple-select, alertify, autoNumeric.
- Números en formato local: `.` separador de miles, `,` decimal. El parser tiene
  que normalizar antes de comparar tasas.
- Los filtros se persisten en `sessionStorage` bajo `filtrosTabla`.
- Los archivos bajo `/controllers/`, `/js/` y la raíz se sirven **sin
  autenticación**. Los endpoints `.p` y `.r` sí la piden.

## Pendiente

- [ ] Respuesta real de `cpd-subastas-api.p` para mapear los campos del JSON
- [ ] `cpd-ch-subasta-i-v2.r?ident=<n>`: el form `formul` con los campos de
      comitente por cheque, que es de donde sale la mitad del payload
- [ ] Cómo se ve la pantalla **después** de un alta: qué devuelve el POST y cómo
      se confirma que la oferta entró
- [ ] Cómo se ve un rechazo del servidor (distinto del `confirm()` cancelado)
- [ ] `cpd-ofertas.r?estado=Activas` para la reconciliación
- [ ] Si la pantalla de subasta se auto-refresca y cada cuánto
- [ ] Un `myData` con varias ofertas de agentes distintos, para ver el orden y
      confirmar que `Baja` solo aparece en las propias
- [ ] Si la plataforma admite dos sesiones simultáneas del mismo usuario

## CONFIRMADO: sesión única por usuario

El login del bot devolvió, textual:

> «Existe una sesión activa para el usuario en otra ubicación. Cierre
> correctamente la sesión anterior y vuelva a intentar.»

Era la pregunta de fondo desde el brief original, y la respuesta es la que más
restringe: **el usuario no puede tener dos sesiones a la vez.** El trader
logueado en su navegador y el bot logueado por su cuenta se excluyen.

Consecuencias:

- El bot y el trader comparten una sola sesión. O el bot la toma (el trader
  cierra la del navegador y opera a través del bot), o el bot usa la sesión del
  navegador del trader (misma cookie, misma máquina).
- El lote se ejecuta en serie, no en paralelo: era el supuesto del brief y queda
  firme.
- La secuencia que evita el conflicto y preserva la garantía de no tocar
  comitentes:
    1. El trader se loguea en su navegador y carga la oferta inicial a mano
       (comitente + primera tasa).
    2. Cierra la sesión del navegador (botón Salida, no solo la pestaña).
    3. Se loguea desde el bot. La oferta ya está viva en el libro y sobrevive al
       cambio de sesión, porque es una orden del mercado, no un estado de
       pantalla.
    4. El bot defiende la tasa. Nunca toca el comitente, que ya está cargado.
- Mientras el bot corre, el trader no puede mirar MAV en su navegador: mira por
  la interfaz del bot. Si quiere retomar el control, el bot para y él vuelve a
  entrar en el navegador.

## Corrección: `T.Min` es tiempo, no tasa

Lo leí mal durante todo el relevamiento. En la tabla del listado:

- **`T.Min`** es el **tiempo mínimo**: la hora a partir de la cual empieza a
  correr la cuenta regresiva de **3 minutos** para que la subasta se ejecute.
  Antes de esa hora la guerra de tasas ya está corriendo; lo que arranca en el
  T.Min es el reloj.
- **`Of.C.`** es la **oferta compradora**. Ahí es donde sucede la guerra.

### CONFIRMADO: los 3 minutos se reinician con cada mejora

Cierre blando. La subasta termina recién cuando pasan 3 minutos sin que nadie
mejore. Tres consecuencias, y son la base del diseño:

1. **La velocidad casi no decide el resultado.** No hay último segundo que ganar:
   quien mejora reabre el reloj. Gana el que sigue contestando cuando el otro
   deja de hacerlo. Contestar en 300 ms o en 8 segundos lleva al mismo lugar.
2. **El valor real del bot no es ser rápido, es no faltar nunca.** Un humano se
   distrae, atiende el teléfono, mira otra subasta. Ese es el error que el bot
   no comete, y es el único que importa acá.
3. **No hay endgame que cronometrar**, así que tampoco hay nada que un ritmo
   adaptativo pueda optimizar. Es una razón técnica más, además de la
   instrucción del trader, para que el bot ejecute la orden que recibe al
   activarse y nada más.

La reacción sí importa en un caso: si el rival mejora y el bot tarda más de 3
minutos en enterarse, la subasta se ejecuta. Con un sondeo de 1–3 segundos ese
margen sobra por dos órdenes de magnitud.

## CONFIRMADO por el trader: el alta de compra

Respuestas del operador, 21/09. Confirman lo que el HTML decía, y cierran las
dudas que quedaban sobre el camino de escritura.

| Pregunta | Respuesta |
|---|---|
| Al bajar la tasa, ¿queda una fila tuya o dos? | **Una.** Queda la nueva y desaparece la vieja. |
| ¿Qué botón apretás? | *"Modificar intención de compra"*. |
| ¿Hay que dar de baja la vieja primero? | **No.** |
| ¿Pide confirmar? | Sí, un cartel, y le da OK. |
| ¿Cuánto tarda en verse en la grilla? | **A la milésima** de tocar el botón. |
| ¿Una tasa por lote o por cheque? | **Una por todo el lote.** |
| ¿Se pueden tener dos ofertas propias en la misma subasta? | **No se puede.** |
| ¿Viste rechazos además del comitente? | **Jamás.** |

### Qué significa cada una para el bot

- **El botón de modificar es el mismo POST que el alta.** El HTML ya lo decía
  (`ofertaCompra()` hace `action.value="altaCompra"` y postea), y el trader lo
  confirma desde el otro lado: una sola fila por agente. El bot manda
  `altaCompra` en cada recotización y eso es correcto, no un atajo.
- **El cartel es un `window.confirm()` del navegador**, no una pantalla
  intermedia del servidor. Saltearlo no saltea ningún paso: el POST que sale
  después del OK es idéntico al que manda el bot.
- **Una sola oferta propia por subasta** convierte al contador de
  `verificar_despues()` en lo que tiene que ser: una red que no se toca nunca.
  Si alguna vez hay dos, algo cambió en la plataforma y hay que mirarlo a mano.
- **La grilla se actualiza al instante**, así que los 6 segundos de paciencia
  de la ventana de confirmación sobran por mucho. Si pasado ese tiempo la
  oferta no está, no es lentitud: es que no entró.
- **Nunca vio un rechazo** más allá del comitente faltante. No hay una familia
  de carteles de error que haya que aprender a leer.

### Lo que faltaba replicar del cliente, y ya está

`ofertaCompra()` valida antes de postear, y el bot no pasa por ahí:

- Tasa con **coma** decimal — `formatear_tasa()`.
- Comitente **numérico y distinto de cero** — `armar_oferta()`. Un `"0"` no lo
  agarraba el control de campos vacíos, y es exactamente como se ve un
  comitente que no quedó cargado.
- CUIT obligatorio — cubierto por el control de campos por cheque.
- Tasa negativa solo en `PAGARE` y `FCE`: este bot opera cheques, así que el
  piso de la banda plausible pasó de `-50` a `0` (`config.TASA_MIN_ABSOLUTA`).
