# Registro de cambios

Orden inverso: lo más reciente arriba. Cada entrada explica el **porqué**, que es
lo que no se deduce leyendo el diff.

> Las entradas no llevan número de pull request. El repositorio se rehízo desde
> cero, con un solo commit inicial, porque el historial anterior y sus pull
> requests contenían datos reales de una cartera; los números de aquellos PR ya
> no resuelven y citarlos aquí solo llevaría a enlaces rotos. Lo que sí se
> conserva es el motivo de cada cambio, que es para lo que sirve este documento.

---

## Sin publicar

Cierre de la auditoría técnica del 6 de agosto de 2026. Cada entrada lleva el ID
del hallazgo que cierra, para que dentro de seis meses se pueda ir del código al
motivo sin adivinarlo.

### [FT-A4] Límite de tamaño en las subidas de ficheros

Ni la importación de operaciones ni la de movimientos del banco acotaban el
tamaño. `archivo.read()` sin argumento carga el fichero entero en memoria y el
`.decode()` posterior hace una segunda copia: arrastrar por error un fichero de
1 GB consume 2-3 GB y el kernel mata el proceso. En un NAS con 4 GB eso tumba
también lo que haya al lado, sin ningún mensaje útil.

Ahora se lee por trozos con tope: 20 MB para CSV y 10 MB para PDF. Son dos
números distintos a propósito — los bytes del PDF van a `fitz.open()`, que con
un fichero malformado o con bombas de descompresión consume mucha más memoria
que el original.

### [FT-A7] La descarga de backup usa un temporal único y lo borra

Escribía siempre en `/tmp/finance-backup.db`. Tres problemas: `/tmp` no existe
fuera de Linux; dos descargas a la vez se pisaban el fichero, y
`sqlite3.backup()` sobre uno que se está leyendo produce una copia corrupta sin
ningún error —el peor fallo posible en un backup—; y la copia, que lleva el
patrimonio, las operaciones y los gastos, se quedaba en disco indefinidamente.

El `ROADMAP` asumía esto como deuda, pero solo había pesado la colisión, que en
una app mono-usuario efectivamente no ocurre. La copia olvidada no estaba en esa
cuenta. Deuda retirada.

### [FT-A6] `BACKUP_KEEP=0` ya no conserva los backups para siempre

`existing[:-0]` es `existing[:0]`, o sea la lista vacía. Con `BACKUP_KEEP=0` no
se borraba **ninguna** copia: quien lo configuraba esperando "no conservar
backups" obtenía "conservarlos todos", a razón de una copia completa de la base
al día. Cuando SQLite se queda sin espacio en un volumen con WAL, las lecturas
siguen funcionando y las escrituras no, que es la forma más incómoda de
enterarse.

Con 0 se conserva ahora el que se acaba de crear, y nada más.

### [FT-A2] Las alertas de precio llegan aunque el activo tenga un `&` en el nombre

Los avisos se envían con `parse_mode: "HTML"` y los nombres de activo se
autorrellenan desde Yahoo, así que llegan tal cual del mercado. Un nombre con
`&` —hay de sobra— hacía que Telegram devolviera `400 can't parse entities`.

Y lo que lo convertía en pérdida silenciosa: el error se tragaba, pero la
alerta ya se había marcado como disparada **antes** de saber si el envío
funcionó. Así que no llegaba, y tampoco volvía a intentarse hasta que la
condición se rearmara. Ponías una alerta, cruzaba el precio, y no te enterabas.

Dos cambios: el nombre va escapado en las tres ramas del mensaje, y el marcado
pasa a hacerse solo cuando el envío devuelve algo. Sin bot configurado no se
marca nada, porque no se ha avisado a nadie.

### [FT-A3] Borrar un activo ya no rompe las alertas de todos los demás

`Asset.operations` tenía cascada; `Alerta` y `PesoObjetivo` no. Y SQLite no
aplica claves foráneas salvo que se active el PRAGMA, así que borrar un activo
dejaba esas filas apuntando a un id inexistente.

Lo grave venía después: la comprobación de alertas leía `alerta.asset.current_price`
sobre un `asset` que ya era `None`, el `AttributeError` quedaba enterrado en el
`try/except` del scheduler, y **las alertas dejaban de comprobarse para todos
los activos, indefinidamente**, con una línea de log como único rastro. Bastaba
borrar una vez un activo que tuviera alerta.

Arreglado en las tres capas, porque cada una tapa un hueco distinto: cascada en
la relación (lo que usa la app), `ON DELETE CASCADE` en el esquema (lo que
protege ante un borrado por SQL) y una defensa en la propia comprobación para
que una fila huérfana llegada por cualquier otra vía no tumbe el ciclo.

La migración limpia además los huérfanos que ya hubiera.

### [FT-A8] Cabeceras de seguridad HTTP

La app no emitía ninguna. Lo que eso permitía, en concreto:

- Sin CSP, cualquier XSS que apareciera en el futuro tendría ejecución total,
  y ya hay bloques `<script>` en línea en tres plantillas.
- Sin `frame-ancestors`, la app se puede embeber desde otro origen. Con las
  credenciales Basic cacheadas por el navegador eso permite clickjacking sobre
  "Eliminar activo": el token CSRF protege el POST, pero no protege de que el
  clic lo dé el propio usuario engañado sobre la página real embebida.
- Sin `Referrer-Policy`, cada enlace externo filtra la URL completa de la app.

`'unsafe-inline'` se mantiene a propósito en esta primera iteración —quitar los
scripts y estilos en línea es un trabajo aparte—, y aun así la CSP ya impide
cargar scripts de otro origen y exfiltrar por `img-src` o `connect-src`
externos.

### [FT-A1] El token del bot de Telegram deja de escribirse en los logs

`API_URL` lleva el token en la ruta, y el mensaje de `httpx.HTTPStatusError`
incluye la URL entera. `logger.exception` volcaba ese mensaje tal cual y, con
el driver `json-file` del compose, el log persiste en disco — que es justo lo
que uno pega en un issue cuando pide ayuda.

Un token filtrado permite enviar mensajes suplantando al bot, leer los updates
pendientes con `getUpdates` (que aquí llevan importes y nombres de activos) y
secuestrarlo cambiando su webhook.

Ahora los dos sitios que construyen la URL con el token registran solo el
código HTTP o el tipo de excepción. Los `logger.exception` de Yahoo, CoinGecko
y Frankfurter se quedan como están: esas URLs no llevan credenciales y ahí la
traza completa es lo útil.

### [FT-C3] La app arranca desde cualquier directorio

Las plantillas, los estáticos y el `.env` se resolvían con rutas relativas al
directorio de trabajo del proceso. Funcionaba de casualidad: el `WORKDIR /app`
del Dockerfile lo salvaba. Cualquier otra forma de arrancar —uvicorn desde otra
carpeta, un systemd unit sin `WorkingDirectory`, un `docker run -w /`— fallaba
al montar los estáticos o al primer render.

El del `.env` era el peor de los tres precisamente porque **no fallaba**: si no
se leía, la app se levantaba con todos los valores por defecto, es decir sin
autenticación y sin Telegram, sin ningún error visible. Un cambio de directorio
bastaba para degradar la seguridad en silencio.

Las tres rutas se derivan ahora del propio módulo.

### [FT-C1] Exponer la app pasa a ser una decisión, no el valor por defecto

Tres cosas defendibles por separado se sumaban en algo que no lo era: el puerto
publicado en todas las interfaces, la autenticación desactivada de fábrica, y
nada que impidiera arrancar con `admin`/`changeme`. Cualquiera en la misma
Wi-Fi —un invitado, un dispositivo IoT comprometido— tenía lectura y escritura
del patrimonio, las operaciones y los extractos importados. Sin credenciales.

Ahora el puerto se publica en `127.0.0.1` y abrirlo a la red es explícito
(`FINANCE_BIND`). Con la autenticación activa, la app **no arranca** si la
contraseña sigue siendo la de fábrica o tiene menos de 8 caracteres: un fallo
al arrancar se corrige en un minuto, y una app expuesta con la contraseña de
fábrica puede pasar meses sin que nadie lo note. Y cuando queda sin
autenticación lo dice en el log, porque en la interfaz no se ve.

`ENABLE_AUTH` sigue desactivado por defecto a propósito: activarlo dejaría
fuera a la instalación existente al primer redespliegue.

**Al actualizar:** si usas la app desde el móvil, hace falta `FINANCE_BIND=0.0.0.0`
en el `.env` para volver a llegar a ella — y con eso, `ENABLE_AUTH=true`.

### [FT-C2] La autenticación aguanta contraseñas con tildes

`secrets.compare_digest` sobre `str` exige ASCII puro: con
`AUTH_PASSWORD=contraseña` —lo más natural, estando el `.env.example` en
español— unas credenciales incorrectas lanzaban `TypeError` y el manejador
global lo servía como la página "Algo ha fallado". Un 500 donde tocaba un 401.

Y la contraseña **correcta** tampoco entraba: `fastapi.security.HTTPBasic`
decodifica la cabecera con `.decode("ascii")` y la rechazaba antes de llegar a
compararse. El usuario quedaba fuera de su propia app sin ninguna pista, y la
salida evidente —desactivar `ENABLE_AUTH` para poder entrar— deja la aplicación
del patrimonio abierta en la LAN.

Ahora la cabecera se parsea a mano decodificando en UTF-8 y la comparación va
sobre bytes. Es la versión que `media-catalog` ya tenía escrita y comentada.

---

## 1.0.0 — 2026-08-02

Primera versión estable. La app lleva tiempo en uso diario contra datos reales;
lo que marca el 1.0 no es una funcionalidad nueva sino que el conjunto está
cerrado: el roadmap no tiene nada pendiente, el despliegue se prueba solo en
cada cambio y lo que se decidió **no** hacer está escrito con su motivo en
[ROADMAP.md](ROADMAP.md).

Todo lo de abajo entra en esta versión.

### Coste mensual de las recurrentes

Un recibo trimestral de 300 aparecía en la lista como un gasto de 300 y competía
visualmente con el alquiler, cuando pesa 100 al mes. Con periodicidades
mezcladas, saber cuánto se va en fijos exigía una división por regla.

Cada regla lleva ahora su coste mensual, y arriba va el total —gasto, ingreso y
lo que queda— con el desglose por categoría ordenado de mayor a menor, que es el
orden en el que se busca dónde recortar.

Los totales suman los importes **ya redondeados** de cada regla, los mismos que
se ven en la lista: sumar los exactos y redondear al final sería más preciso,
pero dejaría un total que no cuadra con la columna que se tiene delante. Solo
cuentan las activas, y las reglas sin tipo de cambio se excluyen y se nombran.

### El mapa de la cartera se amplía

En la portada mide 260px de alto, y a ese tamaño solo se rotulan las piezas
grandes: el resto quedaba en el tooltip, que en el móvil no existe. Se pulsa y
abre a seis veces esa superficie, con nombre completo, peso y variación, y cada
pieza enlaza a la ficha de su activo.

Los umbrales para escribir en una pieza van bajos a propósito: se intenta
rotular casi todas y luego se retira lo que no cupo. Medir el recorte después es
más fiable que adivinarlo antes con un ancho fijo. Aun así una astilla del 0,3%
no admite rótulo, de ahí la lista completa de debajo, que es además la
alternativa en texto de un gráfico que se lee por color.

### Áreas seguras, tarjetas igualadas y tablas ordenadas

Con `viewport-fit=cover` la página ocupa toda la pantalla y solo se contemplaba
el borde inferior: en PWA la barra superior se metía bajo el notch y en
horizontal los extremos de la barra inferior perdían media zona táctil. Ahora se
respetan los cuatro lados.

Las tarjetas abiertas de una misma fila del dashboard se igualan en alto —las
plegadas no, que estirar una sección cerrada dejaría una cabecera dentro de una
caja vacía— y el sobrante se lo lleva la gráfica. Eso último hay que dárselo a
`::details-content`: el navegador envuelve ahí lo que sigue al `<summary>`, así
que el hijo flex del `<details>` es esa caja y no la gráfica.

En las tablas, un nombre de fondo largo partía la fila en cuatro líneas y las
alturas iban de 48 a 102px. Se recorta con elipsis y el nombre completo queda en
el `title`.

### La cotización Euro/Dólar se puede invertir

Y la frase cambia con ella: si va en euros por dólar y sube, quien se revaloriza
es el dólar. El porcentaje pasa además a seguir el rango elegido —antes era
siempre el del día dijera lo que dijera el botón pulsado, lo que hacía inútil
cambiar de rango—.

### Licencia y avisos de terceros

MIT. El repositorio incluye Chart.js y los iconos de Lucide (MIT) y la fuente
Inter (SIL Open Font License), vendorizados para funcionar sin build step ni
CDN. De la fuente no había ninguna mención y la OFL exige distribuir su aviso
junto al fichero: estaba incumplido sin querer.

### La barra superior ya no arrastra la página a scroll horizontal

`.topbar` es un flex sin `wrap` y sus hijos no encogen, así que con las siete
etiquetas necesitaba 1.232px fijos. Por debajo de eso los botones de la derecha
se salían del viewport y **toda** la página hacía scroll horizontal: entre 721px
—donde ya no está la barra inferior de móvil— y ~1.230px, o sea tablet en
horizontal, ventana a media pantalla y portátiles pequeños.

En esa franja se dejan solo los iconos: 273px en vez de 894. Las etiquetas no se
quitan con `display: none` sino recortándolas, para que un lector de pantalla no
anuncie siete enlaces sin nombre, y cada enlace lleva `title`. El corte va en
1.280px y no en los 1.232px justos porque la media query mide el viewport con la
barra de scroll incluida, y en el límite exacto el margen era cero.

### El despliegue se prueba en el CI, no solo el código

Los dos incidentes graves del proyecto no estaban en el código: una columna que
faltaba en la migración —semanas devolviendo 500 en todas las páginas— y un
volumen cuyos ficheros pertenecían a root. Ningún test unitario los habría visto.

El job nuevo construye la imagen y la levanta contra los tres estados de volumen
que importan: vacío, con una base anterior a Alembic y con `/data` en manos de
root. En los tres exige contenedor `healthy`, las once rutas en 200 y que uvicorn
corra sin privilegios; en el segundo, que los datos sigan estando tras migrar.

> **Lo encontró en su primera ejecución**: `/analisis` devolvía 500 con
> `KeyError: 'twr'` en cualquier instalación recién montada. `portfolio_evolution`
> tenía dos caminos que no devolvían la misma forma, y sin operaciones emitía
> puntos sin esa clave. Como el arranque guarda un snapshot, la lista no estaba
> vacía y el `if evolution` de la vista no protegía de nada. No se parcheó la
> vista con un `.get()`: el problema era la forma inconsistente, y de hecho
> `rendimiento.py` ya llevaba dos `.get()` defensivos de alguien que tropezó
> antes y lo tapó en un sitio.

### El healthcheck detecta una app rota

`/salud` devolvía `{"status": "ok"}` sin tocar la base de datos, así que Docker
daba el contenedor por sano mientras todas las páginas fallaban. Ahora consulta
un `Asset` por el ORM —un SELECT con todas las columnas mapeadas, que revienta
igual que reventaban las páginas— y comprueba que la revisión de Alembic esté en
`head`. Si algo falla devuelve 503 y el contenedor pasa a `unhealthy`. Un
`SELECT 1` no habría servido: la conexión iba bien y la tabla existía.

Los errores dejan además de salir como texto plano de Starlette. El 404 y el 500
llevan una página con la cara de la app, autocontenida a propósito: si el fallo
viene de la base, una plantilla que pinte navegación y totales fallaría también.

### Posiciones cerradas fuera de la cartera viva

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

### Documentar que un traspaso de bróker no es un duplicado

El documento de roadmap planteaba "elegir con qué divisa quedarse" para consolidar dos
activos que el detector marcaba como repetidos. Era una lectura equivocada del
dato: invitaba a fusionar dos posiciones distintas o a borrar operaciones reales.
Se añade también la sección "Datos reales" del README — el repositorio es
público y el `.gitignore` protege los ficheros, no la prosa.

### Rebalanceo

Peso objetivo por activo, desviación en porcentaje y en dinero, y reparto de una
aportación entre lo que va corto.

No propone ventas a propósito: vender para rebalancear cristaliza plusvalías y su
peaje fiscal. Con aportación, el objetivo se mide sobre la cartera **futura**, no
la actual, porque el propio dinero nuevo mueve los porcentajes. El reparto va
solo a los que van por debajo: meterle más al que ya sobrepasa su peso agravaría
la desviación.

### Estados vacíos con acción y escala en tokens

Los estados vacíos decían qué hacer en prosa ("Registra la primera arriba"), lo
que obliga a buscar ese "arriba". Ahora el sitio al que ir es un botón.

La jerarquía tipográfica **no** se rediseñó: la propuesta decía que "casi todo
pesa igual" y al comprobarlo ya no era cierto. Se recogió la escala existente en
tokens en vez de tocar decenas de reglas para dejarlo igual.

### Operaciones: alta plegada, chips y separadores de mes

A esa página se entra a consultar, no a registrar, pero el formulario ocupaba la
primera pantalla. Los filtros de tipo y cuenta pasan a chips combinables; el de
activo se queda en select porque una cartera tiene decenas. Los meses van en
tabla propia y no por `strftime("%B")`: el contenedor corre en locale C.

### Avatares de color por activo

Círculo con dos iniciales y color estable por ticker. El color sale de `crc32` y
no de `hash()`, que lleva sal por proceso y habría cambiado los colores en cada
reinicio. Las iniciales saltan el relleno ("Corporation", "Inc.", "Acc"), o
`MUESTRA Corporation` habría dado NC y `Alfabeto (A)` habría dado `A(`.

### Marcas de compra/venta sobre la curva de precio

Cada operación es un punto sobre la propia curva: verde compra, rojo venta, ámbar
si ese día hubo de las dos. Antes había que cruzar mentalmente la gráfica con la
lista de abajo para saber si compraste caro.

### Alertas de precio por Telegram

Tres condiciones por activo. Lo delicado no es la condición sino el rearme: un
activo que cruza a la baja sigue por debajo durante horas y avisaría en cada
refresco. Cada alerta recuerda cuándo saltó y no repite hasta que el precio deja
de cumplirla.

### Móvil

FAB de gasto rápido y voz (apuntar un gasto pasa de dos navegaciones a un toque),
barra inferior de 4 + "Más", historiales apilados en tarjetas sin scroll
horizontal, tirar para actualizar, e icono maskable con standalone en iOS.

El **swipe** para editar/borrar se descartó: se pedía porque las filas iban
apretadas, y con las tarjetas y los objetivos de 44px ya están visibles y a un
toque.

### Fusionar activos duplicados y desglosar la posición por cuenta

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

### Mapa de la cartera en el dashboard

Treemap de la parte invertida: superficie = peso, color = variación del día.

Implementado **sin** `chartjs-chart-treemap` (que era lo que apuntaba la
propuesta): algoritmo squarified propio en ~60 líneas, para no meter un bundle de
terceros sin auditar. Los rectángulos son divs, así que heredan los estilos y el
modo privacidad sin trabajo extra. El color satura a ±3%: más allá el ojo no
distingue.

### Lista de valores en seguimiento (watchlist)

Seguir el precio de algo que aún no tienes obligaba a darlo de alta como activo,
y entonces entraba en el patrimonio con cantidad cero.

Va en **tabla aparte, no como `Asset` con bandera**: los activos entran en el
patrimonio, en las allocations, en el X-Ray y en la reconstrucción del histórico,
y una bandera obligaría a acordarse de excluirlos en cada una de esas consultas.
Un olvido inflaría el patrimonio con dinero que no tienes.

### Índices de referencia configurables

MSCI World y S&P 500 estaban fijos en un diccionario del código. Pasan a tabla,
gestionable desde Análisis. La migración siembra los dos de siempre con las
mismas claves y símbolos, así que el histórico ya descargado se sigue usando.

El símbolo se valida contra Yahoo antes de guardarlo: uno que no exista se
quedaría sin serie para siempre y saldría como columna vacía sin explicar por qué.

---

### XIRR, rendimiento por año y serie de aportaciones

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

### Avisos de mercado anclados a la zona de su plaza

Las horas estaban escritas en UTC pero el scheduler corre en `settings.timezone`.
Con `TIMEZONE=Europe/Madrid` los cuatro avisos saltaban **dos horas antes**: el
"cierre de Europa" llegaba a las 15:30, con la bolsa aún abierta.

Cada sesión declara ahora la zona de su mercado, así que además se ajusta sola al
horario de verano (EE. UU. y Europa no lo cambian el mismo fin de semana).

Los cinco resúmenes del día llamaban al mismo `send_daily_summary()` sin
parámetros y llegaban idénticos; ahora cada uno lleva su título.

### Botón de refrescar precios y mezcla de divisas al importar

- `base.html` llamaba a `showToast`, que no es global (vive en el IIFE de
  `app.js`, expuesto como `window.appToast`). El POST salía bien, pero el
  manejador reventaba con `ReferenceError` **antes** del `location.reload()`: ni
  aviso ni recarga, y parecía que el botón estaba muerto.
- `_match_asset` casaba por ISIN/ticker/nombre pero **no miraba la divisa**. Una
  fila en euros colgada de un activo en dólares no se convierte: se reinterpreta
  el precio y el coste medio mezcla dos divisas sin dejar rastro. Se rechaza y se
  explica, en el preview y otra vez al confirmar.

### Propiedad de `/data` al arrancar, no al construir

El paso a usuario sin privilegios (en la auditoría inicial) hizo `chown` de `/data` en el
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

### Despliegue independiente de la suite

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

### `assets.avg_cost_override` en bases ya desplegadas

La columna se añadió al modelo pero nunca se declaró en la migración ligera.
Como `create_all()` no altera tablas existentes, cualquier base creada antes se
quedaba sin ella: la app arrancaba con normalidad (el fallo del snapshot inicial
queda capturado) y después devolvía **500 en todas las páginas**.

No se veía en los tests porque una base recién creada sí trae la columna: solo
aparecía sobre datos ya desplegados. Y el healthcheck del contenedor solo prueba
`/salud`, que no toca la base, así que Docker reportaba el contenedor **healthy**
mientras la app no servía ni una página.

### Auditoría: XSS, hueco de auth, divisa de voz y FX silencioso

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
