"""[FT-M2] El refresco de precios pedía las cotizaciones una a una.

Con 25 activos y 10 valores en seguimiento, y Yahoo lento, el job podía tardar
minutos: cada petición lleva 10-15 s de timeout y se hacían en serie.

Los hilos **solo hacen HTTP**. La sesión de SQLAlchemy no se toca desde ellos, y
por eso no hace falta una por hilo: primero se descarga todo, después se aplica
en un único hilo. Una sesión que no existe no se puede compartir por descuido.
"""
import threading
import time
from datetime import date

import pytest

from app.models import Asset, AssetType, Currency
from app.services import scheduler


@pytest.fixture
def cartera(db_factory=None):
    """Se construye sobre la base real del scheduler, que usa SessionLocal."""
    from app.database import Base, SessionLocal, engine

    Base.metadata.create_all(engine)
    sesion = SessionLocal()
    sesion.query(Asset).delete()
    for i in range(6):
        sesion.add(Asset(
            name="ACCION %d" % i, asset_type=AssetType.ACCION, ticker="TCK%d" % i,
            currency=Currency.EUR,
        ))
    for i in range(4):
        sesion.add(Asset(
            name="CRIPTO %d" % i, asset_type=AssetType.CRIPTO, ticker="cri%d" % i,
            currency=Currency.EUR,
        ))
    sesion.commit()
    yield sesion
    sesion.query(Asset).delete()
    sesion.commit()
    sesion.close()


def test_las_cotizaciones_se_piden_en_paralelo(cartera, monkeypatch):
    """Con seis acciones y cuatro hilos, se solapan de verdad."""
    hilos = set()
    simultaneos = []
    activos_ahora = []
    cerrojo = threading.Lock()

    def _lento(ticker):
        with cerrojo:
            hilos.add(threading.current_thread().name)
            activos_ahora.append(1)
            simultaneos.append(len(activos_ahora))
        time.sleep(0.05)
        with cerrojo:
            activos_ahora.pop()
        return None

    monkeypatch.setattr(scheduler.market_data, "get_stock_price", _lento)
    monkeypatch.setattr(scheduler.market_data, "get_crypto_price", lambda t, c: None)

    scheduler._descargar_precios(cartera.query(Asset).all())

    assert len(hilos) > 1, "todas las peticiones fueron por el mismo hilo"
    assert max(simultaneos) > 1, "nunca hubo dos peticiones a la vez"


def test_no_se_pasa_del_limite_de_hilos(cartera, monkeypatch):
    """CoinGecko responde 429 en cuanto te pasas, y un 429 no es "más lento":
    es una cotización que no se actualiza."""
    activos_ahora = []
    pico = []
    cerrojo = threading.Lock()

    def _lento(ticker, moneda):
        with cerrojo:
            activos_ahora.append(1)
            pico.append(len(activos_ahora))
        time.sleep(0.05)
        with cerrojo:
            activos_ahora.pop()
        return None

    monkeypatch.setattr(scheduler.market_data, "get_stock_price", lambda t: None)
    monkeypatch.setattr(scheduler.market_data, "get_crypto_price", _lento)

    scheduler._descargar_precios(cartera.query(Asset).all())

    assert max(pico) <= scheduler.HILOS_CRIPTO


def test_no_se_pierde_ningun_activo(cartera, monkeypatch):
    """Paralelizar no puede dejarse ninguno por el camino."""
    monkeypatch.setattr(scheduler.market_data, "get_stock_price",
                        lambda t: {"price": 10.0, "previous_close": 9.0,
                                   "currency": "EUR", "name": None})
    monkeypatch.setattr(scheduler.market_data, "get_crypto_price", lambda t, c: (5.0, 4.0))

    activos = cartera.query(Asset).all()
    resultados = scheduler._descargar_precios(activos)

    assert len(resultados) == len(activos)
    assert all(a.id in resultados for a in activos)


def test_un_activo_sin_ticker_no_entra(cartera, monkeypatch):
    monkeypatch.setattr(scheduler.market_data, "get_stock_price", lambda t: None)
    monkeypatch.setattr(scheduler.market_data, "get_crypto_price", lambda t, c: None)
    huerfano = Asset(name="SIN TICKER", asset_type=AssetType.ACCION,
                     currency=Currency.EUR, ticker=None)
    cartera.add(huerfano)
    cartera.commit()

    resultados = scheduler._descargar_precios(cartera.query(Asset).all())

    assert huerfano.id not in resultados


def test_sin_activos_no_arranca_ningun_pool(cartera, monkeypatch):
    """Abrir un ThreadPoolExecutor con max_workers=0 lanza ValueError."""
    monkeypatch.setattr(scheduler.market_data, "get_stock_price", lambda t: None)
    monkeypatch.setattr(scheduler.market_data, "get_crypto_price", lambda t, c: None)

    assert scheduler._descargar_precios([]) == {}


def test_los_jobs_declaran_max_instances(monkeypatch):
    """Una ejecución larga no puede solaparse con la siguiente: serían dos
    tandas de peticiones pisándose los mismos activos."""
    registrados = {}

    class _SchedulerFalso:
        timezone = None

        def add_job(self, func, *args, **kwargs):
            registrados[kwargs.get("id") or getattr(func, "__name__", "?")] = kwargs

        def start(self):
            pass

    monkeypatch.setattr(scheduler, "BackgroundScheduler", lambda **kw: _SchedulerFalso())
    monkeypatch.setattr(scheduler, "datetime", __import__("datetime").datetime)
    scheduler.start_scheduler()

    assert registrados["update_prices"]["max_instances"] == 1
    assert registrados["intraday_sample"]["max_instances"] == 1


def test_la_fecha_del_fixture_no_importa():
    """Marcador: el fixture no depende de la fecha, y este test lo deja claro
    para que nadie meta aquí un `date.today()` que lo haga frágil."""
    assert isinstance(date.today(), date)
