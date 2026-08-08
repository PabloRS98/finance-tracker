"""Exactitud de los importes del libro y sincronía entre modelos y migraciones."""
import os
import tempfile
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine

from app.models import Category, Transaction, TransactionStatus, TransactionType
from app.services.market_data import to_base


def _gasto(amount) -> Transaction:
    return Transaction(
        date=date.today(), type=TransactionType.GASTO, amount=amount,
        description="x", status=TransactionStatus.CONFIRMADO,
    )


def test_suma_de_centimos_es_exacta(db):
    """Regresión: con Float, diez gastos de 0,10 € sumaban 0.9999999999999999."""
    for _ in range(10):
        db.add(_gasto(Decimal("0.10")))
    db.commit()
    db.expire_all()

    total = sum((t.amount for t in db.query(Transaction).all()), Decimal("0"))

    assert total == Decimal("1.00")


def test_los_importes_se_leen_como_decimal(db):
    db.add(_gasto(19.99))
    db.commit()
    db.expire_all()

    amount = db.query(Transaction).one().amount

    assert isinstance(amount, Decimal)
    assert amount == Decimal("19.99")


def test_presupuesto_tambien_es_decimal(db):
    db.add(Category(name="Comida", keywords="", budget_limit=Decimal("300.00")))
    db.commit()
    db.expire_all()

    assert db.query(Category).one().budget_limit == Decimal("300.00")


def test_to_base_cuadra_a_centimos(monkeypatch):
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.9123)

    # 20 USD * 0,9123 = 18,246 -> 18,25 (half-up), no 18.246000000000002
    assert to_base(20, "USD", "EUR") == Decimal("18.25")


def test_to_base_en_la_misma_divisa_no_llama_a_la_api(monkeypatch):
    from app.services import market_data

    def _explota(a, b):
        raise AssertionError("no debería pedir tipo de cambio para la misma divisa")

    monkeypatch.setattr(market_data, "get_exchange_rate", _explota)

    assert to_base(Decimal("12.5"), "EUR", "EUR") == Decimal("12.50")


def test_migraciones_al_dia_con_los_modelos():
    """Las migraciones deben describir exactamente el esquema de models.py.

    Si alguien toca un modelo y olvida `alembic revision --autogenerate`, este
    test falla enseñando la diferencia, en vez de descubrirse en producción.
    """
    from alembic import command
    from alembic.autogenerate import compare_metadata
    from alembic.config import Config
    from alembic.migration import MigrationContext

    from app.database import Base

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "schema-check.db")
        config = Config(os.path.join(root, "alembic.ini"))
        config.set_main_option("script_location", os.path.join(root, "migrations"))
        config.set_main_option("sqlalchemy.url", "sqlite:///%s" % db_path)
        command.upgrade(config, "head")

        engine = create_engine("sqlite:///%s" % db_path)
        try:
            with engine.connect() as connection:
                context = MigrationContext.configure(
                    connection, opts={"compare_type": True, "target_metadata": Base.metadata},
                )
                diff = compare_metadata(context, Base.metadata)
        finally:
            # En Windows el fichero sigue bloqueado mientras el pool tenga la
            # conexión abierta, y el borrado del directorio temporal revienta.
            engine.dispose()

    assert diff == [], "Faltan migraciones para: %s" % diff
