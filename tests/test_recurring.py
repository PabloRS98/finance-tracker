"""Reglas recurrentes: periodicidad (interval_months), catch-up y conversión USD->EUR."""
from datetime import date as _date

import pytest

from app.models import Currency, RecurringTransaction, Transaction, TransactionType
from app.services import market_data, recurring
from app.services.recurring import generate_due_transactions, next_due_date


class _FixedDate(_date):
    """date con today() fijo para poder controlar el catch-up en los tests."""

    @classmethod
    def today(cls):
        return cls(2026, 6, 15)


@pytest.fixture
def frozen_today(monkeypatch):
    monkeypatch.setattr(recurring, "date", _FixedDate)


def _add(db, **kw):
    base = {
        "name": "Regla", "type": TransactionType.GASTO, "amount": 100.0,
        "currency": Currency.EUR, "interval_months": 1, "day_of_month": 1,
        "start_date": _date(2026, 1, 1),
    }
    base.update(kw)
    rule = RecurringTransaction(**base)
    db.add(rule)
    db.commit()
    return rule


def test_trimestral_ancla_en_start_date(db, frozen_today):
    # today = 2026-06-15; trimestral desde 2026-01-10 -> 01-10 y 04-10 (07-10 es futuro)
    rule = _add(db, interval_months=3, day_of_month=10, start_date=_date(2026, 1, 10))
    created = generate_due_transactions(db)
    assert created == 2
    fechas = sorted(t.date for t in db.query(Transaction).all())
    assert fechas == [_date(2026, 1, 10), _date(2026, 4, 10)]
    assert rule.last_generated == _date(2026, 4, 10)
    assert next_due_date(rule) == _date(2026, 7, 10)
    # Idempotente: una segunda pasada no duplica
    assert generate_due_transactions(db) == 0


def test_anual_genera_una_sola(db, frozen_today):
    rule = _add(db, interval_months=12, day_of_month=1, start_date=_date(2026, 3, 1))
    assert generate_due_transactions(db) == 1  # solo 2026-03-01 (el siguiente es 2027)
    assert next_due_date(rule) == _date(2027, 3, 1)


def test_usd_se_convierte_a_eur(db, frozen_today, monkeypatch):
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.9)
    _add(db, amount=10.0, currency=Currency.USD, day_of_month=1, start_date=_date(2026, 6, 1))
    assert generate_due_transactions(db) == 1
    tx = db.query(Transaction).one()
    assert tx.amount == pytest.approx(9.0)  # 10 USD * 0.9


def test_sin_tipo_de_cambio_la_recurrente_se_aplaza(db, frozen_today, monkeypatch):
    """Sin FX no se apunta un importe sin convertir: la regla queda pendiente y
    el catch-up del próximo arranque la generará bien."""
    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: None)
    rule = _add(db, amount=10.0, currency=Currency.USD, day_of_month=1, start_date=_date(2026, 6, 1))

    assert generate_due_transactions(db) == 0
    assert db.query(Transaction).count() == 0
    assert rule.last_generated is None  # no avanza: se reintentará
