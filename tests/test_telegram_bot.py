"""Bot de Telegram: interpretación de mensajes, botones de confirmación y resumen.
Sin red: FX parcheado y sin llamadas a la API de Telegram."""
from datetime import date, timedelta

import pytest

from app.models import (
    Asset, AssetType, Currency, Operation, OperationType, PriceHistory, Transaction,
    TransactionStatus, TransactionType,
)
from app.services import market_data
from app.services.telegram_bot import (
    DIAS_SEMANA, build_summary, handle_callback, process_text,
)


@pytest.fixture
def bitcoin(db):
    asset = Asset(name="Bitcoin", asset_type=AssetType.CRIPTO, ticker="bitcoin", currency=Currency.EUR)
    db.add(asset)
    db.commit()
    return asset


def test_texto_de_compra_crea_operacion_pendiente(db, bitcoin):
    reply, markup = process_text(db, "compré 0,5 bitcoin a 54.000")
    op = db.query(Operation).one()
    assert op.status == TransactionStatus.PENDIENTE
    assert op.type == OperationType.COMPRA
    assert op.quantity == pytest.approx(0.5)
    assert op.unit_price == pytest.approx(54000)
    assert op.source == "telegram"
    assert "Compra" in reply and "Bitcoin" in reply
    assert markup["inline_keyboard"][0][0]["callback_data"] == "op:ok:%d" % op.id


def test_texto_de_gasto_crea_transaccion_pendiente(db):
    reply, markup = process_text(db, "gasté 25 euros en comida ayer")
    tx = db.query(Transaction).one()
    assert tx.status == TransactionStatus.PENDIENTE
    assert tx.type == TransactionType.GASTO
    assert tx.amount == pytest.approx(25)
    assert "Gasto" in reply
    assert markup["inline_keyboard"][0][1]["callback_data"] == "tx:no:%d" % tx.id


def test_texto_sin_importe_no_crea_nada(db):
    reply, markup = process_text(db, "hola qué tal")
    assert markup is None
    assert db.query(Transaction).count() == 0


def test_boton_confirmar_aplica_la_operacion(db, bitcoin):
    process_text(db, "compré 0,5 bitcoin a 54.000")
    op = db.query(Operation).one()
    result = handle_callback(db, "op:ok:%d" % op.id)
    assert op.status == TransactionStatus.CONFIRMADO
    assert "aplicada" in result.lower()


def test_boton_rechazar_borra_la_operacion(db, bitcoin):
    process_text(db, "compré 0,5 bitcoin a 54.000")
    op = db.query(Operation).one()
    result = handle_callback(db, "op:no:%d" % op.id)
    assert db.query(Operation).count() == 0
    assert "descartado" in result.lower()


def test_boton_sobre_item_inexistente_avisa(db):
    assert "no existe" in handle_callback(db, "op:ok:999").lower()
    assert "no reconocido" in handle_callback(db, "garbage").lower()


def test_resumen_incluye_patrimonio_movimientos_y_eurusd(db, bitcoin, monkeypatch):
    # FX parcheado: EUR->USD 1,12 hoy; cierre de ayer 1/0,90 -> el euro se revaloriza
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0 if a == b else 1.12)
    db.add(PriceHistory(symbol="FX:USD:EUR", date=date.today() - timedelta(days=1), price=0.90))
    bitcoin.current_price = 55000.0
    bitcoin.previous_close = 50000.0
    db.add(Operation(
        asset_id=bitcoin.id, type=OperationType.COMPRA, date=date(2026, 1, 10),
        quantity=0.5, unit_price=40000, status=TransactionStatus.CONFIRMADO,
    ))
    db.commit()
    text = build_summary(db)
    # Se comprueban las CIFRAS, no la redacción: el resumen se ha reformateado
    # en cajas y comprobar rótulos concretos rompía el test sin que nada
    # estuviera mal.
    assert "27.500,00" in text          # patrimonio: 0,5 × 55.000
    assert "P&amp;L" in text or "P&L" in text
    assert "Bitcoin" in text            # mover del día (+10%)
    assert "+10,00%" in text
    # El formato en cajas abrevia el par y ya no incluye la frase larga
    # ("el euro se revaloriza...") ni el % del día: no cabían en el ancho.
    assert "€/$ 1,1200" in text


def test_el_resumen_agrupa_la_cartera_por_tipo(db, bitcoin, monkeypatch):
    """La cartera se reparte en ETFs / Acciones / Crypto. Crypto sale del tipo
    de activo; ETF vs acción es heurística por el nombre, porque el modelo no
    los distingue."""
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)
    bitcoin.current_price = 55000.0
    db.add(Operation(
        asset_id=bitcoin.id, type=OperationType.COMPRA, date=date(2026, 1, 10),
        quantity=0.5, unit_price=40000, status=TransactionStatus.CONFIRMADO,
    ))
    etf = Asset(name="iShares Core MSCI World", asset_type=AssetType.ACCION,
                currency=Currency.EUR, ticker="IWDA.AS", current_price=100.0)
    accion = Asset(name="Accenture plc", asset_type=AssetType.ACCION,
                   currency=Currency.EUR, ticker="ACN", current_price=300.0)
    db.add_all([etf, accion])
    db.flush()
    for activo, cantidad in ((etf, 10), (accion, 5)):
        db.add(Operation(asset_id=activo.id, type=OperationType.COMPRA,
                         date=date(2026, 1, 10), quantity=cantidad, unit_price=90.0,
                         status=TransactionStatus.CONFIRMADO))
    db.commit()

    text = build_summary(db)

    assert "ETF" in text and "Acciones" in text and "Crypto" in text
    # Accenture no puede caer en ETFs por contener "Acc"
    etfs, acciones = text.split("Acciones", 1)
    assert "iShares Core MSCI World" in etfs
    assert "Accenture" in acciones


def test_el_titulo_del_aviso_encabeza_el_resumen(db):
    """Los cinco avisos diarios tienen que distinguirse; sin título, el día."""
    assert "CIERRE DE EE. UU." in build_summary(db, "Cierre de EE. UU.")
    # Sin título, el día de la semana en español (no "FRIDAY": el contenedor va
    # en locale C y strftime("%A") devolvía inglés)
    assert DIAS_SEMANA[date.today().weekday()].upper() in build_summary(db)


def test_gasto_en_dolares_se_convierte_a_la_base(db, monkeypatch):
    """La divisa dictada no se puede ignorar: 20 USD no son 20 EUR."""
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.90)

    reply, markup = process_text(db, "gasté 20 dólares en comida")

    tx = db.query(Transaction).one()
    assert tx.amount == pytest.approx(18.0)  # 20 USD * 0,90
    assert "20,00 USD" in reply  # el resumen enseña también el importe original
    assert markup is not None


def test_gasto_en_euros_no_se_toca(db, monkeypatch):
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.90)

    process_text(db, "gasté 20 euros en comida")

    assert db.query(Transaction).one().amount == pytest.approx(20.0)


def test_sin_tipo_de_cambio_no_se_apunta_nada(db, monkeypatch):
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: None)

    reply, markup = process_text(db, "gasté 20 dólares en comida")

    assert db.query(Transaction).count() == 0
    assert markup is None
    assert "tipo de cambio" in reply
