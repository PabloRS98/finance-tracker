# finance-tracker v4 — Estado y pendientes

Objetivo: el tracker de patrimonio personal más completo posible **sin romper sus
principios** (local-first, sin APIs de pago, stack mínimo, mono-usuario).
Filosofía: **limpio en superficie, técnico en profundidad** — la primera pantalla
de cada sección muestra lo esencial; el detalle vive un clic más adentro.

> Este documento era una lista de propuestas. Se ha reescrito como **estado real**:
> buena parte ya está implementada y mantenerlo como lista de deseos hacía que se
> propusiera dos veces lo mismo. Lo hecho se marca con su PR; el detalle del
> porqué de cada cambio está en [CHANGELOG.md](CHANGELOG.md).

> Excluido por decisión del usuario (no re-proponer): tracking de dividendos,
> calculadora FIRE/proyecciones, regla de fondo de emergencia, divisas más allá
> de EUR/USD.

---

## 1 · Funcionalidad

### Hecho

| # | Propuesta | Dónde |
|---|-----------|-------|
| 0 / 1.1 | Nombres legibles de posiciones (`MSFT` → `Microsoft Corporation`) | ya estaba |
| 1.2 | Autocompletado de ticker al añadir activo | ya estaba |
| 1.3 | Comisiones acumuladas (total e histórico por año) | ya estaba |
| 1.4 | Backup automático de la BD + descarga | ya estaba |
| 1.6 | CAGR / rentabilidad anualizada | ya estaba |
| 1.5 | Benchmark personalizable desde la interfaz | PR #8 |
| 1.7 | Aportado vs. revalorización en la gráfica | PR #7 |
| 1.8 | MWR / XIRR de la cartera | PR #7 |
| 1.9 | Fusión de duplicados + posiciones por cuenta | PR #11 |
| 1.11 | Watchlist | PR #9 |
| 1.12 | Heatmap de cartera | PR #10 |
| 1.13 | Rendimiento por año vs. índice | PR #7 |

### Pendiente

Nada del roadmap. Las dos que quedaban se cerraron:

| # | Propuesta | PR |
|---|-----------|-----|
| 1.10 | Alertas de precio por Telegram | #20 |
| 1.14 | Rebalanceo | #25 |

**1.15 (carpeta vigilada), 1.16 (más importadores) y 1.17 (modo demo)** siguen
sin hacer, pero por decisión y no por olvido: resuelven problemas que todavía no
se han dado. La carpeta vigilada automatiza una importación que hoy son dos
clics; los importadores nuevos harán falta el día que abras cuenta en otro
bróker; y el modo demo, el día que quieras enseñar la app a alguien.


## 2 · Diseño

Principio: **cada pantalla responde una pregunta en <2 segundos**; el resto se
pliega.

### Hecho

- **2.1 Dashboard**: hero con patrimonio + variación + sparkline, secciones
  plegables con estado en localStorage, rangos de gráfica (1D/1S/1M/6M/1A/Todo)
  y selector de benchmark. El mapa de la cartera (1.12) entra como sección
  propia.
- **2.5 Análisis**: es la sección técnica y ahí se ha añadido, no simplificado —
  allocations, comisiones, X-Ray, TWR/XIRR/CAGR y rendimiento por año.

### Pendiente

Nada. El paquete de diseño se cerró entre los PR #15 y #24:

| # | Propuesta | PR |
|---|-----------|-----|
| 2.2 | Marcas de compra/venta sobre la curva de precio | #21 |
| 2.3 | Avatares de color por activo | #22 |
| 2.4 | Alta plegada, filtros por chips y separadores de mes | #23 |
| 2.6 | Estados vacíos con acción y escala en tokens | #24 |
| 2.7 | Móvil: FAB, barra 4+Más, tarjetas, tirar para actualizar, PWA | #15–#19 |

Tres cosas se hicieron distinto de lo propuesto, con su motivo:

- **2.2 no lleva pestañas.** Se añadieron las marcas de compra/venta, que es lo
  que respondía "¿compré caro o barato?". Las pestañas se descartaron porque los
  rangos de la gráfica ya evitan el scroll largo que las motivaba.
- **2.6 no rediseña la tipografía.** La propuesta decía que "casi todo pesa
  igual"; al comprobarlo ya no era cierto. Se recogió la escala en tokens en vez
  de tocar decenas de reglas para dejarlo igual.
- **2.7 no lleva swipe.** Se pedía porque las filas iban apretadas; con las
  tarjetas y los objetivos de 44px, editar y borrar ya están visibles y a un
  toque. Un gesto oculto que compite con el scroll añadía riesgo sin resolver
  nada.


## 3 · Deuda técnica conocida

Cosas que salieron en la auditoría y se decidió **no** arreglar, con el motivo.
Están aquí para no volver a proponerlas sin contexto.

**Backfill de 5 años del histórico** (`services/history.py`). Un activo del que
Yahoo tenga menos de 5 años se vuelve a descargar entero en cada pasada diaria,
porque la condición compara contra una fecha objetivo que nunca alcanza.
Arreglarlo bien exige recordar por símbolo que ya se intentó, o sea tabla nueva y
migración, para ahorrar una petición por activo y día. **No compensa.** Y ojo:
esa misma condición es la que da el tramo antiguo a los activos registrados antes
de que existiera el rango 5A, y `tests/test_history.py` lo exige.

**Backup a ruta fija** (`routers/dashboard.py`). La descarga escribe siempre en
`/tmp/finance-backup.db`: dos descargas simultáneas se pisan. Es una app
mono-usuario, así que en la práctica no ocurre.

**`asset_summary` sin FX al filtrar operaciones** (`routers/operations.py`). Al
filtrar por un activo extranjero, el resumen no trae la descomposición de divisa
que sí sale en la ficha. Inconsistencia menor, no un dato erróneo.

**Auth desactivada por defecto con el puerto en `0.0.0.0`.** Es deliberado: la
app se usa desde el móvil en la LAN. Si sale de casa, activar `ENABLE_AUTH` y
ponerla tras un proxy con TLS o VPN. Está avisado en el README.

---

## 4 · Qué queda

El roadmap de la v4 está cerrado. Sigue vivo lo que se marcó arriba como
pendiente por decisión (1.15, 1.16, 1.17) y la deuda técnica de la sección
anterior, que se dejó a propósito.

Fuera del roadmap queda una decisión tuya, no técnica:

- **`.env.enc` sigue sin versionar** porque contiene secretos de las tres apps.
  Regenerarlo solo con lo de finance pide tu clave age.

### Un traspaso de bróker no es un duplicado

Aquí figuraba, como decisión pendiente, "elegir con qué divisa quedarse" para
consolidar dos activos que el detector marcaba como repetidos. **Era una lectura
equivocada del dato**, y conviene dejar escrito el criterio para que no se vuelva
a proponer:

Al cambiar de bróker se vende en uno y se compra en otro el mismo día. Quedan dos
activos con el mismo valor detrás, pero **no son la misma posición**: uno es un
registro cerrado y el otro la posición viva. Si además cada bróker cotiza en una
plaza distinta, van en divisas distintas.

Fusionarlos juntaría dos escalas de precio bajo un mismo coste medio, y borrar el
cerrado tiraría operaciones reales de las que depende la rentabilidad histórica.
Desde el PR #28 el detector solo mira posiciones vivas, así que el aviso ya no
aparece; la guarda de divisa sigue como segunda red.

> Los casos concretos de una cartera no se documentan aquí: este repositorio es
> público. Ver «Datos reales» en el README.
