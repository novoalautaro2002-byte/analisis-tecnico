# Finanzas

Seguimiento de finanzas personales a partir de resúmenes en PDF y la API de
Mercado Pago. Corre entero en tu máquina: la base es un archivo SQLite y no
sale de ahí.

## Por qué así

En Argentina no existe la vía "API del banco" para una persona física. El
marco de finanzas abiertas (Decreto 353/2025) sigue pre-operativo: el BCRA no
publicó los estándares técnicos. Los portales que sí existen, como Open
Galicia, son exclusivos para clientes empresa con alta en Office Banking.

Quedaban dos caminos: parsear los mails de aviso, o subir los resúmenes a
mano. Este proyecto hace lo segundo. Cuesta unos minutos por mes y a cambio
no hay OAuth de correo, no hay expresiones regulares que se rompan solas
cuando un banco cambia una plantilla, y no hay credenciales bancarias
guardadas en ningún lado.

## Instalación

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Uso

```bash
# 1. Tirás los PDF en una carpeta y los ingerís.
#    Reingerir el mismo archivo no duplica nada.
python -m finanzas ingerir entrada/

# Si el resumen tiene clave (suele ser el DNI), pasala. Podés repetir la
# opción y se prueban todas contra cada archivo.
python -m finanzas ingerir entrada/ --clave 12345678 --clave 4821

# 2. Una vez por mes, el saldo real de cada cuenta. Es el ancla que valida
#    que la ingesta esté capturando todo.
python -m finanzas saldo galicia_visa_4821 --monto=-34.070,62

# 3. Estado general: cuentas, conciliación, cuotas comprometidas.
python -m finanzas estado

# 4. Panel HTML.
python -m finanzas panel
python3 -m http.server -d panel
```

### Mercado Pago

Para tu propia cuenta no hace falta OAuth: creás una aplicación en el
[panel de desarrolladores](https://www.mercadopago.com.ar/developers/panel/app)
y usás el access token de producción directo.

```bash
export MP_ACCESS_TOKEN='APP_USR-...'

# Antes que nada, verificá si la API sirve para tus consumos.
python -m finanzas mp-probar
```

**Esto hay que verificarlo, no darlo por sentado.** La API de Mercado Pago
está diseñada alrededor del lado cobrador. Si `mp-probar` no muestra egresos,
la API está devolviendo solo cobros y Mercado Pago pasa a ser una fuente de
PDF como cualquier otra. Si muestra egresos, sincronizás:

```bash
python -m finanzas mp-sync
```

### Cuando un PDF no se reconoce

```bash
python -m finanzas diagnostico entrada/resumen.pdf
```

Muestra exactamente qué texto extrae el PDF, qué parser lo agarra y cuántas
líneas quedan sin interpretar. Es lo primero que hay que mirar: lo que ves
ahí es todo lo que el parser tiene disponible.

Si el PDF es un escaneo no hay texto que extraer y haría falta OCR, que este
sistema todavía no hace.

### Categorías

Las reglas son expresiones regulares sobre la descripción, deterministas y
editables. La primera que matchea gana.

```bash
python -m finanzas sin-categoria                              # qué no reconoce
python -m finanzas categorizar --regla '\bMI COMERCIO\b' comida
```

## Cómo está armado

```
PDFs        →  pdf.py      extrae texto y tablas, maneja claves
               parsers/    reconocen el emisor y arman Movimientos
Mercado Pago→  mercadopago.py   reporte settlement, sin OAuth
               ↓
               modelo.py   Movimiento canónico, id determinista
               normalizar.py    categorías, cuotas, conciliación
               db.py       SQLite
               panel.py    datos.json + HTML
```

Tres decisiones que conviene conocer antes de tocar el código:

**Los montos son enteros en centavos.** Nunca float. Un float binario no
representa 0,10 exacto y el error se acumula al sumar. Hay un test que lo
verifica.

**El id del movimiento es un hash de su contenido**, y no incluye el archivo
de origen. Reingerir el mismo PDF, o dos resúmenes con períodos que se
solapan, produce el mismo id y la inserción se descarta sola. La idempotencia
no depende de comparar campos en ningún lado.

**La moneda original siempre se preserva.** La conversión es una vista, nunca
un dato guardado. Cada moneda se concilia por separado: sumar pesos y dólares
en un mismo total no significa nada.

## La conciliación

Los resúmenes te dan flujos, no saldos. Una vez por mes cargás el saldo real
de cada cuenta; el sistema calcula el que debería haber según los movimientos
que capturó y te muestra la diferencia.

Si da cero, la ingesta está sana. Si no, hay movimientos que no estás
capturando y sabés exactamente cuánto falta, aunque no de qué se trate. Es el
control de calidad de todo el sistema.

Hace falta un saldo anterior para comparar: la primera carga fija el punto de
partida y recién la segunda concilia.

## El silencio es el modo de falla peligroso

Si la ingesta se rompe, el panel no se pone en rojo: se queda quieto, y eso es
indistinguible de un mes sin gastos. Por eso `estado` y el panel avisan cuando
pasan más de diez días sin un movimiento nuevo.

## Estado

Funcionando: extracción de PDF, parser genérico tabular, detección de cuenta
de Galicia, cuotas, categorías, conciliación multi-moneda, panel.

Pendiente: un parser propio de Galicia que entienda las secciones del resumen
y cuadre contra el total impreso. Hoy la lectura de líneas usa el genérico, que
puede perder impuestos y el detalle de cuotas. Necesita un resumen real de
muestra para escribirse.

## Tests

```bash
.venv/bin/python -m pytest tests/ -q
```

## Advertencia

Nada de esto es asesoramiento financiero ni contable. Los números que produce
son la lectura mecánica de documentos que vos subís, con los errores de
parseo que eso implica. **Cuadrá contra el total impreso del resumen antes de
confiar en cualquier cifra**, y usá la conciliación para detectar lo que falta.

La base nunca se versiona: `.gitignore` excluye `*.db`, `entrada/` y
`datos.json`. Este repositorio es público — que no entre un dato tuyo.
