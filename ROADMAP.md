# Roadmap y decisiones

Qué hay en la 1.0, qué se dejó fuera **a propósito** y qué deuda técnica se
asume. Este documento existe para no volver a proponer lo que ya se descartó con
un motivo, que es exactamente lo que pasa cuando el motivo no se escribe.

El detalle del porqué de cada cambio está en [CHANGELOG.md](CHANGELOG.md); las
decisiones de ingeniería, en [ARQUITECTURA.md](ARQUITECTURA.md).

Objetivo del proyecto: el tracker de patrimonio personal más completo posible
**sin romper sus principios** — local-first, sin APIs de pago, stack mínimo,
mono-usuario. Principio de diseño: **limpio en superficie, técnico en
profundidad**; la primera pantalla de cada sección responde una pregunta y el
detalle vive un clic más adentro.


## Qué entra en la 1.0

**Patrimonio.** Activos de cuatro tipos valorados en una moneda base común,
evolución histórica diaria e intradía, y un mapa de la cartera donde la
superficie es el peso y el color la variación del día.

**Inversión.** Posiciones derivadas de operaciones, con coste medio, P&L
realizado y no realizado, y el efecto divisa separado del efecto precio.

**Rendimiento.** TWR, XIRR, CAGR y rentabilidad por año natural contra los
índices que se elijan.

**Ingresos y gastos.** Transacciones con categorías, presupuestos, reglas
recurrentes con catch-up y coste mensual normalizado, e importación desde CSV
del banco.

**Importadores.** Trade Republic, Revolut (CSV y PDF), OKX y un formato
genérico, con deduplicación por huella.

**Análisis.** Allocations por divisa, región y sector; comisiones acumuladas;
X-Ray de riesgos; rebalanceo por peso objetivo.

**Avisos.** Alertas de precio y resumen diario por Telegram, con entrada por voz
en español.

**Higiene.** Detección de duplicados con fusión guiada, posiciones cerradas
apartadas de la cartera viva, y watchlist para lo que aún no se tiene.


## Excluido por decisión

No se re-propone sin hablarlo antes:

- **Tracking de dividendos.**
- **Calculadora FIRE y proyecciones.**
- **Regla de fondo de emergencia.**
- **Divisas más allá de EUR/USD** como moneda base.

Y tres que se aplazaron porque resuelven problemas que todavía no se han dado:

- **Carpeta vigilada** para importar sola. Automatiza algo que hoy son dos clics.
- **Más importadores.** Harán falta el día que se abra cuenta en otro bróker.
- **Modo demo** con datos inventados. Hará falta el día que haya que enseñar la
  app sin enseñar la cartera.


## Deuda técnica asumida

Cosas que salieron en la auditoría y se decidió **no** arreglar, con el motivo.

**Backfill de 5 años del histórico** (`services/history.py`). Un activo del que
la fuente tenga menos de 5 años se vuelve a descargar entero en cada pasada
diaria, porque la condición compara contra una fecha objetivo que nunca alcanza.
Arreglarlo bien exige recordar por símbolo que ya se intentó —tabla nueva y
migración— para ahorrar una petición por activo y día. No compensa. Y ojo: esa
misma condición es la que da el tramo antiguo a los activos registrados antes de
que existiera el rango de 5 años, y `tests/test_history.py` lo exige.

**Backup a ruta fija** (`routers/dashboard.py`). La descarga escribe siempre en
`/tmp/finance-backup.db`: dos descargas simultáneas se pisan. Es una app
mono-usuario, así que en la práctica no ocurre.

**`asset_summary` sin FX al filtrar operaciones** (`routers/operations.py`). Al
filtrar por un activo extranjero, el resumen no trae la descomposición de divisa
que sí sale en la ficha. Inconsistencia menor, no un dato erróneo.

**Auth desactivada por defecto.** Deliberado: la app se usa desde el móvil en la
LAN y activar la autenticación de serie dejaría fuera a las instalaciones
existentes al primer redespliegue. Lo que sí cambió (auditoría, [FT-C1]) es que
esa decisión ya no viene acompañada de un puerto abierto: el binding es
`127.0.0.1` por defecto y exponerlo es un acto explícito (`FINANCE_BIND`), con
un validador que impide arrancar con la contraseña de fábrica y un aviso en el
log cuando la app queda sin credenciales.

**Modo claro.** Toda la paleta está pensada en oscuro. Derivarla obliga a
revisar además las cuatro gráficas y el mapa de cartera, y duplica la superficie
que hay que mantener.


## Criterios que conviene no perder

Cosas que costaron descubrirse y que un cambio futuro podría deshacer sin darse
cuenta. Hay tests que las fijan, pero el motivo solo está aquí.

**Un traspaso de bróker no es un duplicado.** Al cambiar de bróker se vende en
uno y se compra en otro el mismo día. Quedan dos activos con el mismo valor
detrás, pero **no son la misma posición**: uno es un registro cerrado y el otro
la posición viva. Fusionarlos juntaría dos escalas de precio bajo un mismo coste
medio, y borrar el cerrado tiraría operaciones reales de las que depende la
rentabilidad histórica. Por eso el detector solo mira posiciones vivas.

**Sin tipo de cambio no se inventa un número.** Ni al importar, ni al generar una
recurrente, ni al sumar totales. El importe se excluye y se avisa: contar 20 USD
como 20 EUR es peor que no contarlos, porque el error queda sin rastro.

**Cada año natural arranca en el cierre del anterior**, no en su primer día. Si
no, el salto de fin de diciembre a primeros de enero no se lo apunta ningún año.

**Los totales cuadran con lo que se ve.** Se suman los importes ya redondeados de
cada fila, no los exactos: un céntimo que no encaja en una columna a la vista se
nota más que un céntimo de precisión perdido.

**El healthcheck consulta la base.** Un `SELECT 1` no vale: el fallo que tuvo la
app caída semanas fue una columna ausente, con la conexión y las tablas
perfectamente bien.


## Si algún día hay una 1.1

Nada de esto está comprometido; es lo que quedó apuntado como siguiente si el
uso lo pide.

- **Modo claro**, que es lo que más luce en capturas de un repositorio público.
- **Color con intención**: hoy hay un acento y tres colores semánticos, y los
  avatares ya generan una variedad de tono que no se aprovecha.
- **Movimiento contenido**: transiciones de estado y entrada de las gráficas,
  respetando `prefers-reduced-motion`.
