"""Auto-clasificación de región/sector: heurística por nombre, fill-if-empty."""
import pytest

from app.models import Asset, AssetType, Currency
from app.services import classify


# Nombres reales de fondos/acciones → región esperada
@pytest.mark.parametrize("nombre, region", [
    ("iShares Core MSCI World UCITS ETF", "Global"),
    ("Vanguard FTSE All-World USD (Acc)", "Global"),
    ("Vanguard S&P 500 UCITS ETF", "EE. UU."),
    ("Invesco Nasdaq-100", "EE. UU."),
    ("Xtrackers EuroStoxx 50", "Europa"),
    ("iShares STOXX 600", "Europa"),
    ("Amundi IBEX 35", "Europa"),
    ("iShares MSCI Emerging Markets", "Emergentes"),
    ("MSCI Emerging Markets Asia", "Emergentes"),  # emergentes gana a Asia
    ("iShares Nikkei 225", "Japón"),
    ("Xtrackers MSCI Japan", "Japón"),
    ("iShares MSCI China A", "China"),
    ("Hang Seng Index ETF", "China"),
    ("iShares MSCI Pacific ex-Japan", "Asia"),
    ("Telefónica", None),  # sin pista en el nombre
])
def test_region_por_nombre(nombre, region):
    assert classify.region_from_name(nombre) == region


def test_sector_indice_amplio_es_diversificado():
    assert classify.sector_from_name("iShares Core MSCI World UCITS ETF") == "Diversificado"


def test_sector_indice_tecnologico():
    assert classify.sector_from_name("Invesco Nasdaq-100") == "Tecnología"


def test_sector_accion_suelta_sin_pista():
    assert classify.sector_from_name("Microsoft Corporation") is None


def test_etf_exige_palabra_completa():
    # "nETFlix" contiene "etf": el patrón debe exigir palabra completa
    assert classify.sector_from_name("Netflix, Inc.") is None


def test_no_pisa_valores_manuales():
    a = Asset(name="Vanguard S&P 500", asset_type=AssetType.ACCION,
              currency=Currency.USD, region="Europa", sector="Consumo")
    changed = classify.autofill(a)
    assert a.region == "Europa"
    assert a.sector == "Consumo"
    assert changed is False


def test_rellena_solo_huecos():
    a = Asset(name="Vanguard S&P 500", asset_type=AssetType.ACCION,
              currency=Currency.USD, region=None, sector="Consumo")
    classify.autofill(a)
    assert a.region == "EE. UU."
    assert a.sector == "Consumo"  # el manual se conserva


def test_cripto_global_cripto():
    a = Asset(name="Bitcoin", asset_type=AssetType.CRIPTO, currency=Currency.EUR)
    assert classify.autofill(a) is True
    assert (a.region, a.sector) == ("Global", "Cripto")


def test_cuenta_no_se_clasifica():
    a = Asset(name="Cuenta nómina", asset_type=AssetType.CUENTA, currency=Currency.EUR)
    assert classify.autofill(a) is False
    assert a.region is None


def test_yahoo_sector_mapea_a_espanol(monkeypatch):
    monkeypatch.setattr(classify.market_data, "_yahoo_search",
                        lambda q, n: [{"symbol": "AAPL", "sector": "Technology"}])
    assert classify.yahoo_sector("AAPL") == "Tecnología"


def test_yahoo_sector_sin_campo_devuelve_none(monkeypatch):
    monkeypatch.setattr(classify.market_data, "_yahoo_search",
                        lambda q, n: [{"symbol": "AAPL"}])
    assert classify.yahoo_sector("AAPL") is None


def test_exposicion_por_nombre_clase_usd():
    a = Asset(name="Fondo Global USD (Acc)", asset_type=AssetType.ACCION, currency=Currency.EUR)
    classify.autofill(a)
    assert a.exposure_currency == "USD"


def test_exposicion_cross_listing_usa(monkeypatch):
    # Acción individual USA cotizada en EUR (Frankfurt): exposición USD
    monkeypatch.setattr(classify.market_data, "_yahoo_search", lambda q, n: [])
    a = Asset(name="Alfabeto (A)", asset_type=AssetType.ACCION, currency=Currency.EUR,
              region="EE. UU.", ticker="ALFA.DE")
    classify.autofill(a, {"instrument_type": "EQUITY", "exchange": "GER"})
    assert a.exposure_currency == "USD"


def test_exposicion_no_para_clase_eur():
    a = Asset(name="Fondo Índice Mundial P EUR Acc", asset_type=AssetType.ACCION, currency=Currency.EUR)
    classify.autofill(a)
    assert a.exposure_currency is None


def test_exposicion_no_pisa_manual():
    a = Asset(name="Core S&P 500 USD (Acc)", asset_type=AssetType.ACCION, currency=Currency.EUR,
              exposure_currency="GBP")
    classify.autofill(a)
    assert a.exposure_currency == "GBP"


def test_exposicion_solo_si_cotiza_en_base():
    # En divisa extranjera la propia cotización ya da el efecto divisa
    a = Asset(name="Algo USD", asset_type=AssetType.ACCION, currency=Currency.USD)
    classify.autofill(a)
    assert a.exposure_currency is None


def test_region_fallback_por_exchange(monkeypatch):
    # Sin pista en el nombre y con meta de Yahoo: usa el exchange (HKG → China)
    monkeypatch.setattr(classify.market_data, "_yahoo_search", lambda q, n: [])
    a = Asset(name="Ejemplo Corp", asset_type=AssetType.ACCION,
              currency=Currency.HKD, ticker="1810.HK")
    classify.autofill(a, {"instrument_type": "EQUITY", "exchange": "HKG"})
    assert a.region == "China"
