"""[FT-A5] El resumen de Telegram calculaba los pesos ignorando el tipo de cambio.

    value_eur = (a.current_price or 0) * qty      # el comentario decía "en EUR"
    alloc = value_eur / total_eur * 100           # total_eur sí venía convertido

Se dividían peras entre manzanas: el numerador en la divisa del activo y el
denominador en la moneda base. Con EUR/USD ~1,08, una posición en dólares
aparecía con un peso ~8 % inferior al real y todas las demás quedaban infladas
para compensar. Y sobre esos porcentajes se toman decisiones de rebalanceo.

Llamativo porque el resto de la app es escrupulosa con esto: `portfolio_totals`
excluye del agregado los activos que no puede convertir, `snapshot_net_worth` se
niega a guardar un snapshot incompleto y `xray.invested_rows` descarta las filas
sin tipo de cambio. Aquí, en el mensaje que llega al móvil cinco veces al día,
se hacía justo lo contrario.
"""
import pytest

from app.models import Asset, AssetType, Currency, Operation, OperationType
from app.services import market_data, telegram_bot


@pytest.fixture
def cartera_mixta(db, monkeypatch):
    """1.000 EUR en un activo y 1.000 USD en otro, con el dólar a 0,9.

    En moneda base son 1.000 y 900: los pesos correctos son 52,6 % y 47,4 %,
    no 50/50."""
    def _activo(nombre, divisa, precio):
        a = Asset(name=nombre, asset_type=AssetType.ACCION, ticker=nombre,
                  currency=divisa, current_price=precio)
        db.add(a)
        db.flush()
        db.add(Operation(asset_id=a.id, type=OperationType.COMPRA, quantity=10.0,
                         unit_price=precio, date=__import__("datetime").date(2026, 1, 1)))
        return a

    _activo("EUROPA", Currency.EUR, 100.0)
    _activo("EEUU", Currency.USD, 100.0)
    db.commit()

    monkeypatch.setattr(
        market_data, "get_exchange_rate",
        lambda origen, destino: 1.0 if origen == destino else 0.9,
    )
    return db


def _pesos(lineas: list[str]) -> list[int]:
    """Porcentajes de las líneas de posición. El resumen los imprime enteros
    (`%.0f%%`), así que se comparan como enteros."""
    import re

    return [int(m) for linea in lineas
            for m in re.findall(r"\((\d+)%\)", linea)]


def test_resumen_convierte_divisas_en_la_asignacion(cartera_mixta):
    """1.000 EUR y 1.000 USD a 0,9 son 1.000 y 900 en base: 53 % y 47 %.

    Sin convertir salía 50 % y 50 %, que es el número que llegaba al móvil."""
    lineas = telegram_bot._positions_grouped(cartera_mixta, total_eur=1900.0)

    pesos = sorted(_pesos(lineas))

    assert pesos == [47, 53]


def test_sin_convertir_los_pesos_saldrian_iguales(cartera_mixta):
    """Deja escrito el número incorrecto, para que se vea qué se corrigió."""
    lineas = telegram_bot._positions_grouped(cartera_mixta, total_eur=1900.0)

    assert _pesos(lineas) != [50, 50]


def test_resumen_omite_activos_sin_tipo_de_cambio_y_lo_dice(db, monkeypatch):
    """Coherente con portfolio_totals: fuera del agregado antes que contarlo 1:1.

    Y se dice, porque un porcentaje que no suma 100 sin explicación es peor que
    uno que falta."""
    import datetime

    convertible = Asset(name="CONVERTIBLE", asset_type=AssetType.ACCION, ticker="CONV",
                        currency=Currency.EUR, current_price=100.0)
    huerfano = Asset(name="SIN CAMBIO", asset_type=AssetType.ACCION, ticker="SINFX",
                     currency=Currency.USD, current_price=100.0)
    db.add_all([convertible, huerfano])
    db.flush()
    for a in (convertible, huerfano):
        db.add(Operation(asset_id=a.id, type=OperationType.COMPRA, quantity=10.0,
                         unit_price=100.0, date=datetime.date(2026, 1, 1)))
    db.commit()

    monkeypatch.setattr(
        market_data, "get_exchange_rate",
        lambda origen, destino: 1.0 if origen == destino else None,
    )

    lineas = telegram_bot._positions_grouped(db, total_eur=1000.0)
    posiciones = [ln for ln in lineas if ln.startswith("│")]
    aviso = [ln for ln in lineas if "sin tipo de cambio" in ln.lower()]

    assert any("CONVERTIBLE" in ln for ln in posiciones)
    assert not any("SIN CAMBIO" in ln for ln in posiciones), "no puede entrar en el reparto"
    assert aviso, "si se dejan posiciones fuera, hay que decirlo"
    assert "SIN CAMBIO" in aviso[0], "y hay que decir cuáles"
