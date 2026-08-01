"""Rebalanceo: desviación frente a los pesos objetivo y reparto de una aportación.

No propone ventas a propósito: vender para rebalancear cristaliza plusvalías y su
peaje fiscal. La vía por defecto es comprar lo que falta con dinero nuevo.
"""
from datetime import date

import pytest

from app.models import (
    Asset, AssetType, Currency, Operation, OperationType, PesoObjetivo, TransactionStatus,
)
from app.services.rebalanceo import plan, reparto_de_aportacion


@pytest.fixture
def cartera(db, monkeypatch):
    """Dos activos: 7.500 y 2.500 € -> 75% / 25%."""
    from app.services import market_data
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)

    mundo = Asset(name="MSCI World", asset_type=AssetType.ACCION,
                  currency=Currency.EUR, ticker="IWDA.AS", current_price=100.0)
    emergentes = Asset(name="Emergentes", asset_type=AssetType.ACCION,
                       currency=Currency.EUR, ticker="EMIM.AS", current_price=100.0)
    db.add_all([mundo, emergentes])
    db.flush()
    for activo, unidades in ((mundo, 75), (emergentes, 25)):
        db.add(Operation(asset_id=activo.id, type=OperationType.COMPRA, date=date(2026, 1, 1),
                         quantity=unidades, unit_price=100.0, status=TransactionStatus.CONFIRMADO))
    db.commit()
    return db, mundo, emergentes


def _objetivo(db, asset, pct):
    db.add(PesoObjetivo(asset_id=asset.id, porcentaje=pct))
    db.commit()


# ---------- Desviación ----------

def test_sin_objetivos_no_hay_plan(cartera):
    db, _, _ = cartera

    detalle = plan(db)

    assert detalle["filas"] == []
    assert detalle["total_actual"] == 10000.0


def test_calcula_la_desviacion(cartera):
    """La cartera está 75/25 y el objetivo es 60/40."""
    db, mundo, emergentes = cartera
    _objetivo(db, mundo, 60)
    _objetivo(db, emergentes, 40)

    filas = {f["asset"].name: f for f in plan(db)["filas"]}

    assert filas["MSCI World"]["actual_pct"] == pytest.approx(75.0)
    assert filas["MSCI World"]["desviacion_pct"] == pytest.approx(15.0)
    assert filas["Emergentes"]["desviacion_pct"] == pytest.approx(-15.0)


def test_el_ajuste_dice_cuanto_falta_en_dinero(cartera):
    """Con 10.000 € y objetivo 40%, emergentes debería valer 4.000: faltan 1.500."""
    db, mundo, emergentes = cartera
    _objetivo(db, mundo, 60)
    _objetivo(db, emergentes, 40)

    filas = {f["asset"].name: f for f in plan(db)["filas"]}

    assert filas["Emergentes"]["ajuste"] == pytest.approx(1500.0)
    assert filas["MSCI World"]["ajuste"] == pytest.approx(-1500.0)


def test_con_aportacion_el_objetivo_se_calcula_sobre_la_cartera_futura(cartera):
    """Aportando 2.000, el 40% se mide sobre 12.000 y no sobre 10.000."""
    db, mundo, emergentes = cartera
    _objetivo(db, mundo, 60)
    _objetivo(db, emergentes, 40)

    filas = {f["asset"].name: f for f in plan(db, aportacion=2000)["filas"]}

    assert filas["Emergentes"]["deseado"] == pytest.approx(4800.0)   # 40% de 12.000
    assert filas["Emergentes"]["ajuste"] == pytest.approx(2300.0)


def test_se_avisa_de_lo_que_no_tiene_objetivo(cartera):
    """Si no, los porcentajes parecen no cuadrar y no se entiende por qué."""
    db, mundo, _ = cartera
    _objetivo(db, mundo, 60)

    detalle = plan(db)

    assert detalle["sin_objetivo"] == pytest.approx(2500.0)


def test_las_filas_van_ordenadas_por_lo_que_mas_falta(cartera):
    """Es el orden en el que uno actúa."""
    db, mundo, emergentes = cartera
    _objetivo(db, mundo, 60)
    _objetivo(db, emergentes, 40)

    filas = plan(db)["filas"]

    assert filas[0]["asset"].name == "Emergentes"


# ---------- Reparto de la aportación ----------

def test_la_aportacion_va_solo_a_los_que_van_cortos(cartera):
    """Meter dinero en el que ya sobrepasa su peso agravaría la desviación."""
    db, mundo, emergentes = cartera
    _objetivo(db, mundo, 60)
    _objetivo(db, emergentes, 40)

    reparto = reparto_de_aportacion(db, 1000)

    assert [r["asset"].name for r in reparto] == ["Emergentes"]
    assert reparto[0]["importe"] == pytest.approx(1000.0)
    assert reparto[0]["pct_de_la_aportacion"] == pytest.approx(100.0)


def test_se_reparte_en_proporcion_a_lo_que_falta(db, monkeypatch):
    from app.services import market_data
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)

    # Cartera vacía de dos activos con objetivos 25/75: falta el triple del segundo
    a = Asset(name="A", asset_type=AssetType.ACCION, currency=Currency.EUR,
              ticker="A", current_price=1.0)
    b = Asset(name="B", asset_type=AssetType.ACCION, currency=Currency.EUR,
              ticker="B", current_price=1.0)
    db.add_all([a, b])
    db.flush()
    db.add(PesoObjetivo(asset_id=a.id, porcentaje=25))
    db.add(PesoObjetivo(asset_id=b.id, porcentaje=75))
    db.commit()

    reparto = {r["asset"].name: r["importe"] for r in reparto_de_aportacion(db, 1000)}

    assert reparto["A"] == pytest.approx(250.0)
    assert reparto["B"] == pytest.approx(750.0)


def test_sin_aportacion_no_hay_reparto(cartera):
    db, mundo, emergentes = cartera
    _objetivo(db, mundo, 60)

    assert reparto_de_aportacion(db, 0) == []
    assert reparto_de_aportacion(db, -100) == []


def test_si_todo_esta_en_objetivo_no_hay_nada_que_repartir(cartera):
    db, mundo, emergentes = cartera
    _objetivo(db, mundo, 75)
    _objetivo(db, emergentes, 25)

    # Con la cartera ya en su objetivo y sin aportación, nadie va corto
    assert reparto_de_aportacion(db, 0) == []


# ---------- Página ----------

def test_la_pagina_responde(client):
    assert client.get("/analisis/rebalanceo").status_code == 200


def test_fijar_y_reemplazar_un_objetivo(client):
    asset = Asset(name="MSCI World", asset_type=AssetType.ACCION,
                  currency=Currency.EUR, ticker="IWDA.AS")
    client.db.add(asset)
    client.db.commit()

    client.post_form("/analisis/rebalanceo/objetivos",
                     data={"asset_id": asset.id, "porcentaje": "60"}, follow_redirects=False)
    client.post_form("/analisis/rebalanceo/objetivos",
                     data={"asset_id": asset.id, "porcentaje": "70"}, follow_redirects=False)

    objetivos = client.db.query(PesoObjetivo).all()
    assert len(objetivos) == 1, "volver a fijarlo actualiza, no duplica"
    assert objetivos[0].porcentaje == 70.0


@pytest.mark.parametrize("pct", ["0", "-5", "120"])
def test_un_peso_fuera_de_rango_se_rechaza(client, pct):
    asset = Asset(name="MSCI World", asset_type=AssetType.ACCION,
                  currency=Currency.EUR, ticker="IWDA.AS")
    client.db.add(asset)
    client.db.commit()

    client.post_form("/analisis/rebalanceo/objetivos",
                     data={"asset_id": asset.id, "porcentaje": pct}, follow_redirects=False)

    assert client.db.query(PesoObjetivo).count() == 0
