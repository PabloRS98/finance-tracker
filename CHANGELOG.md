# Registro de cambios

Orden inverso: lo más reciente arriba. Cada entrada enlaza su PR y explica el
**porqué**, que es lo que no se deduce leyendo el diff.

---

## Sin publicar — pendientes de merge

Los cuatro van encadenados (`#8 → #9 → #10 → #11`) para que las revisiones de
Alembic queden en una sola línea. Hay que mergearlos en ese orden; GitHub
reapunta cada uno a `main` solo.

### #11 · Fusionar activos duplicados y desglosar la posición por cuenta

El mismo valor comprado en dos sitios acaba como dos activos: Trade Republic
exporta por ISIN, Revolut por ticker. La cartera enseñaba dos líneas de lo mismo
y los pesos del X-Ray salían partidos.

- Detección por ISIN → ticker → nombre normalizado, sin repetir activos entre
  grupos: si uno cayera en dos, fusionarlo desde el primero dejaría el segundo
  apuntando a un activo ya borrado.
- La fusión cuelga todas las operaciones de un solo activo. No recalcula nada:
  la posición y el coste medio se derivan de las operaciones. El destino hereda
  el ISIN/ticker que le faltara (lo que evita que el duplicado reaparezca al
  importar) y pierde la `quantity` manual de la v2, que si no se sumaría a la
  posición real.
- **No fusiona divisas distintas.** Las operaciones no guardan divisa, heredan la
  del activo, así que juntarlas mezclaría dos escalas de precio en el mismo coste
  medio. Pasa cuando el mismo valor cotiza en dos plazas: la europea en euros y
  la estadounidense en dólares.
- Desglose de la posición por cuenta en la ficha: es lo que se pierde al fusionar
  si no se enseña.

> **Trampa evitada**: mover las operaciones con `op.asset_id = destino.id` deja
> intacta la colección del origen y, como `Asset.operations` va con
> `cascade="all, delete-orphan"`, al borrar el activo absorbido **se llevaba por
> delante las operaciones**. Se reasigna `op.asset`, que actualiza ambas
> colecciones. Un `remove()` previo tampoco vale: marca la operación como
> huérfana y la borra igual.

### #10 · Mapa de la cartera en el dashboard

Treemap de la parte invertida: superficie = peso, color = variación del día.

Implementado **sin** `chartjs-chart-treemap` (que era lo que apuntaba la
propuesta): algoritmo squarified propio en ~60 líneas, para no meter un bundle de
terceros sin auditar. Los rectángulos son divs, así que heredan los estilos y el
modo privacidad sin trabajo extra. El color satura a ±3%: más allá el ojo no
distingue.

### #9 · Lista de valores en seguimiento (watchlist)

Seguir el precio de algo que aún no tienes obligaba a darlo de alta como activo,
y entonces entraba en el patrimonio con cantidad cero.

Va en **tabla aparte, no como `Asset` con bandera**: los activos entran en el
patrimonio, en las allocations, en el X-Ray y en la reconstrucción del histórico,
y una bandera obligaría a acordarse de excluirlos en cada una de esas consultas.
Un olvido inflaría el patrimonio con dinero que no tienes.

### #8 · Índices de referencia configurables

MSCI World y S&P 500 estaban fijos en un diccionario del código. Pasan a tabla,
gestionable desde Análisis. La migración siembra los dos de siempre con las
mismas claves y símbolos, así que el histórico ya descargado se sigue usando.

El símbolo se valida contra Yahoo antes de guardarlo: uno que no exista se
quedaría sin serie para siempre y saldría como columna vacía sin explicar por qué.

---

## Publicado

### #28 · Posiciones cerradas fuera de la cartera viva

Un activo vendido entero seguía en medio de la lista valiendo 0. La app no
distinguía entre lo que tienes y lo que tuviste.

- `posicion_cerrada()` en `services/portfolio.py`: hubo operaciones y ya no queda
  cantidad. No es lo mismo que "sin posición" —un activo recién dado de alta
  tampoco tiene cantidad, pero no hay historial detrás que apartar—, y la
  comparación va con tolerancia porque vender en varios trozos deja la posición
  en 1e-16, no en 0 clavado.
- Salen a una sección plegada al final, con su P&L realizado y la fecha de la
  última operación. **Ninguna cifra cambia**: valían 0, así que no aportaban a
  ningún subtotal. Se conservan enteras porque son las que sostienen la
  rentabilidad histórica.
- `/activos/duplicados` deja de mirarlas. Un traspaso de bróker —vender en uno y
  comprar en otro el mismo día— dejaba dos activos que el detector marcaba como
  repetidos, y el aviso era falso: no son la misma posición, sino dos etapas de
  la misma historia. Fusionarlos habría juntado dos escalas de precio bajo un
  solo coste medio.

### #27 · Documentar que un traspaso de bróker no es un duplicado

`PROPUESTAS-v4.md` planteaba "elegir con qué divisa quedarse" para consolidar dos
activos que el detector marcaba como repetidos. Era una lectura equivocada del
dato: invitaba a fusionar dos posiciones distintas o a borrar operaciones reales.
Se añade también la sección "Datos reales" del README — el repositorio es
público y el `.gitignore` protege los ficheros, no la prosa.

### #25 · Rebalanceo

Peso objetivo por activo, desviación en porcentaje y en dinero, y reparto de una
aportación entre lo que va corto.

No propone ventas a propósito: vender para rebalancear cristaliza plusvalías y su
peaje fiscal. Con aportación, el objetivo se mide sobre la cartera **futura**, no
la actual, porque el propio dinero nuevo mueve los porcentajes. El reparto va
solo a los que van por debajo: meterle más al que ya sobrepasa su peso agravaría
la desviación.

### #24 · Estados vacíos con acción y escala en tokens

Los estados vacíos decían qué hacer en prosa ("Registra la primera arriba"), lo
que obliga a buscar ese "arriba". Ahora el sitio al que ir es un botón.

La jerarquía tipográfica **no** se rediseñó: la propuesta decía que "casi todo
pesa igual" y al comprobarlo ya no era cierto. Se recogió la escala existente en
tokens en vez de tocar decenas de reglas para dejarlo igual.

### #23 · Operaciones: alta plegada, chips y separadores de mes

A esa página se entra a consultar, no a registrar, pero el formulario ocupaba la
primera pantalla. Los filtros de tipo y cuenta pasan a chips combinables; el de
activo se queda en select porque una cartera tiene decenas. Los meses van en
tabla propia y no por `strftime("%B")`: el contenedor corre en locale C.

### #22 · Avatares de color por activo

Círculo con dos iniciales y color estable por ticker. El color sale de `crc32` y
no de `hash()`, que lleva sal por proceso y habría cambiado los colores en cada
reinicio. Las iniciales saltan el relleno ("Corporation", "Inc.", "Acc"), o
`MUESTRA Corporation` habría dado NC y `Alfabeto (A)` habría dado `A(`.

### #21 · Marcas de compra/venta sobre la curva de precio

Cada operación es un punto sobre la propia curva: verde compra, rojo venta, ámbar
si ese día hubo de las dos. Antes había que cruzar mentalmente la gráfica con la
lista de abajo para saber si compraste caro.

### #20 · Alertas de precio por Telegram

Tres condiciones por activo. Lo delicado no es la condición sino el rearme: un
activo que cruza a la baja sigue por debajo durante horas y avisaría en cada
refresco. Cada alerta recuerda cuándo saltó y no repite hasta que el precio deja
de cumplirla.

### #15–#19 · Móvil

FAB de gasto rápido y voz (apuntar un gasto pasa de dos navegaciones a un toque),
barra inferior de 4 + "Más", historiales apilados en tarjetas sin scroll
horizontal, tirar para actualizar, e icono maskable con standalone en iOS.

El **swipe** para editar/borrar se descartó: se pedía porque las filas iban
apretadas, y con las tarjetas y los objetivos de 44px ya están visibles y a un
toque.

### #7 · XIRR, rendimiento por año y serie de aportaciones

Tres métricas que salen de datos que ya se calculaban para el TWR.

- **Aportado**: tercera serie en la gráfica de evolución. El hueco contra
  "Invertido" es la ganancia.
- **XIRR**: el TWR mide la estrategia y no cambia según cuándo entraras; el XIRR
  pondera cada aportación por el tiempo que lleva trabajando. Resuelto por
  bisección, no Newton-Raphson: algo más lento pero no diverge con flujos
  irregulares, que es lo que tiene una cartera real.
- **Rendimiento por año natural** contra los índices. Cada año arranca en el
  *cierre del anterior*, no en su primer día: si no, el salto de fin de diciembre
  a primeros de enero no se lo apunta ningún año.

> **Fallo encontrado al construirlo**: los benchmarks no tenían backfill. Una vez
> guardado su primer cierre solo avanzaban hacia delante, así que la comparación
> contra el índice salía vacía para todo el periodo anterior a la primera
> descarga: sobre una base con años de historial, el índice solo tenía los
> cierres de los últimos meses. Ahora se piden desde la primera operación, así
> que la serie cubre todo el periodo comparable.

### #6 · Avisos de mercado anclados a la zona de su plaza

Las horas estaban escritas en UTC pero el scheduler corre en `settings.timezone`.
Con `TIMEZONE=Europe/Madrid` los cuatro avisos saltaban **dos horas antes**: el
"cierre de Europa" llegaba a las 15:30, con la bolsa aún abierta.

Cada sesión declara ahora la zona de su mercado, así que además se ajusta sola al
horario de verano (EE. UU. y Europa no lo cambian el mismo fin de semana).

Los cinco resúmenes del día llamaban al mismo `send_daily_summary()` sin
parámetros y llegaban idénticos; ahora cada uno lleva su título.

### #5 · Botón de refrescar precios y mezcla de divisas al importar

- `base.html` llamaba a `showToast`, que no es global (vive en el IIFE de
  `app.js`, expuesto como `window.appToast`). El POST salía bien, pero el
  manejador reventaba con `ReferenceError` **antes** del `location.reload()`: ni
  aviso ni recarga, y parecía que el botón estaba muerto.
- `_match_asset` casaba por ISIN/ticker/nombre pero **no miraba la divisa**. Una
  fila en euros colgada de un activo en dólares no se convierte: se reinterpreta
  el precio y el coste medio mezcla dos divisas sin dejar rastro. Se rechaza y se
  explica, en el preview y otra vez al confirmar.

### #4 · Propiedad de `/data` al arrancar, no al construir

El paso a usuario sin privilegios (en #1) hizo `chown` de `/data` en el
Dockerfile, pero eso solo afecta a la imagen: al montar encima un volumen **que
ya existe**, sus ficheros conservan la propiedad que tuvieran. El despliegue real
entró en bucle de reinicio con `attempt to write a readonly database`.

Con un volumen nuevo no se reproduce, porque ahí Docker sí copia la propiedad
desde la imagen: por eso pasó el CI y solo se vio al tocar el despliegue.

Se mueve a un entrypoint que hace el `chown` y baja de privilegios con `setpriv`
(ya viene en la imagen base). Es automático a propósito: un `chown` manual
documentado significa que cualquier despliegue que venga de una versión anterior
se cae al actualizar.

`.gitattributes` fuerza LF en los `.sh`: con `core.autocrlf=true` un clon en
Windows dejaría el shebang como `#!/bin/sh\r` y el contenedor no arrancaría.

### #3 · Despliegue independiente de la suite

La app arrancaba como un servicio de `home-apps-suite`, un compose que vivía
fuera del repo y que **ya no existe**: el contenedor colgaba de un proyecto
fantasma. El `docker-compose.yml` que había en la carpeta era un recorte de aquel
fichero, sin versionar, y conservaba `build: ./finance-tracker` — una ruta
relativa al directorio padre que desde la raíz del repo no construye.

- Compose propio y versionado, con el healthcheck y los límites de log que antes
  definía la suite por fuera.
- Volumen con nombre explícito: sin fijarlo, Compose lo prefija con el nombre del
  proyecto y renombrar la carpeta apuntaría a un volumen vacío, con la base de
  datos aparentemente desaparecida.
- `.env.example` solo de esta app (el anterior era el de las tres) y sin el
  prefijo `FINANCE_`, que solo existía para repartir un `.env` compartido.
- El `.gitignore` ocultaba la propia plantilla con `.env.*`, así que un clon nuevo
  no tenía de dónde partir.

> **Migración de datos**: al pasar de `home-apps-suite_finance_data` a
> `finance-tracker_data` hubo que copiar **el WAL**, más reciente que el `.db`.
> Copiar solo el fichero principal habría perdido 12.264 filas de `price_history`
> y un snapshot.

### #2 · `assets.avg_cost_override` en bases ya desplegadas

La columna se añadió al modelo pero nunca se declaró en la migración ligera.
Como `create_all()` no altera tablas existentes, cualquier base creada antes se
quedaba sin ella: la app arrancaba con normalidad (el fallo del snapshot inicial
queda capturado) y después devolvía **500 en todas las páginas**.

No se veía en los tests porque una base recién creada sí trae la columna: solo
aparecía sobre datos ya desplegados. Y el healthcheck del contenedor solo prueba
`/salud`, que no toca la base, así que Docker reportaba el contenedor **healthy**
mientras la app no servía ni una página.

### #1 · Auditoría: XSS, hueco de auth, divisa de voz y FX silencioso

- El filtro `tojson` estaba sobrescrito por un `json.dumps` a secas, que anulaba
  el escapado de Jinja: cualquier dato guardado podía cerrar el `<script>` del
  dashboard.
- `POST /api/refresh-prices` vivía fuera de los routers y era el único endpoint
  sin autenticación.
- El parser de voz detectaba la divisa pero los llamadores la ignoraban: «gasté
  30 dólares» se apuntaba como 30 €.
- `get_exchange_rate` devolvía `1.0` cuando la API fallaba sin caché, valorando
  1 USD = 1 EUR, y el snapshot diario llegaba a persistir ese número.
- Alembic sustituye a `ensure_columns`; importes del libro a `Numeric(12,2)`
  (con `Float`, diez gastos de 0,10 € sumaban 0.9999999999999999); índices de
  fecha y `selectinload` en las consultas de cartera.
- CI, README, `.env.example` y `tests/test_endpoints.py`.

> Este PR llegó **con el CI en rojo** (`pytest` a secas no mete la raíz del repo
> en `sys.path`; en local no se veía porque `python -m pytest` sí lo hace) y con
> una migración que **no arreglaba las bases ya desplegadas**: sin
> `alembic_version` intentaba `create_table` sobre tablas existentes, y el
> `alembic stamp` manual que documentaba las marcaba como al día sin añadirles la
> columna que les faltaba. Ambas cosas se corrigieron antes de mergear.
