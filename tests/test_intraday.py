"""Muestreo intradía del patrimonio: inserción y purga por retención."""
from datetime import timedelta

import pytest

from app.models import NetWorthIntraday, utcnow
from app.services import scheduler as scheduler_svc
from app.services.scheduler import Valuation, sample_intraday


@pytest.fixture
def _sin_apis(monkeypatch):
    monkeypatch.setattr(scheduler_svc, "compute_net_worth", lambda db: Valuation(total=1000.0))
    monkeypatch.setattr("app.services.portfolio.portfolio_totals", lambda db: {"invested_value": 600.0})


def test_sample_inserta_y_purga(db, _sin_apis):
    # Una muestra vieja (50 h) y una reciente (2 h): la vieja debe purgarse
    db.add(NetWorthIntraday(ts=utcnow() - timedelta(hours=50), total_value=1.0, invested_value=1.0))
    db.add(NetWorthIntraday(ts=utcnow() - timedelta(hours=2), total_value=2.0, invested_value=2.0))
    db.commit()

    sample_intraday(db)

    rows = db.query(NetWorthIntraday).order_by(NetWorthIntraday.ts).all()
    assert len(rows) == 2  # la de 50 h fuera; quedan la de 2 h y la nueva
    assert rows[0].total_value == 2.0


def test_muestra_conserva_desglose(db, _sin_apis):
    sample_intraday(db)
    row = db.query(NetWorthIntraday).one()
    assert row.total_value == pytest.approx(1000.0)
    assert row.invested_value == pytest.approx(600.0)
    assert row.ts.microsecond == 0


def test_no_muestrea_si_falta_tipo_de_cambio(db, monkeypatch):
    """Con una divisa sin tipo de cambio el total sería parcial: no se persiste."""
    monkeypatch.setattr(
        scheduler_svc, "compute_net_worth", lambda db: Valuation(total=500.0, missing={"USD"})
    )
    monkeypatch.setattr("app.services.portfolio.portfolio_totals", lambda db: {"invested_value": 0.0})

    sample_intraday(db)

    assert db.query(NetWorthIntraday).count() == 0
