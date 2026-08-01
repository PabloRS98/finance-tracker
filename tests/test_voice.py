"""Parser de voz para operaciones de inversión."""
from datetime import date, timedelta

import pytest

from app.models import Asset, AssetType, Currency
from app.services.voice_parser import parse_voice_operation


@pytest.fixture
def db_con_activos(db):
    db.add(Asset(name="Bitcoin", asset_type=AssetType.CRIPTO, currency=Currency.EUR, ticker="bitcoin"))
    db.add(Asset(name="Apple", asset_type=AssetType.ACCION, currency=Currency.USD, ticker="AAPL"))
    db.commit()
    return db


def test_compra_con_decimales_y_miles(db_con_activos):
    op = parse_voice_operation("compré 0,5 bitcoin a 54.000", db_con_activos)
    assert op is not None and op["error"] is None
    assert op["type"] == "compra"
    assert op["asset"].name == "Bitcoin"
    assert op["quantity"] == pytest.approx(0.5)
    assert op["unit_price"] == pytest.approx(54000)


def test_venta_con_fecha_relativa(db_con_activos):
    op = parse_voice_operation("vendí 2 apple a 300 el lunes pasado", db_con_activos)
    assert op is not None and op["error"] is None
    assert op["type"] == "venta"
    assert op["quantity"] == pytest.approx(2)
    assert op["unit_price"] == pytest.approx(300)
    assert op["date"] < date.today()
    assert op["date"] >= date.today() - timedelta(days=7)
    assert op["date"].weekday() == 0


def test_gasto_normal_no_es_operacion(db_con_activos):
    assert parse_voice_operation("gasté 20 euros en el supermercado", db_con_activos) is None


def test_compra_sin_activo_conocido_no_es_operacion(db_con_activos):
    assert parse_voice_operation("compré una lavadora por 400 euros", db_con_activos) is None


def test_compra_sin_precio_devuelve_error(db_con_activos):
    op = parse_voice_operation("compré 1 bitcoin", db_con_activos)
    assert op is not None
    assert op["error"] is not None and "precio" in op["error"]


def test_keyword_de_categoria_casa_palabra_completa(db):
    from app.models import Category
    from app.services.voice_parser import guess_category

    db.add(Category(name="Vivienda", keywords="alquiler,gas,luz"))
    db.add(Category(name="Comida", keywords="supermercado"))
    db.commit()
    # "gasté" no debe casar con la keyword "gas"
    assert guess_category("gasté 15 euros en el supermercado", db).name == "Comida"
    assert guess_category("factura del gas de enero", db).name == "Vivienda"
