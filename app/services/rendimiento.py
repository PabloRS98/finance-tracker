"""Métricas de rendimiento derivadas de la serie de evolución de la cartera.

Todo sale de lo que `history.portfolio_evolution` ya calcula, sin volver a
pedir precios ni tipos de cambio:

- **XIRR (MWR)**: la rentabilidad de TU dinero, que sí depende de cuándo
  aportaste. El TWR que ya había mide la estrategia encadenando rendimientos
  diarios y descontando las aportaciones, así que dos personas con las mismas
  posiciones tienen el mismo TWR aunque una entrara en el peor momento. El XIRR
  las distingue. Se enseñan los dos porque responden preguntas distintas.
- **Rendimiento por año natural**: el TWR acumulado partido por años, para poder
  compararlo con lo que rindió un índice en ese mismo año.
"""
from datetime import date

# Máximos de la búsqueda de la tasa: por debajo de -99,99% el descuento se va a
# infinito y por encima de 1000% anual ya no es una cartera, es un error de dato.
_TASA_MINIMA = -0.9999
_TASA_MAXIMA = 10.0


def _valor_actual_neto(tasa: float, flujos: list[tuple[date, float]], origen: date) -> float:
    """VAN de los flujos descontados a `tasa` anual, tomando `origen` como día 0."""
    return sum(
        importe / (1.0 + tasa) ** ((dia - origen).days / 365.0)
        for dia, importe in flujos
    )


def xirr(flujos: list[tuple[date, float]]) -> float | None:
    """Rentabilidad anualizada (%) que hace cero el valor actual neto.

    `flujos` son pares (fecha, importe) con el signo desde tu bolsillo: las
    aportaciones negativas y los cobros —incluido el valor actual de la cartera
    como cobro final— positivos. Devuelve None si no hay solución razonable:
    sin flujos de los dos signos la ecuación no corta el cero.

    Se resuelve por bisección en vez de Newton-Raphson: es algo más lenta pero
    no se va a las nubes con flujos irregulares, que es justo lo que tiene una
    cartera real (aportaciones a saltos y alguna venta suelta).
    """
    if len(flujos) < 2:
        return None
    if not (any(i < 0 for _, i in flujos) and any(i > 0 for _, i in flujos)):
        return None

    flujos = sorted(flujos)
    origen = flujos[0][0]
    if all(dia == origen for dia, _ in flujos):
        return None  # todo el mismo día: no hay periodo que anualizar

    van_min = _valor_actual_neto(_TASA_MINIMA, flujos, origen)
    van_max = _valor_actual_neto(_TASA_MAXIMA, flujos, origen)
    if van_min * van_max > 0:
        return None  # la solución cae fuera del rango que consideramos creíble

    bajo, alto = _TASA_MINIMA, _TASA_MAXIMA
    for _ in range(200):
        medio = (bajo + alto) / 2.0
        van = _valor_actual_neto(medio, flujos, origen)
        if abs(van) < 1e-7 or (alto - bajo) < 1e-9:
            return round(medio * 100, 2)
        if van * van_min > 0:
            bajo, van_min = medio, van
        else:
            alto = medio
    return round(((bajo + alto) / 2.0) * 100, 2)


def flujos_desde_evolucion(evolution: list[dict]) -> list[tuple[date, float]]:
    """Flujos de caja para el XIRR, sacados de la serie de evolución.

    `aportado` es la suma acumulada de lo puesto: su diferencia de un día al
    siguiente es la aportación (o retirada) de ese día. Se toma de ahí en vez de
    releer las operaciones para no repetir la conversión de divisa, que la serie
    ya hizo con el tipo de cambio de cada día.
    """
    flujos: list[tuple[date, float]] = []
    anterior = 0.0
    for punto in evolution:
        aportado = punto.get("aportado")
        if aportado is None:
            continue
        movimiento = aportado - anterior
        anterior = aportado
        if abs(movimiento) > 1e-9:
            # Signo invertido: aportar es dinero que sale de tu bolsillo
            flujos.append((date.fromisoformat(punto["fecha"]), -movimiento))

    if evolution:
        ultimo = evolution[-1]
        valor = ultimo.get("invertido") or 0.0
        if valor > 0:
            # La cartera se "cobra" hoy a precio de mercado para cerrar la ecuación
            flujos.append((date.fromisoformat(ultimo["fecha"]), valor))
    return flujos


def xirr_de_la_cartera(evolution: list[dict]) -> float | None:
    return xirr(flujos_desde_evolucion(evolution))


def _rendimiento_del_tramo(inicio_idx: float, fin_idx: float) -> float | None:
    if inicio_idx <= 0:
        return None
    return round(100.0 * (fin_idx / inicio_idx - 1.0), 2)


def _tramos_por_ano(serie: list[tuple[int, float]]) -> dict[int, tuple[float, float]]:
    """De una serie ordenada de (año, valor) saca el (base, cierre) de cada año.

    La base de un año es el CIERRE del anterior, no su primer valor: si no, el
    salto de fin de diciembre a primeros de enero no se lo apunta ningún año y
    la suma de los años no cuadra con el acumulado. El primer año es la
    excepción obligada: no hay diciembre previo, así que arranca en su primer
    valor y por eso sale marcado como parcial.
    """
    cierres: dict[int, float] = {}
    primeros: dict[int, float] = {}
    for ano, valor in serie:
        cierres[ano] = valor
        primeros.setdefault(ano, valor)

    tramos: dict[int, tuple[float, float]] = {}
    anos = sorted(cierres)
    for posicion, ano in enumerate(anos):
        base = cierres[anos[posicion - 1]] if posicion else primeros[ano]
        tramos[ano] = (base, cierres[ano])
    return tramos


def _indice_por_ano(evolution: list[dict]) -> dict[int, tuple[float, float]]:
    """Índice TWR de cada año natural con exposición."""
    return _tramos_por_ano([
        (int(p["fecha"][:4]), 1.0 + p.get("twr", 0.0) / 100.0)
        for p in evolution if p.get("invertido")
    ])


def _cierres_por_ano(puntos: list[dict]) -> dict[int, tuple[float, float]]:
    return _tramos_por_ano([
        (int(p["fecha"][:4]), p["close"]) for p in puntos if p.get("close")
    ])


def rendimiento_por_ano(evolution: list[dict], benchmarks: dict | None = None) -> list[dict]:
    """Rentabilidad de cada año natural, con la de los índices al lado.

    El primer año casi nunca es un año completo (la cartera empezó a mitad), y
    el último tampoco: por eso se marca `parcial`, para que la tabla no invite a
    comparar un trimestre con un año entero.
    """
    indices = _indice_por_ano(evolution)
    if not indices:
        return []

    expuestos = [p for p in evolution if p.get("invertido")]
    primer_dia = date.fromisoformat(expuestos[0]["fecha"])
    ultimo_dia = date.fromisoformat(expuestos[-1]["fecha"])

    series_bench = {
        clave: (bench.get("label", clave), _cierres_por_ano(bench.get("points", [])))
        for clave, bench in (benchmarks or {}).items()
    }

    filas = []
    for ano in sorted(indices):
        inicio_idx, fin_idx = indices[ano]
        parcial = (ano == primer_dia.year and (primer_dia.month, primer_dia.day) != (1, 1)) or (
            ano == ultimo_dia.year and (ultimo_dia.month, ultimo_dia.day) != (12, 31)
        )
        fila = {
            "ano": ano,
            "cartera": _rendimiento_del_tramo(inicio_idx, fin_idx),
            "parcial": parcial,
            "benchmarks": {},
        }
        for clave, (etiqueta, cierres) in series_bench.items():
            primero, ultimo = cierres.get(ano, (None, None))
            # La clave se pone SIEMPRE, aunque el índice no tenga datos de ese
            # año: la tabla saca las columnas de una fila cualquiera, y si unos
            # años traían índice y otros no, la cabecera y el cuerpo dejaban de
            # cuadrar y los porcentajes salían bajo la columna equivocada.
            fila["benchmarks"][clave] = {
                "label": etiqueta,
                "pct": _rendimiento_del_tramo(primero, ultimo) if primero else None,
            }
        filas.append(fila)
    return filas
