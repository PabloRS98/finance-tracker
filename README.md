# finance-tracker

Tracker de patrimonio e ingresos/gastos personales. Local-first, mono-usuario y
sin APIs de pago: los precios salen de fuentes públicas gratuitas y todo vive en
un único SQLite dentro de un volumen Docker.

**Stack**: FastAPI · SQLAlchemy 2.0 · SQLite · Jinja2 · Chart.js (vendorizado, sin build step).

## Qué hace

- **Patrimonio**: activos de cuatro tipos (cuenta bancaria, acción/ETF/fondo,
  cripto, inmueble/otro) valorados en una moneda base común, con evolución
  histórica diaria e intradía y un mapa de la cartera (superficie = peso,
  color = variación del día).
- **Inversión**: posiciones derivadas de operaciones de compra/venta, con coste
  medio, P&L realizado y no realizado, y descomposición del efecto divisa.
- **Rendimiento**: TWR (mide la estrategia), XIRR (mide tu dinero: pondera cada
  aportación por el tiempo que lleva trabajando), CAGR, y rentabilidad por año
  natural comparada con los índices que elijas.
- **Ingresos y gastos**: transacciones con categorías, presupuestos por categoría,
  reglas recurrentes con catch-up, e importación desde CSV del banco.
- **Importadores** de operaciones: Trade Republic, Revolut (CSV y PDF), OKX y un
  formato genérico, con deduplicación por huella.
- **Análisis**: allocations por divisa/región/sector, comisiones acumuladas y un
  X-Ray de riesgos (concentración, exposición a divisa, precios estancados).
- **Rebalanceo**: peso objetivo por activo, desviación en dinero y reparto de una
  aportación entre lo que va por debajo. No propone ventas: rebalancea comprando.
- **Alertas de precio** por Telegram: precio objetivo por encima o por debajo, y
  caída diaria mayor de un porcentaje. Avisan una vez y se rearman al dejar de
  cumplirse.
- **Higiene de cartera**: detección de activos duplicados (el mismo valor
  comprado en dos brókers acaba como dos activos) con fusión guiada, y desglose
  de la posición por cuenta. Lo vendido entero se aparta a "posiciones cerradas",
  fuera de los totales pero con su historial y su P&L realizado intactos: un
  traspaso de bróker deja las dos etapas y ninguna es un duplicado de la otra.
- **Watchlist**: seguir el precio de valores que aún no tienes, sin que entren
  en el patrimonio.
- **Entrada por voz** en español, en el navegador y por Telegram, con confirmación
  previa antes de aplicar nada.

## Puesta en marcha

### Docker (recomendado)

```bash
cp .env.example .env       # opcional: sin .env arranca con los valores por defecto
docker compose up -d --build
```

La app queda en <http://localhost:8001> (cámbialo con `FINANCE_PORT`). El volumen
`finance-tracker_data` guarda la base de datos y los backups diarios: es lo único
que hay que respaldar.

El contenedor arranca como root solo el tiempo justo de que el entrypoint ajuste
la propiedad de `/data` —un volumen que ya existe conserva la suya, y el `chown`
del build no le llega— y acto seguido baja a un usuario sin privilegios. La app
nunca corre como root.

### Local

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
echo "DB_PATH=./finance.db" >> .env        # fuera de Docker no existe /data
uvicorn app.main:app --reload
```

## Configuración

Todas las opciones son variables de entorno (o líneas de un `.env`), y todas
tienen un valor por defecto razonable. La lista completa y comentada está en
[`.env.example`](.env.example); los campos viven en `app/config.py`.

Lo que conviene revisar antes de exponer la app:

| Variable | Por qué importa |
|---|---|
| `ENABLE_AUTH` | Desactivada por defecto. **Actívala** si la app sale de localhost, aunque sea por VPN. |
| `AUTH_PASSWORD` | El default es `changeme`. |
| `DB_PATH` | Debe apuntar a un volumen persistente. |
| `BASE_CURRENCY` | Moneda de todos los totales. Cambiarla con datos ya cargados no reconvierte lo existente. |

### CSRF

Toda petición que modifique estado exige un token (cookie `csrftoken` +
campo `_csrf` en los formularios, o cabecera `X-CSRF-Token` en las llamadas
fetch). Es automático desde el navegador; solo importa si atacas la API a mano:

```bash
# El token sale de cualquier página, y la cookie tiene que viajar de vuelta
curl -c cookies.txt -s http://localhost:8000/ | grep csrf-token
curl -b cookies.txt -X POST http://localhost:8000/api/refresh-prices \
     -H "X-CSRF-Token: <el token>"
```

Si sirves la app detrás de HTTPS, pon `secure=True` en la cookie
(`app/csrf.py`). Está en `False` porque en LAN/VPN por HTTP el navegador no
guardaría una cookie `Secure`.

### Bot de Telegram (opcional)

Crea un bot con [@BotFather](https://t.me/BotFather), pon el token en
`TELEGRAM_BOT_TOKEN` y arranca la app. Escríbele por Telegram: te responderá con
tu `chat_id` para que lo pongas en `TELEGRAM_CHAT_ID` y reinicies. A partir de
ahí acepta mensajes de texto y notas de voz («compré 0,5 bitcoin a 54.000»,
«gasté 25 euros en comida») y manda un resumen diario de la cartera.

Funciona por long polling: no expone nada a internet ni necesita webhook.

## Desarrollo

### Datos reales

**Este repositorio es público y la app guarda un patrimonio entero.** El
`.gitignore` cubre lo evidente —la base de datos, el `.env`, los extractos de
banco en CSV/PDF—, pero eso solo protege los ficheros. Lo que se escapa es la
prosa: un total de patrimonio en el cuerpo de un PR, la lista de tickers de una
cartera en una entrada del registro de cambios, el nombre del bróker en un
mensaje de commit.

No van al repositorio, en ninguna forma:

- Importes reales (patrimonio, valor de una posición, P&L).
- Tickers, ISINs o nombres de activos que alguien tenga de verdad, ni el recuento
  de activos u operaciones de una cartera concreta.
- Brókers y cuentas de una persona.

Para ejemplos, cifras inventadas. Los tickers que aparecen en la interfaz y en
los tests (`AAPL`, `VWCE.DE`, `bitcoin`) son marcadores de posición genéricos, no
la cartera de nadie.

Y ojo con dónde queda: un cuerpo de PR se puede editar después, pero un **mensaje
de commit** ya empujado solo se quita reescribiendo la historia.

### Tests

```bash
pip install pytest
pytest -q
```

Los tests no tocan la red ni el volumen `/data`: las APIs externas van
parcheadas y la base de datos es SQLite en memoria. Hay dos niveles:

- `tests/test_*.py` (servicios): cálculo de posiciones, histórico, recurrentes,
  parser de voz, importadores, clasificación.
- `tests/test_endpoints.py`: HTTP contra la app real — que cada página responda,
  que la autenticación cubra todas las rutas y que ningún dato guardado pueda
  romper el HTML.

### Migraciones

El esquema se versiona con Alembic:

```bash
alembic upgrade head                          # aplicar las pendientes
alembic revision --autogenerate -m "..."      # crear una nueva tras tocar models.py
```

La app aplica `upgrade head` sola al arrancar, así que en uso normal no hay que
ejecutar nada a mano.

> **Bases anteriores a Alembic** (creadas con el `ensure_columns` de las primeras
> versiones): no hay que hacer nada. `init_db()` las detecta —no tienen tabla
> `alembic_version`—, les completa las columnas que les falten, las marca en la
> revisión inicial y sigue con el resto de migraciones.
>
> No ejecutes `alembic stamp head` sobre una base así: la daría por actualizada
> y se saltaría migraciones que sí necesita, dejando la app con errores de
> "no such column" en todas las páginas.

## Datos y backups

- Un job diario copia la BD a `<dir de DB_PATH>/backups/finance-AAAAMMDD.db`
  usando la API de backup de SQLite (consistente aunque haya escrituras) y rota
  las copias antiguas según `BACKUP_KEEP`.
- El dashboard tiene un botón para descargar un backup fresco.
- Las transacciones se pueden exportar a CSV desde `/transacciones`.

## Fuentes de datos

Todas públicas y sin API key:

| Fuente | Para qué |
|---|---|
| [Frankfurter](https://frankfurter.dev) (BCE) | Tipos de cambio, actuales e históricos |
| Yahoo Finance (endpoint de gráficas) | Precios y cierres de acciones/ETFs/fondos y benchmarks |
| [CoinGecko](https://coingecko.com) | Precios e histórico de criptomonedas |

Si una fuente falla, la app **no** inventa valores: los activos que no puede
convertir quedan fuera de los totales y se avisa en pantalla, y los snapshots
históricos incompletos no se guardan.

## Licencia

MIT, en [`LICENSE`](LICENSE).

El repositorio incluye además Chart.js y los iconos de Lucide (ambos MIT) y la
fuente Inter (SIL Open Font License 1.1), vendorizados para que la app funcione
sin build step y sin depender de una CDN. Cada uno conserva su licencia: el
detalle está en [`NOTICE`](NOTICE).
