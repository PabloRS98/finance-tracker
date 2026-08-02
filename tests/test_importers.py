"""Parsers de CSV de brokers: columnas tolerantes, tipos omitidos, huella estable."""
from datetime import date

import pytest

from app.services.importers import generic, okx, revolut, trade_republic, to_float, to_date, looks_like_isin

TR_CSV = """Fecha;Tipo;Valor;Nota;ISIN;Cantidad;Comisión
2025-11-03;Compra;-500,00;Fondo Mundial Ejemplo;IE00EJEMPLO1;4,2;1,00
2026-01-15;Venta;250,00;Fondo Mundial Ejemplo;IE00EJEMPLO1;-2;1,00
2026-02-01;Dividendo;3,50;Fondo Mundial Ejemplo;IE00EJEMPLO1;;
2026-02-05;Depósito;100,00;;;;
"""

# Export con el ISIN en la columna "symbol" (no "isin") y tipos verbosos
TR_SYMBOL_CSV = """date,type,name,symbol,shares,price,amount,fee,currency
2026-01-20,BUY,Fondo Global USD (Acc),IE00EJEMPLO2,2,100,-200,-1,EUR
2026-01-21,CUSTOMER_INPAYMENT,,,,,100,,EUR
2026-01-22,DIVIDEND,Fondo Global USD (Acc),IE00EJEMPLO2,,,0.5,,EUR
"""

REVOLUT_CSV = """Date,Ticker,Type,Quantity,Price per share,Total Amount,Currency,FX Rate
2026-01-10T14:30:00Z,AAPL,BUY - MARKET,2,180.50,361.00,USD,1.08
2026-03-01T09:00:00Z,,CASH TOP-UP,,,100.00,USD,
2026-03-02T09:00:00Z,VUAA,BUY - MARKET,3,95.00,285.00,GBP,0.85
"""

OKX_CSV = """Order Time,Instrument,Side,Filled Amount,Avg Price,Fee
2026-03-05 10:00:00,BTC-EUR,Buy,0.01,60000,0.6
2026-04-01 12:00:00,ETH-USDT,Sell,0.5,3000,1.5
2026-04-02 12:00:00,DOGE-BTC,Buy,100,0.0000012,0
"""

GENERIC_CSV = """fecha,tipo,nombre,ticker,isin,cantidad,precio,comision,divisa,clase
2026-05-01,compra,MicroStrategy,MSTR,,1,1500,2,USD,accion
"""


def test_trade_republic_deriva_precio_y_omite_no_operaciones():
    result = trade_republic.parse(TR_CSV)
    assert len(result.rows) == 2
    compra, venta = result.rows
    assert (compra.type, compra.isin) == ("compra", "IE00EJEMPLO1")
    assert compra.quantity == pytest.approx(4.2)
    assert compra.unit_price == pytest.approx(500 / 4.2)  # derivado de importe/cantidad
    assert compra.fee == pytest.approx(1.0)
    # La venta llega con cantidad negativa (-2): el signo lo da el tipo, no la cantidad
    assert (venta.type, venta.error) == ("venta", None)
    assert venta.quantity == pytest.approx(2.0)
    assert sum(result.skipped.values()) == 2  # dividendo + depósito


def test_trade_republic_lee_isin_de_columna_symbol():
    result = trade_republic.parse(TR_SYMBOL_CSV)
    assert len(result.rows) == 1  # solo el BUY; inpayment y dividendo se omiten
    compra = result.rows[0]
    assert (compra.type, compra.isin, compra.error) == ("compra", "IE00EJEMPLO2", None)
    assert compra.name == "Fondo Global USD (Acc)"
    assert compra.unit_price == pytest.approx(100)
    assert sum(result.skipped.values()) == 2


def test_looks_like_isin():
    assert looks_like_isin("IE00EJEMPLO2")
    assert looks_like_isin("us0378331005")  # normaliza mayúsculas
    assert not looks_like_isin("AAPL")
    assert not looks_like_isin("")


def test_revolut_marca_divisa_no_soportada():
    result = revolut.parse(REVOLUT_CSV)
    assert len(result.rows) == 2
    ok, gbp = result.rows
    assert ok.ticker == "AAPL" and ok.currency == "USD" and ok.error is None
    assert gbp.error and "GBP" in gbp.error
    assert result.skipped.get("movimiento de efectivo") == 1


def test_okx_separa_par_y_mapea_coingecko():
    result = okx.parse(OKX_CSV)
    assert len(result.rows) == 3
    btc, eth, doge = result.rows
    assert (btc.name, btc.ticker, btc.currency, btc.error) == ("BTC", "bitcoin", "EUR", None)
    assert (eth.type, eth.currency) == ("venta", "USD")  # USDT -> USD
    assert doge.error and "BTC" in doge.error  # par cotizado no soportado


def test_generic_y_huella_estable():
    result = generic.parse(GENERIC_CSV)
    assert len(result.rows) == 1
    row = result.rows[0]
    assert (row.type, row.ticker, row.currency, row.kind) == ("compra", "MSTR", "USD", "accion")
    assert row.import_hash() == generic.parse(GENERIC_CSV).rows[0].import_hash()


def test_to_float_formatos_mixtos():
    assert to_float("1.234,56") == pytest.approx(1234.56)
    assert to_float("1,234.56") == pytest.approx(1234.56)
    assert to_float("-12,5 €") == pytest.approx(-12.5)
    assert to_float("") is None


def test_to_date_formatos_mixtos():
    assert to_date("2026-03-01T10:20:30.123Z") == date(2026, 3, 1)
    assert to_date("15/01/2026") == date(2026, 1, 15)
    assert to_date("no-fecha") is None
