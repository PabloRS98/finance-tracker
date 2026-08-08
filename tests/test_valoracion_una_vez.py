"""[FT-M10] Valorar la cartera recalculaba la posición más veces de la cuenta.

`Asset.effective_price()` llama a `compute_position(self.operations)` cuando el
activo no tiene precio de mercado, y `compute_position` **ordena la lista de
operaciones** cada vez. Como `current_value()` usa `effective_price()`, y
`current_value()` se invoca en bucle sobre todos los activos —al valorar el
patrimonio, al pintar el desglose y al montar cada fila de /activos—, ese
trabajo se repetía.

En la lista de activos era doble: `_row_for` pedía `asset.current_value()` **y**
`asset_summary(asset, ...)`, y las dos cosas calculan lo mismo.
"""
from datetime import date

import pytest

from app.models import Asset, AssetType, Currency, Operation, OperationType
from app.services import portfolio
from app.services.scheduler import compute_net_worth


@pytest.fixture
def sin_cotizacion(client, monkeypatch):
    """Cinco activos con operaciones y SIN precio de mercado.

    Es el caso que dispara el recálculo: con `current_price` puesto,
    `effective_price` devuelve antes de llegar a `compute_position`."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)
    for i in range(5):
        activo = Asset(name="SIN PRECIO %d" % i, asset_type=AssetType.ACCION,
                       ticker="TCK%d" % i, currency=Currency.EUR, current_price=None)
        client.db.add(activo)
        client.db.flush()
        client.db.add(Operation(asset_id=activo.id, type=OperationType.COMPRA,
                                quantity=2.0, unit_price=50.0, date=date(2026, 1, 5)))
    client.db.commit()
    return client


def _contar_compute_position(monkeypatch) -> list[int]:
    llamadas = []
    original = portfolio.compute_position

    def espia(operaciones, *args, **kwargs):
        llamadas.append(1)
        return original(operaciones, *args, **kwargs)

    monkeypatch.setattr(portfolio, "compute_position", espia)
    return llamadas


def test_valorar_la_cartera_calcula_la_posicion_una_vez_por_activo(sin_cotizacion, monkeypatch):
    llamadas = _contar_compute_position(monkeypatch)

    compute_net_worth(sin_cotizacion.db)

    assert len(llamadas) <= 5, "5 activos, %d cálculos de posición" % len(llamadas)


def test_la_lista_de_activos_no_calcula_la_posicion_dos_veces(sin_cotizacion, monkeypatch):
    """`_row_for` pedía el valor al modelo y el resumen al servicio, y los dos
    recalculan la posición del mismo activo."""
    llamadas = _contar_compute_position(monkeypatch)

    respuesta = sin_cotizacion.get("/activos")

    assert respuesta.status_code == 200
    assert len(llamadas) <= 5, "5 activos, %d cálculos de posición" % len(llamadas)


def test_el_valor_sigue_siendo_el_mismo(sin_cotizacion):
    """Sin cotización, cada activo vale 2 unidades a coste medio 50 = 100."""
    valoracion = compute_net_worth(sin_cotizacion.db)

    assert valoracion.total == pytest.approx(500.0)
