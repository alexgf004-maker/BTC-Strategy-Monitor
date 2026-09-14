# BTCUSDT Strategy Monitor

Monitor **paper/shadow** para `BTCUSDT_Strategy_Suite_v1.0_FINAL`.

Consulta únicamente velas públicas de Binance USD-M Futures, reconstruye los motores A/B/C/D y registra entradas y salidas simuladas. No contiene integración de órdenes, claves de exchange, retiros ni ejecución real.

## Funcionamiento

- GitHub Actions ejecuta el monitor a los minutos `07, 22, 37 y 52` de cada hora (UTC), después del cierre esperado de cada vela de 15 minutos.
- El motor intenta primero la API de Binance Futures. Si Binance bloquea la región del runner, usa exclusivamente el archivo oficial Binance Vision: conserva el mercado y las columnas exactas, pero los datos llegan con retraso hasta el día siguiente.
- Las señales se calculan solo con velas cerradas y las entradas usan la apertura de la vela siguiente.
- El registro forward-OOS comienza el `2026-09-01 00:00 UTC`.
- El gestor de riesgo aplica los límites congelados: 2% global y 1.25% compartido entre A+C.
- Los eventos nuevos crean una incidencia de GitHub para facilitar las notificaciones.
- Una ejecución manual está disponible desde la pestaña **Actions**.

## Archivos importantes

- `strategy_suite_v1_FINAL.json`: especificación congelada e inmutable.
- `monitor/market.py`, `monitor/indicators.py` y `monitor/strategies.py`: datos, indicadores y señales.
- `monitor/portfolio.py`: simulación paper y gestor de riesgo.
- `monitor/runner.py`: ejecución idempotente y escritura de resultados.
- `runtime/forward_events.csv`: registro paper acumulado y reproducible.
- `runtime/status.json`: último estado confirmado.

## Seguridad

El código falla de forma cerrada si la fuente de datos no responde o si cambia el SHA-256 de la especificación. No usa `BINANCE_API_KEY`, `BINANCE_API_SECRET` ni ningún endpoint de órdenes.

El estado distingue claramente `binance_futures_realtime` de `binance_vision_official_delayed`; nunca sustituye silenciosamente datos spot o de otro exchange.

## Estado de validación

El ZIP recibido incluye la especificación, el resumen congelado y auditorías, pero no incluye el CSV canónico de 15 minutos ni los tradebooks completos A/B/C/D mencionados en el manifiesto. Por eso esta implementación se identifica como **reconstrucción paper desde la especificación congelada**. No se afirma reproducción histórica completa hasta aportar esos archivos y ejecutar la auditoría correspondiente.

> Herramienta educativa y de investigación. Los resultados simulados no garantizan resultados futuros ni constituyen asesoría financiera.
