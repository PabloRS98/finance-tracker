"""[FT-M1] El dashboard calcula lo mismo con menos trabajo.

Dos cambios, los dos mecánicos:

- El desglose por tipo de activo sale de la valoración que ya se hace, en vez de
  recorrer los activos por cuarta vez pidiendo otra vez un tipo de cambio por
  cada uno.
- Los seis meses de la gráfica de ingresos/gastos salen de **una** consulta
  agrupada, no de seis: eran seis viajes a la base para pintar seis puntos, y
  además traían las filas enteras para sumarlas en Python.

Lo que estos tests vigilan es que los números no cambien. Una optimización que
altera el resultado no es una optimización.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models import (
    Asset,
    AssetType,
    Currency,
    Transaction,
    TransactionStatus,
    TransactionType,
)
from app.routers.dashboard import series_mensuales
from app.services.scheduler import compute_net_worth


@pytest.fixture
def con_gastos(client):
    hoy = date.today()
    # Dos meses distintos dentro de la ventana de seis, con ingresos y gastos
    for desplazamiento, gasto, ingreso in ((0, 30, 100), (1, 50, 200)):
        mes = hoy.month - desplazamiento
        anio = hoy.year
        if mes <= 0:
            mes += 12
            anio -= 1
        client.db.add(Transaction(date=date(anio, mes, 5), amount=Decimal(gasto),
                                  type=TransactionType.GASTO, description="g"))
        client.db.add(Transaction(date=date(anio, mes, 6), amount=Decimal(ingreso),
                                  type=TransactionType.INGRESO, description="i"))
    client.db.commit()
    return client


def _meses_recientes(cuantos: int = 2) -> list[tuple[int, int]]:
    hoy = date.today()
    meses = []
    for desplazamiento in range(cuantos - 1, -1, -1):
        mes, anio = hoy.month - desplazamiento, hoy.year
        if mes <= 0:
            mes += 12
            anio -= 1
        meses.append((anio, mes))
    return meses


def test_la_serie_mensual_suma_lo_mismo_que_las_filas(con_gastos):
    """La consulta agrupada tiene que dar lo mismo que sumar a mano."""
    ingresos, gastos = series_mensuales(con_gastos.db, _meses_recientes())

    # El fixture pone 50/200 en el mes anterior y 30/100 en el actual
    assert gastos == [Decimal("50.00"), Decimal("30.00")]
    assert ingresos == [Decimal("200.00"), Decimal("100.00")]


def test_un_mes_sin_movimientos_sale_a_cero(con_gastos):
    """La consulta agrupada solo devuelve los meses que tienen filas: los que
    faltan hay que rellenarlos, o la gráfica se descuadra con sus etiquetas."""
    ingresos, gastos = series_mensuales(con_gastos.db, _meses_recientes(6))

    assert len(gastos) == 6 and len(ingresos) == 6
    assert gastos[0] == Decimal("0.00")


def test_los_pendientes_no_entran_en_la_serie(con_gastos):
    """El filtro por estado tiene que sobrevivir al cambio de consulta: una
    transacción pendiente todavía no ha ocurrido."""
    hoy = date.today()
    con_gastos.db.add(Transaction(date=date(hoy.year, hoy.month, 7), amount=Decimal("9999"),
                                  type=TransactionType.GASTO, description="pendiente",
                                  status=TransactionStatus.PENDIENTE))
    con_gastos.db.commit()

    _, gastos = series_mensuales(con_gastos.db, _meses_recientes())

    assert gastos[-1] == Decimal("30.00"), "la pendiente se ha colado en la serie"


def test_el_desglose_por_tipo_sale_de_la_valoracion(client, monkeypatch):
    """Antes se recorrían los activos otra vez para lo mismo."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)
    client.db.add(Asset(name="CUENTA", asset_type=AssetType.CUENTA,
                        currency=Currency.EUR, manual_value=1000.0))
    client.db.add(Asset(name="PISO", asset_type=AssetType.OTRO,
                        currency=Currency.EUR, manual_value=200000.0))
    client.db.commit()

    valoracion = compute_net_worth(client.db)

    assert valoracion.por_tipo[AssetType.CUENTA] == pytest.approx(1000.0)
    assert valoracion.por_tipo[AssetType.OTRO] == pytest.approx(200000.0)
    assert sum(valoracion.por_tipo.values()) == pytest.approx(valoracion.total)


def test_los_activos_sin_tipo_de_cambio_no_entran_en_el_desglose(client, monkeypatch):
    """Mismo criterio que el total: fuera antes que contarlo 1:1."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate",
                        lambda origen, destino: 1.0 if origen == destino else None)
    client.db.add(Asset(name="EUROS", asset_type=AssetType.CUENTA,
                        currency=Currency.EUR, manual_value=500.0))
    client.db.add(Asset(name="DOLARES", asset_type=AssetType.CUENTA,
                        currency=Currency.USD, manual_value=500.0))
    client.db.commit()

    valoracion = compute_net_worth(client.db)

    assert valoracion.por_tipo[AssetType.CUENTA] == pytest.approx(500.0)
    assert "USD" in valoracion.missing


def test_un_activo_sin_valor_no_ensucia_el_desglose(client, monkeypatch):
    """Se conserva el filtro de valor positivo que tenía el bucle original."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)
    client.db.add(Asset(name="VACIA", asset_type=AssetType.CUENTA,
                        currency=Currency.EUR, manual_value=0.0))
    client.db.commit()

    assert compute_net_worth(client.db).por_tipo == {}
