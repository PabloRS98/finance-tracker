"""Índices de referencia configurables desde la interfaz.

Antes eran dos constantes en el código (`IWDA.AS` y `^GSPC`), así que seguir el
IBEX o el ETF concreto de tu plan de pensiones obligaba a tocar el código y
reconstruir la imagen.
"""
import pytest

from app.models import Benchmark, PriceHistory
from app.services.history import benchmark_series, clave_de_simbolo

# ---------- Clave a partir del símbolo ----------

@pytest.mark.parametrize("symbol,esperado", [
    ("^GSPC", "gspc"),
    ("IWDA.AS", "iwda_as"),
    ("VWCE.DE", "vwce_de"),
    ("^IBEX", "ibex"),
])
def test_la_clave_se_deriva_del_simbolo(symbol, esperado):
    """La usan el selector del dashboard y las columnas de la tabla anual, así
    que tiene que sobrevivir a que se renombre la etiqueta."""
    assert clave_de_simbolo(symbol) == esperado


def test_un_simbolo_sin_letras_ni_numeros_no_deja_la_clave_vacia():
    assert clave_de_simbolo("^^^") == "benchmark"


# ---------- Series ----------

def test_la_serie_sale_de_los_indices_dados_de_alta(db):
    db.add(Benchmark(clave="ibex", label="IBEX 35", symbol="^IBEX"))
    db.add(PriceHistory(symbol="^IBEX", date=__import__("datetime").date(2026, 1, 2), price=11000.0))
    db.commit()

    series = benchmark_series(db)

    assert list(series) == ["ibex"]
    assert series["ibex"]["label"] == "IBEX 35"
    assert series["ibex"]["points"] == [{"fecha": "2026-01-02", "close": 11000.0}]


def test_sin_indices_dados_de_alta_no_hay_series(db):
    assert benchmark_series(db) == {}


# ---------- Alta y baja ----------

@pytest.fixture
def yahoo_reconoce(monkeypatch):
    """Yahoo responde a cualquier símbolo, con nombre largo."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_stock_price",
                        lambda s: {"price": 1.0, "currency": "EUR", "previous_close": 1.0,
                                   "exchange": "MCE", "instrument_type": "INDEX",
                                   "name": "IBEX 35 Index"})


def test_dar_de_alta_un_indice(client, yahoo_reconoce):
    respuesta = client.post_form("/analisis/benchmarks", data={"symbol": "^ibex"},
                                 follow_redirects=False)

    assert respuesta.status_code == 303
    creado = client.db.query(Benchmark).filter(Benchmark.symbol == "^IBEX").one()
    assert creado.clave == "ibex"
    assert creado.label == "IBEX 35 Index"  # el nombre lo pone Yahoo si no lo das


def test_el_nombre_propio_gana_al_de_yahoo(client, yahoo_reconoce):
    client.post_form("/analisis/benchmarks",
                     data={"symbol": "^IBEX", "label": "Mi índice"}, follow_redirects=False)

    assert client.db.query(Benchmark).one().label == "Mi índice"


def test_un_simbolo_que_yahoo_no_conoce_se_rechaza(client, monkeypatch):
    """Sin comprobarlo se guardaría un índice que nunca tendrá serie, y saldría
    como una columna vacía en la tabla anual sin explicar por qué."""
    from app.services import market_data
    monkeypatch.setattr(market_data, "get_stock_price", lambda s: None)

    client.post_form("/analisis/benchmarks", data={"symbol": "NOEXISTE"},
                     follow_redirects=False)

    assert client.db.query(Benchmark).count() == 0


def test_no_se_puede_seguir_dos_veces_el_mismo(client, yahoo_reconoce):
    client.post_form("/analisis/benchmarks", data={"symbol": "^IBEX"}, follow_redirects=False)
    client.post_form("/analisis/benchmarks", data={"symbol": "^ibex"}, follow_redirects=False)

    assert client.db.query(Benchmark).count() == 1


def test_dar_de_baja_borra_tambien_su_historico(client, yahoo_reconoce):
    """Si se quedaran los cierres, ocuparían sitio sin que nada los lea y al
    volver a añadirlo traerían datos rancios."""
    from datetime import date

    client.post_form("/analisis/benchmarks", data={"symbol": "^IBEX"}, follow_redirects=False)
    bench = client.db.query(Benchmark).one()
    client.db.add(PriceHistory(symbol="^IBEX", date=date(2026, 1, 2), price=11000.0))
    client.db.commit()

    client.post_form("/analisis/benchmarks/%d/eliminar" % bench.id, follow_redirects=False)

    assert client.db.query(Benchmark).count() == 0
    assert client.db.query(PriceHistory).filter(PriceHistory.symbol == "^IBEX").count() == 0
