"""Matemática de posiciones: coste medio, P&L realizado, ventas en exceso,
y efecto divisa (coste en base al FX del día de cada compra)."""
from datetime import date

import pytest

from app.models import Asset, AssetType, Currency, Operation, OperationType, PriceHistory, TransactionStatus
from app.services.portfolio import asset_summary, compute_position, fx_lookup


def op(tipo, qty, price, fee=0.0, day="2026-01-10", status=TransactionStatus.CONFIRMADO):
    return Operation(
        type=tipo, quantity=qty, unit_price=price, fee=fee,
        date=date.fromisoformat(day), status=status,
    )


def test_compra_simple_incluye_comision():
    pos = compute_position([op(OperationType.COMPRA, 10, 100, fee=5)])
    assert pos.quantity == 10
    assert pos.cost_open == pytest.approx(1005)
    assert pos.avg_cost == pytest.approx(100.5)
    assert pos.realized_pnl == 0


def test_coste_medio_de_dos_compras():
    pos = compute_position([
        op(OperationType.COMPRA, 10, 100),
        op(OperationType.COMPRA, 10, 200, day="2026-02-10"),
    ])
    assert pos.quantity == 20
    assert pos.avg_cost == pytest.approx(150)


def test_venta_cristaliza_pnl_a_coste_medio():
    pos = compute_position([
        op(OperationType.COMPRA, 0.5, 50000, fee=10),          # coste 25010, medio 50020
        op(OperationType.VENTA, 0.2, 60000, fee=5, day="2026-03-15"),
    ])
    assert pos.quantity == pytest.approx(0.3)
    assert pos.realized_pnl == pytest.approx((0.2 * 60000 - 5) - 0.2 * 50020)
    assert pos.cost_open == pytest.approx(25010 - 0.2 * 50020)


def test_venta_en_exceso_no_deja_coste_negativo():
    pos = compute_position([
        op(OperationType.COMPRA, 1, 100),
        op(OperationType.VENTA, 2, 120, day="2026-02-01"),
    ])
    assert pos.quantity == 0
    assert pos.cost_open == 0
    # solo la parte cubierta retira coste; el exceso entra como coste cero
    assert pos.realized_pnl == pytest.approx(2 * 120 - 100)


def test_pendientes_no_cuentan():
    pos = compute_position([
        op(OperationType.COMPRA, 5, 10),
        op(OperationType.COMPRA, 99, 10, status=TransactionStatus.PENDIENTE),
    ])
    assert pos.quantity == 5


def test_orden_por_fecha_no_por_lista():
    pos = compute_position([
        op(OperationType.VENTA, 1, 200, day="2026-05-01"),
        op(OperationType.COMPRA, 2, 100, day="2026-01-01"),
    ])
    assert pos.quantity == 1
    assert pos.realized_pnl == pytest.approx(200 - 100)


def _accion_con_ops(*ops):
    a = Asset(name="X", asset_type=AssetType.ACCION, currency=Currency.EUR)
    a.operations = list(ops)
    return a


def test_valora_a_coste_medio_sin_precio_de_mercado():
    # Activo recién importado (sin current_price): debe valer a coste medio, no 0
    a = _accion_con_ops(op(OperationType.COMPRA, 10, 100, fee=5))  # coste 1005
    assert a.effective_price() == pytest.approx(100.5)
    assert a.current_value() == pytest.approx(1005)


def test_precio_de_mercado_manda_sobre_coste():
    a = _accion_con_ops(op(OperationType.COMPRA, 10, 100))
    a.current_price = 120
    assert a.effective_price() == 120
    assert a.current_value() == pytest.approx(1200)


def test_sin_operaciones_ni_precio_no_vale_nada():
    a = _accion_con_ops()
    assert a.effective_price() is None
    assert a.current_value() == 0.0


# ---------- Efecto divisa ----------

def test_coste_en_base_usa_fx_del_dia_de_cada_compra():
    fx = {date(2026, 1, 10): 1.0, date(2026, 2, 10): 0.8}
    pos = compute_position([
        op(OperationType.COMPRA, 10, 100),                    # 1000 USD -> 1000 EUR
        op(OperationType.COMPRA, 10, 100, day="2026-02-10"),  # 1000 USD -> 800 EUR
    ], fx_on=lambda d: fx[d])
    assert pos.cost_open == pytest.approx(2000)
    assert pos.cost_open_base == pytest.approx(1800)


def test_venta_retira_coste_base_a_coste_medio():
    pos = compute_position([
        op(OperationType.COMPRA, 10, 100),                     # 1000 USD -> 800 EUR
        op(OperationType.VENTA, 5, 100, day="2026-02-01"),
    ], fx_on=lambda d: 0.8 if d == date(2026, 1, 10) else 0.9)
    assert pos.cost_open == pytest.approx(500)
    assert pos.cost_open_base == pytest.approx(400)  # mitad del coste base, no al FX de la venta


def test_cierre_total_resetea_coste_base():
    pos = compute_position([
        op(OperationType.COMPRA, 1, 100),
        op(OperationType.VENTA, 1, 120, day="2026-02-01"),
    ], fx_on=lambda d: 0.9)
    assert pos.cost_open_base == 0.0


def test_sin_fx_el_coste_base_es_el_local():
    pos = compute_position([op(OperationType.COMPRA, 10, 100, fee=5)])
    assert pos.cost_open_base == pytest.approx(pos.cost_open)


def test_efecto_divisa_separado_del_pnl_de_precio():
    # Precio local plano (compra 100, cotiza 100): P&L 0%. El USD se aprecia de
    # 0.90 a 0.99 EUR: todo el rendimiento en EUR es efecto divisa (+10%).
    a = Asset(name="X", asset_type=AssetType.ACCION, currency=Currency.USD)
    a.operations = [op(OperationType.COMPRA, 10, 100)]
    a.current_price = 100.0
    s = asset_summary(a, fx_on=lambda d: 0.90 if d == date(2026, 1, 10) else 0.99)
    assert s["pnl_pct"] == pytest.approx(0.0)
    assert s["pnl_pct_base"] == pytest.approx(10.0)
    assert s["fx_effect_pct"] == pytest.approx(10.0)
    assert s["unrealized_base"] == pytest.approx(90.0)  # 990 EUR de valor - 900 de coste


def test_efecto_divisa_multiplicativo():
    # Precio +10% local y divisa -5%: total en base = 1.10 x 0.95 - 1 = +4.5%
    a = Asset(name="X", asset_type=AssetType.ACCION, currency=Currency.USD)
    a.operations = [op(OperationType.COMPRA, 10, 100)]
    a.current_price = 110.0
    s = asset_summary(a, fx_on=lambda d: 1.0 if d == date(2026, 1, 10) else 0.95)
    assert s["pnl_pct"] == pytest.approx(10.0)
    assert s["pnl_pct_base"] == pytest.approx(4.5)
    assert s["fx_effect_pct"] == pytest.approx(-5.0)


def test_activo_eur_no_tiene_efecto_divisa():
    a = _accion_con_ops(op(OperationType.COMPRA, 10, 100))
    a.current_price = 110.0
    s = asset_summary(a)  # sin fx_on: divisa base
    assert s["fx_effect_pct"] is None
    assert s["pnl_pct_base"] is None


# ---------- Multi-divisa ----------

def test_efecto_divisa_hkd():
    # La matemática es la misma para cualquier divisa del enum: precio local
    # plano y el HKD se aprecia de 0,115 a 0,126 EUR -> todo es efecto divisa
    a = Asset(name="Ejemplo", asset_type=AssetType.ACCION, currency=Currency.HKD)
    a.operations = [op(OperationType.COMPRA, 100, 17.0)]
    a.current_price = 17.0
    s = asset_summary(a, fx_on=lambda d: 0.115 if d == date(2026, 1, 10) else 0.126)
    assert s["pnl_pct"] == pytest.approx(0.0)
    assert s["fx_effect_pct"] == pytest.approx(100 * (0.126 / 0.115 - 1))


def test_fx_lookup_serie_generica(db, monkeypatch):
    # fx_lookup lee la serie FX:<divisa>:EUR para días pasados y el tipo vivo para hoy
    from app.services import market_data
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.120)
    db.add(PriceHistory(symbol="FX:HKD:EUR", date=date(2026, 1, 10), price=0.110))
    db.commit()
    rate_for = fx_lookup(db, "HKD")
    assert rate_for(date(2026, 1, 15)) == pytest.approx(0.110)  # último cierre conocido
    assert rate_for(date.today()) == pytest.approx(0.120)       # hoy: tipo vivo


def test_fx_lookup_divisa_base_devuelve_none(db):
    assert fx_lookup(db, "EUR") is None


# ---------- Divisa de exposición (cotiza en base, subyacente en otra divisa) ----------

def test_exposicion_todo_el_rendimiento_es_divisa():
    # Compra a 90 EUR con USD->EUR 0,90 (=100 USD); hoy 99 EUR con FX 0,99
    # (sigue valiendo 100 USD): total EUR +10%, precio en USD 0%, divisa +10%.
    a = Asset(name="Fondo USD (Acc)", asset_type=AssetType.ACCION, currency=Currency.EUR,
              exposure_currency="USD")
    a.operations = [op(OperationType.COMPRA, 10, 90)]
    a.current_price = 99.0
    def fx(d):
        return 0.90 if d == date(2026, 1, 10) else 0.99

    s = asset_summary(a, fx_on=None, exposure_fx=fx)
    assert s["pnl_pct"] == pytest.approx(10.0)             # total en EUR
    assert s["exposure_local_pct"] == pytest.approx(0.0)   # precio plano en USD
    assert s["fx_effect_pct"] == pytest.approx(10.0)
    assert s["pnl_pct_base"] == pytest.approx(10.0)


def test_exposicion_descomposicion_multiplicativa():
    # Precio en USD +10% y USD -5%: total EUR = 1,10 x 0,95 - 1 = +4,5%
    a = Asset(name="Fondo USD (Acc)", asset_type=AssetType.ACCION, currency=Currency.EUR,
              exposure_currency="USD")
    a.operations = [op(OperationType.COMPRA, 10, 100)]  # 100 EUR al FX 1,0 = 100 USD
    a.current_price = 104.5
    def fx(d):
        return 1.0 if d == date(2026, 1, 10) else 0.95

    s = asset_summary(a, exposure_fx=fx)
    assert s["pnl_pct"] == pytest.approx(4.5)
    assert s["exposure_local_pct"] == pytest.approx(10.0)
    assert s["fx_effect_pct"] == pytest.approx(-5.0)


def test_sin_exposicion_no_cambia_nada():
    a = _accion_con_ops(op(OperationType.COMPRA, 10, 100))
    a.current_price = 110.0
    s = asset_summary(a)
    assert s["exposure_local_pct"] is None
    assert s["fx_effect_pct"] is None
