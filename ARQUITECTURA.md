# Arquitectura

Por qué la app está construida así. Lo que se puede leer en el código no se
repite aquí: esto recoge las decisiones que no se deducen mirándolo, y los
fallos que las provocaron.


## El stack, y por qué es tan corto

FastAPI, SQLAlchemy, SQLite, Jinja2 y Chart.js. **Sin build step, sin
framework de front, sin base de datos externa.**

No es minimalismo por deporte. Es una app mono-usuario que corre en un servidor
doméstico y que tiene que seguir arrancando dentro de tres años sin que nadie la
mantenga. Cada pieza que se añade es una que puede romperse sola: una versión de
Node que ya no compila, un paquete abandonado, un contenedor de base de datos
que no levanta. Lo que hay aquí se instala con `pip install -r requirements.txt`
y arranca.

Consecuencias asumidas:

- **Chart.js va vendorizado** en `app/static/js/`, no desde una CDN. La app
  funciona sin red salvo para pedir precios.
- **Los iconos son una macro de Jinja** con los SVG embebidos, no un paquete.
- **El CSS son tokens y componentes propios**, unas mil líneas. No hay Tailwind
  ni nada que haya que compilar.
- **El mapa de la cartera es un treemap escrito a mano** (~60 líneas del
  algoritmo *squarified* de Bruls et al.) en vez de un plugin de Chart.js. Los
  rectángulos son divs, así que heredan los estilos y el modo privacidad sin
  trabajo extra.


## El dinero es `Decimal`, nunca `float`

Los importes del libro se guardan en un tipo `Money` (`Numeric(12,2)`). Con
`Float`, diez gastos de 0,10 € sumaban `0.9999999999999999`.

El tipo de cambio llega como `float` desde una API, así que al convertir se pasa
a decimal **por su representación textual** (`Decimal(str(x))`, no
`Decimal(x)`), para no arrastrar el ruido binario al importe final.

Las posiciones de inversión sí usan `float`: son cantidades y precios de
mercado, no dinero del libro, y ahí la precisión decimal no aporta.


## Sin tipo de cambio no se inventa un número

Es la regla que más veces aparece en el código. Cuando falla la conversión:

- El importador **rechaza** la fila y lo explica.
- La recurrente **no se genera** y `last_generated` no avanza, así que el
  catch-up del siguiente arranque la creará bien.
- Los totales **excluyen** el activo y lo dicen en pantalla.
- El snapshot diario **no se guarda** si sale incompleto.

Contar 20 USD como 20 EUR es peor que no contarlos: el error queda dentro del
dato, sin rastro, y contamina la serie histórica para siempre.


## Migraciones: el fallo que tuvo la app caída tres semanas

Las primeras versiones creaban el esquema con `create_all()`, que **no altera
tablas existentes**. Cuando se añadió una columna al modelo, las bases ya
desplegadas se quedaron sin ella: la app arrancaba con normalidad y devolvía 500
en todas las páginas.

De ahí tres decisiones:

**`init_db()` reconcilia las bases anteriores a Alembic.** Detecta que no tienen
`alembic_version`, les completa las columnas que falten, las marca en la
revisión inicial y sigue con el resto de migraciones. Automático a propósito: un
paso manual documentado significa que cualquier despliegue que venga de una
versión anterior se cae al actualizar.

**Nunca `alembic stamp head` sobre una base así.** La daría por actualizada sin
añadirle nada, que es cómo se esconde el fallo en vez de arreglarlo.

**Las migraciones corren en el entrypoint, antes de uvicorn.** Dentro del
lifespan de FastAPI podían quedarse esperando un lock de SQLite y dejaban el
arranque colgado sin explicar por qué. En el entrypoint no hay servidor ni
scheduler ni bot tocando la base.


## El healthcheck consulta la base

Durante aquellas tres semanas Docker reportaba el contenedor **healthy**, porque
`/salud` devolvía `{"status": "ok"}` sin tocar nada.

Ahora consulta un `Asset` por el ORM —un SELECT con todas las columnas
mapeadas, que falla igual que fallaban las páginas— y comprueba que la revisión
de Alembic esté en `head`. Si algo falla devuelve 503 y el contenedor pasa a
`unhealthy`.

Un `SELECT 1` no habría servido: la conexión iba bien y la tabla existía.


## El CI prueba el despliegue, no solo el código

Los dos incidentes graves del proyecto no estaban en el código:

1. Una columna que faltaba en la migración.
2. Un volumen cuyos ficheros pertenecían a root, que metía el contenedor en
   bucle de reinicio con `attempt to write a readonly database`. El `chown` del
   Dockerfile solo afecta a la imagen: un volumen que ya existe conserva la
   propiedad de sus ficheros, así que el ajuste se hace en cada arranque.

Ningún test unitario los habría visto. Por eso el CI construye la imagen y la
levanta contra los tres estados de volumen que importan —vacío, con una base
anterior a Alembic, y con `/data` en manos de root— exigiendo contenedor sano,
las once rutas en 200 y la app corriendo sin privilegios.

En su primera ejecución encontró un 500 en `/analisis` que llevaba ahí desde
siempre y que solo se daba en instalaciones recién montadas.


## Seguridad

**CSRF a nivel de app, no router a router.** Se declara como dependencia de la
aplicación entera para que cubra también cualquier ruta que se añada después,
que es justo como se coló en su día un endpoint sin autenticación.

**La app nunca corre como root.** El contenedor arranca como root el tiempo
justo de que el entrypoint ajuste la propiedad de `/data`, y acto seguido baja
de privilegios con `setpriv`.

**Auth desactivada por defecto** porque la app se usa desde el móvil en la LAN.
Si sale de casa hay que activarla y ponerla tras TLS. Está avisado en el README
y es una decisión consciente, no un olvido.

**`pip-audit` en el CI**, como job propio: que una dependencia tenga un CVE no
significa que el código esté roto, y verlo en un check separado dice de un
vistazo cuál de las dos cosas ha pasado.


## Rendimiento

**`selectinload` en las consultas de cartera.** `asset_summary` recorre las
operaciones de cada activo; sin esto son N+1 consultas en cada carga.

**Caché de tipos de cambio y de series históricas** en `price_history`. La misma
tabla guarda cierres de activos, de índices y de divisas, distinguidos por el
prefijo del símbolo.

**Índices de fecha** en transacciones y operaciones, que es por donde se filtra
siempre.

**Se mide antes de optimizar.** `DEBUG_SQL=1` cuenta las sentencias de cada
petición y las publica en `X-Consultas-SQL`; los tests usan la misma función
para fijar un techo por página. La línea base con cinco activos es de 39
consultas en la portada frente a 4-5 en el resto, así que la portada es la que
hay que vigilar. Sin ese número, optimizar es una cuestión de opinión.

El contador es **global, no por hilo**: FastAPI ejecuta los endpoints síncronos
en un hilo del pool, así que un `threading.local` contaba cero. La contrapartida
es que dos peticiones simultáneas mezclan sus cuentas, y por eso está detrás de
una variable de entorno y apagado por defecto.


## Cosas pequeñas con motivo

**El color de los avatares sale de `crc32`, no de `hash()`.** `hash()` lleva sal
por proceso: los colores habrían cambiado en cada reinicio.

**Los nombres de mes van en una tabla propia, no por `strftime("%B")`.** El
contenedor corre en locale C y devolvía los meses en inglés.

**Las horas de los avisos de mercado se anclan a la zona de cada plaza.** Estaban
escritas en UTC mientras el scheduler corría en la zona configurada: con
`TIMEZONE=Europe/Madrid` los avisos saltaban dos horas antes y el "cierre de
Europa" llegaba con la bolsa abierta.

**El XIRR se resuelve por bisección, no por Newton-Raphson.** Algo más lento,
pero no diverge con flujos irregulares, que es lo que tiene una cartera real.

**`.gitattributes` fuerza LF en los `.sh`.** Con `core.autocrlf=true`, un clon en
Windows dejaría el shebang como `#!/bin/sh\r` y el contenedor no arrancaría.

**`*.db` no cubre `finance.db-wal`.** El sufijo va después de la extensión, así
que el WAL —que puede llevar miles de filas que aún no están en el `.db`— se
lista aparte en el `.gitignore`.

**El `.dockerignore` va alineado con el `.gitignore`.** No porque nada de eso
llegue a la imagen —el Dockerfile copia rutas concretas—, sino porque el
contexto entero se sube al daemon en cada build y se queda en la caché de
capas. Bastaría con que alguien escribiera `COPY . .` para publicar un extracto
de banco en el registro. Alinearlo bajó el contexto de 1,76 MB a 15 kB.

**La imagen lleva los tests dentro, a propósito.** `COPY tests ./tests` permite
`docker exec finance-tracker pytest -q` contra el contenedor real, que es donde
aparecieron los dos incidentes graves. El coste son 33 ficheros de texto en una
imagen que ya pesa cientos de megas por las dependencias; el beneficio es poder
comprobar el despliegue sin montar nada.


## Tests

Unos 470, en tres niveles:

- **Servicios**: cálculo de posiciones, histórico, recurrentes, parser de voz,
  importadores, clasificación, rendimiento, rebalanceo.
- **HTTP contra la app real**: que cada página responda, que la autenticación
  cubra todas las rutas y que ningún dato guardado pueda romper el HTML.
- **Sistema visual**: que los tres planos de profundidad sigan separándose, que
  no aparezca un `font-size` suelto en el CSS, que el diálogo no se pinte
  cerrado. No comprueban que sea bonito —eso no se testea— sino que las
  decisiones que lo sostienen no se deshagan por descuido.

No tocan la red ni el volumen `/data`: las APIs externas van parcheadas y la
base es SQLite en memoria.
