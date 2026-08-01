"""Fixtures de tests: sesión SQLite en memoria y cliente HTTP de la app real.

Los tests no tocan el volumen /data ni la red. `DB_PATH` se fija ANTES de
importar `app` porque `app.database` crea el engine (y el directorio de la BD)
en tiempo de import: sin esto los tests intentarían crear /data, que no existe
fuera del contenedor.
"""
import os
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "finance-tracker-tests.db"))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app import models  # noqa: F401  registra los modelos en Base


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def sin_red(monkeypatch):
    """Neutraliza todas las llamadas a APIs externas.

    Los tests de endpoints no deben depender de Yahoo/CoinGecko/Frankfurter: el
    tipo de cambio queda fijo en 1.0 y el resto de fetchers devuelven vacío."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)
    monkeypatch.setattr(market_data, "get_stock_price", lambda t: None)
    monkeypatch.setattr(market_data, "get_crypto_price", lambda t, c="eur": None)
    monkeypatch.setattr(market_data, "get_stock_intraday", lambda t: [])
    monkeypatch.setattr(market_data, "get_crypto_intraday", lambda t, c: [])
    monkeypatch.setattr(market_data, "search_symbols", lambda q, limit=8: [])
    monkeypatch.setattr(market_data, "search_crypto", lambda q, limit=8: [])
    monkeypatch.setattr(market_data, "resolve_ticker_by_isin", lambda i: [])
    monkeypatch.setattr(market_data, "get_crypto_name", lambda i: None)


@pytest.fixture
def client(sin_red):
    """Cliente contra la app REAL (app.main:app), con la BD en memoria.

    Se instancia TestClient sin `with`: así no se dispara el lifespan, que
    arrancaría el scheduler, el bot de Telegram y el snapshot inicial."""
    from app.main import app

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine)

    def _get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_db
    test_client = TestClient(app)
    test_client.db = TestingSession()  # para preparar/inspeccionar datos en los tests

    def csrf() -> str:
        """Token CSRF vigente, pidiendo una página primero si aún no hay cookie
        (es lo que hace un navegador antes de poder enviar un formulario)."""
        if "csrftoken" not in test_client.cookies:
            test_client.get("/salud")
        return test_client.cookies["csrftoken"]

    def post_form(url, data=None, **kwargs):
        """POST de formulario con el token incluido, como lo manda la plantilla."""
        data = dict(data or {})
        data.setdefault("_csrf", csrf())
        return test_client.post(url, data=data, **kwargs)

    def post_json(url, json=None, **kwargs):
        """POST JSON con el token en la cabecera, como hace voice.js."""
        headers = {"X-CSRF-Token": csrf(), **kwargs.pop("headers", {})}
        return test_client.post(url, json=json, headers=headers, **kwargs)

    test_client.csrf = csrf
    test_client.post_form = post_form
    test_client.post_json = post_json

    yield test_client
    test_client.db.close()
    app.dependency_overrides.clear()
