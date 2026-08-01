"""Valores en seguimiento: se vigila el precio pero no se tiene posición.

Van en tabla aparte y no como un Asset con bandera. Los activos entran en el
patrimonio, en las allocations, en el X-Ray y en la reconstrucción del
histórico: una bandera obligaría a acordarse de excluirlos en cada una de esas
consultas, y cualquier olvido inflaría el patrimonio con dinero que no tienes.
"""
import pytest

from app.models import Asset, AssetType, Currency, Operation, Watchlist


@pytest.fixture
def yahoo_reconoce(monkeypatch):
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_stock_price",
                        lambda t: {"price": 120.0, "currency": "USD", "previous_close": 100.0,
                                   "exchange": "NMS", "instrument_type": "EQUITY",
                                   "name": "MUESTRA Corporation"})


# ---------- Variación del día ----------

def test_la_variacion_del_dia_sale_del_cierre_anterior():
    item = Watchlist(ticker="NVDA", name="MUESTRA", asset_type=AssetType.ACCION,
                     current_price=110.0, previous_close=100.0)

    assert item.day_change_pct() == pytest.approx(10.0)


@pytest.mark.parametrize("precio,cierre", [(None, 100.0), (110.0, None), (110.0, 0.0)])
def test_sin_datos_no_se_inventa_variacion(precio, cierre):
    item = Watchlist(ticker="X", name="X", asset_type=AssetType.ACCION,
                     current_price=precio, previous_close=cierre)

    assert item.day_change_pct() is None


# ---------- Alta ----------

def test_seguir_un_valor_resuelve_nombre_y_divisa(client, yahoo_reconoce):
    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)

    item = client.db.query(Watchlist).one()
    assert item.name == "MUESTRA Corporation"
    assert item.currency == Currency.USD
    assert item.day_change_pct() == pytest.approx(20.0)


def test_un_ticker_que_no_existe_se_rechaza(client, monkeypatch):
    from app.services import market_data
    monkeypatch.setattr(market_data, "get_stock_price", lambda t: None)

    client.post_form("/activos/seguimiento", data={"ticker": "NOEXISTE"}, follow_redirects=False)

    assert client.db.query(Watchlist).count() == 0


def test_no_se_sigue_dos_veces_el_mismo(client, yahoo_reconoce):
    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)
    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)

    assert client.db.query(Watchlist).count() == 1


# ---------- Lo seguido NO es patrimonio ----------

def test_lo_seguido_no_cuenta_en_el_patrimonio(client, yahoo_reconoce):
    """Es la razón de que sea una tabla aparte: seguir un valor no puede sumar
    ni un euro al patrimonio ni aparecer en las allocations."""
    from app.services.scheduler import compute_net_worth

    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)

    assert compute_net_worth(client.db).total == 0.0
    assert client.db.query(Asset).count() == 0


# ---------- Pasar a cartera ----------

def test_pasar_a_cartera_crea_el_activo_y_lo_saca_de_seguimiento(client, yahoo_reconoce):
    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)
    item = client.db.query(Watchlist).one()

    respuesta = client.post_form("/activos/seguimiento/%d/comprar" % item.id,
                                 follow_redirects=False)

    asset = client.db.query(Asset).one()
    assert asset.ticker == "NVDA"
    assert asset.currency == Currency.USD
    assert asset.current_price == 120.0
    assert client.db.query(Watchlist).count() == 0
    # Lleva al alta de la operación, ya filtrada por el activo
    assert respuesta.headers["location"] == "/operaciones?activo=%d" % asset.id


def test_pasar_a_cartera_no_duplica_un_activo_que_ya_existe(client, yahoo_reconoce):
    """Si mientras tanto se dio de alta por otro lado, se reutiliza en vez de
    crear un segundo activo con el mismo ticker."""
    client.db.add(Asset(name="MUESTRA", asset_type=AssetType.ACCION,
                        currency=Currency.USD, ticker="NVDA"))
    client.db.commit()
    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)
    item = client.db.query(Watchlist).one()

    client.post_form("/activos/seguimiento/%d/comprar" % item.id, follow_redirects=False)

    assert client.db.query(Asset).count() == 1
    assert client.db.query(Watchlist).count() == 0


def test_pasar_a_cartera_no_inventa_posicion(client, yahoo_reconoce):
    """El activo se crea sin cantidad: la posición sale de las operaciones."""
    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)
    item = client.db.query(Watchlist).one()

    client.post_form("/activos/seguimiento/%d/comprar" % item.id, follow_redirects=False)

    asset = client.db.query(Asset).one()
    assert asset.quantity is None
    assert client.db.query(Operation).count() == 0
    assert asset.current_value() == 0.0


# ---------- Baja ----------

def test_dejar_de_seguir(client, yahoo_reconoce):
    client.post_form("/activos/seguimiento", data={"ticker": "NVDA"}, follow_redirects=False)
    item = client.db.query(Watchlist).one()

    client.post_form("/activos/seguimiento/%d/eliminar" % item.id, follow_redirects=False)

    assert client.db.query(Watchlist).count() == 0


# ---------- Refresco de precios ----------

def test_el_job_de_precios_refresca_lo_seguido(db, monkeypatch):
    from app.services import market_data
    from app.services.scheduler import update_watchlist_prices

    monkeypatch.setattr(market_data, "get_stock_price",
                        lambda t: {"price": 150.0, "currency": "USD", "previous_close": 140.0,
                                   "exchange": "NMS", "instrument_type": "EQUITY", "name": "MUESTRA"})
    db.add(Watchlist(ticker="NVDA", name="MUESTRA", asset_type=AssetType.ACCION,
                     currency=Currency.USD, current_price=1.0, previous_close=1.0))
    db.commit()

    assert update_watchlist_prices(db) == 1

    item = db.query(Watchlist).one()
    assert item.current_price == 150.0
    assert item.last_price_update is not None
