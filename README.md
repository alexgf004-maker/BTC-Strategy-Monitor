# BTCUSDT Strategy Monitor

Monitor **paper/shadow** para `BTCUSDT_Strategy_Suite_v1.0_FINAL`.

Consulta únicamente velas públicas de Binance USD-M Futures, reconstruye los motores A/B/C/D y registra entradas y salidas simuladas. No contiene integración de órdenes, claves de exchange, retiros ni ejecución real.

## Funcionamiento

- GitHub Actions ejecuta el monitor a los minutos `07, 22, 37 y 52` de cada hora (UTC), después del cierre esperado de cada vela de 15 minutos.
- En un VPS, `python -m monitor.service` mantiene un proceso 24/7 y evalúa 35 segundos después de cada cierre de vela de 15 minutos.
- El motor intenta primero la API de Binance Futures. Si Binance bloquea la región del runner, usa exclusivamente el archivo oficial Binance Vision: conserva el mercado y las columnas exactas, pero los datos llegan con retraso hasta el día siguiente.
- Las señales se calculan solo con velas cerradas y las entradas usan la apertura de la vela siguiente.
- El registro forward-OOS comienza el `2026-09-01 00:00 UTC`.
- El gestor de riesgo aplica los límites congelados: 2% global y 1.25% compartido entre A+C.
- Los eventos nuevos crean una incidencia de GitHub para facilitar las notificaciones.
- Si se configuran los secretos `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`, cada nueva **ENTRADA** y **SALIDA** paper también se envía como mensaje de Telegram.
- Una ejecución manual está disponible desde la pestaña **Actions**.

## Archivos importantes

- `strategy_suite_v1_FINAL.json`: especificación congelada e inmutable.
- `monitor/market.py`, `monitor/indicators.py` y `monitor/strategies.py`: datos, indicadores y señales.
- `monitor/portfolio.py`: simulación paper y gestor de riesgo.
- `monitor/notify.py`: alerta de Telegram para nuevas entradas y salidas (best-effort, nunca detiene el monitor).
- `monitor/runner.py`: ejecución idempotente y escritura de resultados.
- `monitor/service.py`: proceso persistente, planificador y API de lectura protegida.
- `deploy/install.sh`: instalación reproducible como servicio `systemd` en Ubuntu.
- `runtime/forward_events.csv`: registro paper acumulado y reproducible.
- `runtime/status.json`: último estado confirmado.

## Alertas de Telegram

1. Crea un bot con [@BotFather](https://t.me/BotFather) y copia el token (`TELEGRAM_BOT_TOKEN`).
2. Escríbele algo al bot y obtén tu `chat_id` (por ejemplo con `https://api.telegram.org/bot<token>/getUpdates`).
3. En el repo: **Settings → Secrets and variables → Actions → New repository secret** y agrega `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID`.
4. Sin esos secretos el monitor sigue funcionando igual, simplemente no envía el mensaje de Telegram.

## Seguridad

El código falla de forma cerrada si la fuente de datos no responde o si cambia el SHA-256 de la especificación. No usa `BINANCE_API_KEY`, `BINANCE_API_SECRET` ni ningún endpoint de órdenes.

El estado distingue claramente `binance_futures_realtime` de `binance_vision_official_delayed`; nunca sustituye silenciosamente datos spot o de otro exchange.

La ruta `/healthz` solo publica salud básica. `/status.json` y `/events.json` exigen `Authorization: Bearer <MONITOR_READ_TOKEN>`.

## VPS Ubuntu

Como `root`, ejecuta `bash deploy/install.sh` desde una copia del repositorio. El instalador crea un usuario sin privilegios, guarda el estado en `/var/lib/btc-strategy-monitor`, genera un token privado y registra `btc-strategy-monitor.service` para arrancar automáticamente con el servidor.

El servicio escucha únicamente en `127.0.0.1:8080` hasta que se configure un proxy HTTPS. Esto evita exponer señales paper directamente a internet.

## Estado de validación

El ZIP recibido incluye la especificación, el resumen congelado y auditorías, pero no incluye el CSV canónico de 15 minutos ni los tradebooks completos A/B/C/D mencionados en el manifiesto. Por eso esta implementación se identifica como **reconstrucción paper desde la especificación congelada**. No se afirma reproducción histórica completa hasta aportar esos archivos y ejecutar la auditoría correspondiente.

> Herramienta educativa y de investigación. Los resultados simulados no garantizan resultados futuros ni constituyen asesoría financiera.
