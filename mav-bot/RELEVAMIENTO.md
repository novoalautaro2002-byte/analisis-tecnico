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

Las columnas de la tabla renderizada sugieren que hay más: `1 Neg.`, `Sello`,
`Tipo`, `Plz`, **`T.Min.`**, **`Cierre`**, `Cpr`, **`Of.C.`**, **`Of.V.`**, `Vdr`,
`$`, `Monto`, `CH`, `Est.`, `H.Conc.`, `SGR/Lib/Deu/Alm`. Falta ver la respuesta
real para mapear los nombres.

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

Las dos que más importan para el bot: **`cpd-versubasta.r?ident=<n>`** (las puntas
de una subasta) y **`cpd-ofertas.r?estado=Activas`** (mis ofertas vivas, que es el
camino de reconciliación). Ninguna tiene controller JS bajo `/controllers/`, así
que probablemente sigan siendo HTML renderizado en el servidor — falta
confirmarlo con una captura.

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
- [ ] `cpd-versubasta.r?ident=<n>`: cómo se ven las puntas, el número de agente y
      la hora hh:mm:ss de cada una
- [ ] Si esa pantalla tiene su propio endpoint JSON o es HTML
- [ ] Flujo de modificar oferta y su preview
- [ ] `cpd-ofertas.r?estado=Activas` para la reconciliación
- [ ] Cómo se ve un rechazo
- [ ] Si la pantalla de subasta se auto-refresca y cada cuánto
- [ ] Si la plataforma admite dos sesiones simultáneas del mismo usuario
