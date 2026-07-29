# Análisis Técnico

Herramienta de análisis técnico sobre acciones y ADRs que cotizan en Estados Unidos,
medidos en dólares. Un único archivo HTML autocontenido, sin build ni dependencias
que instalar.

**En vivo:** https://novoalautaro2002-byte.github.io/analisis-tecnico/

## Qué hace

**Análisis.** Gráfico de velas con medias móviles, Bollinger, volumen y panel
conmutable de RSI, MACD, estocástico o ADX. Detecta solo la estructura del precio:
pivotes, zonas de soporte y resistencia agrupadas por toques, y las directrices de
tendencia. Avisa el caso concreto de "tocó el piso de la tendencia de N meses", su
ruptura, y cuántos toques la sostienen. Un tablero de 16 señales ponderadas produce
un score de −100 a +100, con la lectura de cada señal a la vista, y un plan operativo
con entrada, stop por ATR o soporte, objetivos y ratio riesgo-beneficio.

**Scanner.** Barre el universo y filtra por volumen anormal, rupturas de máximos y
mínimos de 20 ruedas, máximos de 52 semanas, gaps de apertura, alineación de medias,
fuerza relativa contra el índice y contra el sector, y compresión de bandas.

**Momentum / Reversión.** Dos rankings: papeles que ya se mueven y podrían seguir, o
papeles sobreextendidos que podrían rebotar, con el detalle de por qué entró cada uno.

## Tres gráficos

- **Motor** — gráfico propio en canvas, con todo lo que detecta el motor dibujado encima.
- **Lightweight** — velas de [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts)
  (Apache 2.0) con las directrices y los niveles del motor dibujados sobre ellas.
- **TradingView** — el widget Advanced Chart, con los indicadores y las herramientas
  de dibujo de TradingView.

## Por qué en dólares

El análisis técnico sobre series en pesos está contaminado por la devaluación: los
máximos "nuevos" reflejan la moneda y no la empresa, el RSI queda sesgado hacia arriba
de forma permanente, la SMA200 compara contra pesos de hace diez meses y las
directrices dejan de poder trazarse con una recta. Por eso todo se calcula sobre el
precio en dólares del papel que cotiza afuera. El CEDEAR se usa solo como criterio de
armado del universo: entran únicamente papeles que se pueden operar localmente.

## Datos

Yahoo Finance, con demora y por fuentes públicas. Sin API key. El cálculo de
indicadores es íntegramente del lado del navegador.

## Advertencia

Las señales y "recomendaciones" son la salida mecánica de un modelo de indicadores
sobre precios históricos. **No constituyen asesoramiento de inversión** ni
recomendación de compra o venta de valores negociables. Los pesos de las señales
fueron elegidos a criterio y todavía no están validados estadísticamente: que el score
dé +57 significa que varios indicadores apuntan en la misma dirección, no que el papel
vaya a subir. El desempeño pasado no garantiza resultados futuros. Verificá siempre
contra la punta real del mercado antes de operar.
