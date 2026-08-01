"""Cálculos derivados del histórico: rentabilidad anualizada (CAGR) y
cotización EUR/USD (snapshot para dashboard y resumen de Telegram)."""
from datetime import date, timedelta

import pytest

from app.models import (
    Asset, AssetType, Benchmark, Currency, Operation, OperationType, PriceHistory, TransactionStatus,
)
from app.services import market_data
from app.services import history as history_svc
from app.services.history import cagr_from_evolution, eur_usd_snapshot, portfolio_evolution


def _serie(dias: int, twr_final: float) -> list[dict]:
    """Serie sintética diaria con TWR lineal hasta twr_final."""
    from datetime import date, timedelta
    start = date(2025, 1, 1)
    return [
        {
            "fecha": (start + timedelta(days=i)).isoformat(),
            "invertido": 1000.0,
            "twr": twr_final * i / max(dias - 1, 1),
        }
        for i in range(dias)
    ]


def test_cagr_anualiza_un_anno_completo():
    # +10% en ~365 días -> CAGR ~ +10%
    cagr = cagr_from_evolution(_serie(366, 10.0))
    assert cagr == pytest.approx(10.0, abs=0.2)


def test_cagr_dos_annos_compone():
    # +21% en ~2 años -> ~+10% anual (1.21 = 1.1^2)
    cagr = cagr_from_evolution(_serie(731, 21.0))
    assert cagr == pytest.approx(10.0, abs=0.2)


def test_cagr_none_con_historico_corto():
    assert cagr_from_evolution(_serie(60, 5.0)) is None


def test_cagr_none_sin_datos():
    assert cagr_from_evolution([]) is None
    assert cagr_from_evolution([{"fecha": "2026-01-01", "invertido": 0.0, "twr": 0.0}]) is None


# ---------- EUR/USD ----------

def _fx_row(db, days_ago: int, usd_to_eur: float):
    db.add(PriceHistory(symbol="FX:USD:EUR", date=date.today() - timedelta(days=days_ago), price=usd_to_eur))
    db.commit()


def test_eur_usd_snapshot_invierte_la_serie_y_compara_con_ayer(db, monkeypatch):
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.12)
    _fx_row(db, 1, 0.90)  # cierre de ayer: EUR/USD = 1/0,90 = 1,1111
    snap = eur_usd_snapshot(db)
    assert snap["rate"] == pytest.approx(1.12)
    assert snap["points"][0]["rate"] == pytest.approx(1.1111, abs=1e-4)
    assert snap["change_pct"] == pytest.approx(100 * (1.12 - 1 / 0.90) / (1 / 0.90), abs=0.01)
    assert snap["phrase"] == "el euro se revaloriza frente al dólar"
    assert snap["points"][-1]["fecha"] == date.today().isoformat()  # punto vivo de hoy


def test_eur_usd_snapshot_depreciacion(db, monkeypatch):
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.05)
    _fx_row(db, 1, 0.90)
    assert eur_usd_snapshot(db)["phrase"] == "el euro se deprecia frente al dólar"


def test_eur_usd_snapshot_sin_historico_no_inventa_variacion(db, monkeypatch):
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.10)
    snap = eur_usd_snapshot(db)
    assert snap["change_pct"] is None
    assert snap["phrase"] is None
    assert snap["rate"] == pytest.approx(1.10)


def test_cagr_ignora_dias_sin_exposicion():
    # los primeros 200 días sin invertir no cuentan como periodo
    from datetime import date, timedelta
    start = date(2025, 1, 1)
    serie = [
        {"fecha": (start + timedelta(days=i)).isoformat(), "invertido": 0.0, "twr": 0.0}
        for i in range(200)
    ] + _serie(366, 10.0)
    # fechas del tramo invertido: reindexar para que sigan al tramo vacío
    for j, p in enumerate(serie[200:]):
        p["fecha"] = (start + timedelta(days=200 + j)).isoformat()
    cagr = cagr_from_evolution(serie)
    assert cagr == pytest.approx(10.0, abs=0.2)


# ---------- Multi-divisa ----------

def test_evolucion_multidivisa(db):
    # Dos activos extranjeros con divisas distintas: cada uno se valora con SU
    # serie FX (con una única serie USD, el HKD saldría valorado como USD)
    d0 = date.today() - timedelta(days=5)
    a_usd = Asset(name="A", asset_type=AssetType.ACCION, currency=Currency.USD)
    a_hkd = Asset(name="B", asset_type=AssetType.ACCION, currency=Currency.HKD)
    db.add_all([a_usd, a_hkd])
    db.flush()
    db.add_all([
        Operation(asset_id=a_usd.id, type=OperationType.COMPRA, quantity=1, unit_price=100.0,
                  fee=0.0, date=d0, status=TransactionStatus.CONFIRMADO),
        Operation(asset_id=a_hkd.id, type=OperationType.COMPRA, quantity=1, unit_price=100.0,
                  fee=0.0, date=d0, status=TransactionStatus.CONFIRMADO),
        PriceHistory(symbol="FX:USD:EUR", date=d0, price=0.5),
        PriceHistory(symbol="FX:HKD:EUR", date=d0, price=0.1),
    ])
    db.commit()
    ev = portfolio_evolution(db)
    # 100 USD x 0,5 + 100 HKD x 0,1 = 60 EUR
    assert ev[-1]["invertido"] == pytest.approx(60.0)


def test_refresh_descarga_fx_de_divisas_usadas(db, monkeypatch):
    # Pide la serie FX de cada divisa con operaciones (y USD siempre), nada más
    llamadas = []
    monkeypatch.setattr(history_svc, "fetch_fx_history", lambda f, t, s: (llamadas.append(f), {})[1])
    monkeypatch.setattr(history_svc, "fetch_stock_history", lambda t, s: {})
    monkeypatch.setattr(history_svc, "fetch_crypto_history", lambda i, c, s: {})
    a_eur = Asset(name="E", asset_type=AssetType.ACCION, currency=Currency.EUR)
    a_hkd = Asset(name="H", asset_type=AssetType.ACCION, currency=Currency.HKD)
    db.add_all([a_eur, a_hkd])
    db.flush()
    d0 = date.today() - timedelta(days=3)
    db.add_all([
        Operation(asset_id=a_eur.id, type=OperationType.COMPRA, quantity=1, unit_price=10.0,
                  fee=0.0, date=d0, status=TransactionStatus.CONFIRMADO),
        Operation(asset_id=a_hkd.id, type=OperationType.COMPRA, quantity=1, unit_price=10.0,
                  fee=0.0, date=d0, status=TransactionStatus.CONFIRMADO),
    ])
    db.commit()
    history_svc.refresh_price_history(db)
    assert set(llamadas) == {"USD", "HKD"}


def test_backfill_5a_pide_el_tramo_antiguo(db, monkeypatch):
    # Con serie guardada que empieza hace ~1 año, el refresh debe pedir el
    # tramo hasta 5 años atrás (backfill one-shot para el rango 5A de la ficha)
    pedidas = []
    monkeypatch.setattr(history_svc, "fetch_stock_history", lambda t, s: (pedidas.append((t, s)), {})[1])
    monkeypatch.setattr(history_svc, "fetch_fx_history", lambda f, t, s: {})
    a = Asset(name="Fondo", asset_type=AssetType.ACCION, currency=Currency.EUR, ticker="XXX.DE")
    db.add(a)
    db.flush()
    d0 = date.today() - timedelta(days=400)
    db.add(Operation(asset_id=a.id, type=OperationType.COMPRA, quantity=1, unit_price=10.0,
                     fee=0.0, date=d0, status=TransactionStatus.CONFIRMADO))
    db.add(PriceHistory(symbol="XXX.DE", date=date.today() - timedelta(days=365), price=10.0))
    db.add(PriceHistory(symbol="XXX.DE", date=date.today(), price=11.0))
    db.commit()

    history_svc.refresh_price_history(db)

    inicio = [s for t, s in pedidas if t == "XXX.DE"][0]
    assert inicio <= date.today() - timedelta(days=5 * 365 - 7)


def test_el_indice_se_rellena_hasta_la_primera_operacion(db, monkeypatch):
    """Los benchmarks solo avanzaban hacia delante: una vez guardado el primer
    cierre, nunca se pedía el tramo anterior y la comparación contra el índice
    quedaba en blanco para casi todo el histórico."""
    pedidas = []
    monkeypatch.setattr(history_svc, "fetch_stock_history", lambda t, s: (pedidas.append((t, s)), {})[1])
    monkeypatch.setattr(history_svc, "fetch_fx_history", lambda f, t, s: {})
    a = Asset(name="Fondo", asset_type=AssetType.ACCION, currency=Currency.EUR, ticker="ZZZ.DE")
    db.add(a)
    db.flush()
    primera_op = date.today() - timedelta(days=900)
    db.add(Operation(asset_id=a.id, type=OperationType.COMPRA, quantity=1, unit_price=10.0,
                     fee=0.0, date=primera_op, status=TransactionStatus.CONFIRMADO))
    db.add(Benchmark(clave="sp500", label="S&P 500", symbol="^GSPC"))
    # El índice solo tiene los últimos 30 días
    db.add(PriceHistory(symbol="^GSPC", date=date.today() - timedelta(days=30), price=5000.0))
    db.commit()

    history_svc.refresh_price_history(db)

    inicio = [s for t, s in pedidas if t == "^GSPC"][0]
    assert inicio == primera_op


def test_el_indice_no_se_repide_si_ya_llega(db, monkeypatch):
    """Con la serie completa, solo el tramo incremental."""
    pedidas = []
    monkeypatch.setattr(history_svc, "fetch_stock_history", lambda t, s: (pedidas.append((t, s)), {})[1])
    monkeypatch.setattr(history_svc, "fetch_fx_history", lambda f, t, s: {})
    a = Asset(name="Fondo", asset_type=AssetType.ACCION, currency=Currency.EUR, ticker="WWW.DE")
    db.add(a)
    db.flush()
    primera_op = date.today() - timedelta(days=100)
    db.add(Operation(asset_id=a.id, type=OperationType.COMPRA, quantity=1, unit_price=10.0,
                     fee=0.0, date=primera_op, status=TransactionStatus.CONFIRMADO))
    db.add(Benchmark(clave="sp500", label="S&P 500", symbol="^GSPC"))
    db.add(PriceHistory(symbol="^GSPC", date=primera_op, price=4800.0))
    db.add(PriceHistory(symbol="^GSPC", date=date.today() - timedelta(days=3), price=5000.0))
    db.commit()

    history_svc.refresh_price_history(db)

    inicio = [s for t, s in pedidas if t == "^GSPC"][0]
    assert inicio == date.today() - timedelta(days=2)


def test_backfill_5a_no_repite_si_ya_esta(db, monkeypatch):
    # Serie ya completa hasta 5 años: el refresh solo pide el tramo incremental
    pedidas = []
    monkeypatch.setattr(history_svc, "fetch_stock_history", lambda t, s: (pedidas.append((t, s)), {})[1])
    monkeypatch.setattr(history_svc, "fetch_fx_history", lambda f, t, s: {})
    a = Asset(name="Fondo", asset_type=AssetType.ACCION, currency=Currency.EUR, ticker="YYY.DE")
    db.add(a)
    db.flush()
    db.add(Operation(asset_id=a.id, type=OperationType.COMPRA, quantity=1, unit_price=10.0,
                     fee=0.0, date=date.today() - timedelta(days=30), status=TransactionStatus.CONFIRMADO))
    db.add(PriceHistory(symbol="YYY.DE", date=date.today() - timedelta(days=5 * 365 + 2), price=9.0))
    db.add(PriceHistory(symbol="YYY.DE", date=date.today() - timedelta(days=3), price=10.5))
    db.commit()

    history_svc.refresh_price_history(db)

    inicio = [s for t, s in pedidas if t == "YYY.DE"][0]
    assert inicio == date.today() - timedelta(days=2)  # solo lo que falta
