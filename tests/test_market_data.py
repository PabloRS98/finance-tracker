"""Normalización de cotizaciones de Yahoo (peniques GBp) y helpers de mercado."""
import pytest

from app.services.market_data import normalize_quote_currency, parse_yahoo_intraday


def test_normaliza_gbp_peniques():
    price, cur = normalize_quote_currency(4520.0, "GBp")
    assert cur == "GBP"
    assert price == pytest.approx(45.20)


def test_gbx_tambien_son_peniques():
    price, cur = normalize_quote_currency(4520.0, "GBX")
    assert cur == "GBP"
    assert price == pytest.approx(45.20)


def test_gbp_libras_no_se_escala():
    price, cur = normalize_quote_currency(45.2, "GBP")
    assert cur == "GBP"
    assert price == pytest.approx(45.2)


def test_divisa_normal_intacta():
    assert normalize_quote_currency(17.0, "HKD") == (17.0, "HKD")


def test_sin_divisa_asume_usd():
    assert normalize_quote_currency(10.0, None) == (10.0, "USD")


def test_precio_none_se_respeta():
    price, cur = normalize_quote_currency(None, "GBp")
    assert price is None
    assert cur == "GBP"


# ---------- Intradía ----------

def test_parse_yahoo_intraday():
    # Huecos None fuera, y peniques de Londres escalados a libras
    result = {
        "meta": {"currency": "GBp"},
        "timestamp": [1751871600, 1751871900, 1751872200],
        "indicators": {"quote": [{"close": [4520.0, None, 4530.0]}]},
    }
    points = parse_yahoo_intraday(result)
    assert len(points) == 2  # el None desaparece
    assert points[0][1] == pytest.approx(45.20)
    assert points[1][1] == pytest.approx(45.30)
    assert points[0][0].tzinfo is not None  # timestamps UTC aware


def test_parse_yahoo_intraday_vacio():
    assert parse_yahoo_intraday({}) == []
