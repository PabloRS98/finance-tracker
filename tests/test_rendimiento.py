"""XIRR y rendimiento por año natural.

El TWR que ya había mide la estrategia: encadena rendimientos diarios y
descuenta las aportaciones, así que no cambia según cuándo metieras el dinero.
El XIRR sí, porque pondera cada aportación por el tiempo que ha estado
trabajando. Son las dos caras y por eso se enseñan juntas.
"""
from datetime import date, timedelta

import pytest

from app.services.rendimiento import (
    flujos_desde_evolucion,
    rendimiento_por_ano,
    xirr,
    xirr_de_la_cartera,
)


def _punto(fecha: str, invertido: float, aportado: float, twr: float) -> dict:
    return {"fecha": fecha, "invertido": invertido, "aportado": aportado,
            "twr": twr, "total": invertido}


# ---------- XIRR ----------

def test_duplicar_en_un_ano_es_100_por_ciento():
    resultado = xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 2000.0)])

    assert resultado == pytest.approx(100.0, abs=0.5)


def test_no_ganar_nada_es_cero():
    resultado = xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 1000.0)])

    assert resultado == pytest.approx(0.0, abs=0.01)


def test_perder_la_mitad_en_un_ano_es_menos_50():
    resultado = xirr([(date(2025, 1, 1), -1000.0), (date(2026, 1, 1), 500.0)])

    assert resultado == pytest.approx(-50.0, abs=0.5)


def test_se_anualiza_el_periodo_corto():
    """+10% en medio año es más de un 20% anual (interés compuesto)."""
    resultado = xirr([(date(2025, 1, 1), -1000.0), (date(2025, 7, 2), 1100.0)])

    assert 20.0 < resultado < 22.0


def test_aportar_tarde_pesa_menos_que_aportar_pronto():
    """Mismo dinero y mismo valor final: quien aportó el grueso al final tiene
    mejor XIRR, porque su dinero estuvo menos tiempo expuesto para el mismo
    resultado. Es justo lo que el TWR no distingue."""
    pronto = xirr([(date(2025, 1, 1), -900.0), (date(2025, 11, 1), -100.0),
                   (date(2026, 1, 1), 1200.0)])
    tarde = xirr([(date(2025, 1, 1), -100.0), (date(2025, 11, 1), -900.0),
                  (date(2026, 1, 1), 1200.0)])

    assert tarde > pronto


def test_sin_flujos_de_los_dos_signos_no_hay_solucion():
    assert xirr([(date(2025, 1, 1), -100.0), (date(2026, 1, 1), -100.0)]) is None
    assert xirr([]) is None
    assert xirr([(date(2025, 1, 1), -100.0)]) is None


def test_todo_el_mismo_dia_no_se_puede_anualizar():
    assert xirr([(date(2025, 1, 1), -100.0), (date(2025, 1, 1), 120.0)]) is None


# ---------- Flujos a partir de la serie de evolución ----------

def test_los_flujos_salen_de_las_diferencias_de_aportado():
    evolucion = [
        _punto("2025-01-01", 1000, 1000, 0.0),
        _punto("2025-01-02", 1010, 1000, 1.0),   # solo revalorización: sin flujo
        _punto("2025-01-03", 1510, 1500, 1.0),   # aportación de 500
    ]

    flujos = flujos_desde_evolucion(evolucion)

    assert flujos == [
        (date(2025, 1, 1), -1000.0),
        (date(2025, 1, 3), -500.0),
        (date(2025, 1, 3), 1510.0),   # valor actual, como cobro final
    ]


def test_la_revalorizacion_sola_no_genera_flujo():
    evolucion = [_punto("2025-01-01", 1000, 1000, 0.0),
                 _punto("2025-06-01", 1300, 1000, 30.0)]

    aportaciones = [f for f in flujos_desde_evolucion(evolucion) if f[1] < 0]

    assert aportaciones == [(date(2025, 1, 1), -1000.0)]


def test_xirr_de_la_cartera_sobre_una_serie_de_un_ano():
    hoy = date(2026, 1, 1)
    evolucion = [_punto((hoy - timedelta(days=365)).isoformat(), 1000, 1000, 0.0),
                 _punto(hoy.isoformat(), 2000, 1000, 100.0)]

    assert xirr_de_la_cartera(evolucion) == pytest.approx(100.0, abs=0.5)


def test_una_venta_total_deja_el_valor_final_fuera():
    """Si no queda posición, el cobro final es la propia venta: `aportado` baja
    y `invertido` queda a cero, así que no se añade valor de mercado."""
    evolucion = [_punto("2025-01-01", 1000, 1000, 0.0),
                 _punto("2025-12-31", 0, -200, 20.0)]

    flujos = flujos_desde_evolucion(evolucion)

    assert flujos == [(date(2025, 1, 1), -1000.0), (date(2025, 12, 31), 1200.0)]


# ---------- Rendimiento por año ----------

def test_cada_ano_arranca_en_el_cierre_del_anterior():
    """Si el año partiera de su primer día, el salto de fin de diciembre a
    primeros de enero no se lo apuntaría ningún año."""
    evolucion = [
        _punto("2024-06-01", 100, 100, 0.0),
        _punto("2024-12-31", 110, 100, 10.0),
        _punto("2025-01-01", 110, 100, 10.0),
        _punto("2025-12-31", 132, 100, 32.0),
    ]

    filas = rendimiento_por_ano(evolucion)

    por_ano = {f["ano"]: f["cartera"] for f in filas}
    assert por_ano[2024] == pytest.approx(10.0)
    # 1.32 / 1.10 - 1 = 20%
    assert por_ano[2025] == pytest.approx(20.0)


def test_los_anos_incompletos_se_marcan():
    evolucion = [
        _punto("2024-06-01", 100, 100, 0.0),
        _punto("2024-12-31", 110, 100, 10.0),
        _punto("2025-01-01", 110, 100, 10.0),
        _punto("2025-07-15", 121, 100, 21.0),
    ]

    filas = {f["ano"]: f for f in rendimiento_por_ano(evolucion)}

    assert filas[2024]["parcial"] is True   # empezó en junio
    assert filas[2025]["parcial"] is True   # aún no ha acabado


def test_el_indice_se_calcula_sobre_el_mismo_criterio():
    evolucion = [_punto("2025-01-01", 100, 100, 0.0),
                 _punto("2025-12-31", 110, 100, 10.0)]
    benchmarks = {"sp500": {"label": "S&P 500", "points": [
        {"fecha": "2025-01-01", "close": 200.0},
        {"fecha": "2025-12-31", "close": 260.0},
    ]}}

    fila = rendimiento_por_ano(evolucion, benchmarks)[0]

    assert fila["cartera"] == pytest.approx(10.0)
    assert fila["benchmarks"]["sp500"]["pct"] == pytest.approx(30.0)
    assert fila["benchmarks"]["sp500"]["label"] == "S&P 500"


def test_todas_las_filas_traen_las_mismas_columnas_de_indice():
    """La tabla saca las columnas de una fila cualquiera: si unos años trajeran
    índice y otros no, cabecera y cuerpo dejarían de cuadrar y los porcentajes
    saldrían bajo la columna equivocada. Pasó con datos reales, porque las
    series de los índices no llegan tan atrás como la primera operación."""
    evolucion = [
        _punto("2024-01-01", 100, 100, 0.0),
        _punto("2024-12-31", 110, 100, 10.0),
        _punto("2025-01-01", 110, 100, 10.0),
        _punto("2025-12-31", 121, 100, 21.0),
    ]
    # El índice solo tiene datos de 2025
    benchmarks = {"sp500": {"label": "S&P 500", "points": [
        {"fecha": "2025-01-02", "close": 100.0},
        {"fecha": "2025-12-31", "close": 115.0},
    ]}}

    filas = rendimiento_por_ano(evolucion, benchmarks)

    assert [sorted(f["benchmarks"]) for f in filas] == [["sp500"], ["sp500"]]
    por_ano = {f["ano"]: f["benchmarks"]["sp500"]["pct"] for f in filas}
    assert por_ano[2024] is None      # sin datos: hueco, pero la columna existe
    assert por_ano[2025] == pytest.approx(15.0)


def test_sin_exposicion_no_hay_tabla():
    assert rendimiento_por_ano([_punto("2025-01-01", 0, 0, 0.0)]) == []
    assert rendimiento_por_ano([]) == []
