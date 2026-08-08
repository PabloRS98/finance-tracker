"""[FT-M4] `/analisis/rebalanceo` calculaba la cartera entera dos veces.

La vista llamaba a `plan(db, aportacion)` **y** a
`reparto_de_aportacion(db, aportacion)`, y esta segunda empezaba llamando otra
vez a `plan`. Y `plan` recorre todos los invertibles, calcula `asset_summary`
—que reordena la lista de operaciones de cada activo— y pide un tipo de cambio
por divisa.

No se nota en el contador de sentencias SQL: la sesión ya tiene los objetos
cargados, así que la segunda pasada no vuelve a la base. Lo que se duplica es el
trabajo en CPU, y por eso aquí se cuenta el número de llamadas, que es lo que
pide el criterio de aceptación del hallazgo.
"""
from datetime import date

import pytest

from app.models import Asset, AssetType, Currency, Operation, OperationType, PesoObjetivo
from app.services import rebalanceo
from app.services.xray import invested_rows


@pytest.fixture
def cartera_con_objetivos(client, monkeypatch):
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 1.0)
    for i in range(3):
        activo = Asset(name="ACTIVO %d" % i, asset_type=AssetType.ACCION, ticker="TCK%d" % i,
                       currency=Currency.EUR, current_price=100.0)
        client.db.add(activo)
        client.db.flush()
        client.db.add(Operation(asset_id=activo.id, type=OperationType.COMPRA,
                                quantity=1.0 + i, unit_price=100.0, date=date(2026, 1, 5)))
        client.db.add(PesoObjetivo(asset_id=activo.id, porcentaje=33.0))
    client.db.commit()
    return client


def _contar_invested_rows(monkeypatch) -> list[int]:
    llamadas = []
    original = invested_rows

    def espia(db, *args, **kwargs):
        llamadas.append(1)
        return original(db, *args, **kwargs)

    monkeypatch.setattr(rebalanceo, "invested_rows", espia)
    return llamadas


def test_rebalanceo_calcula_las_posiciones_una_sola_vez(cartera_con_objetivos, monkeypatch):
    llamadas = _contar_invested_rows(monkeypatch)

    respuesta = cartera_con_objetivos.get("/analisis/rebalanceo?aportacion=1000")

    assert respuesta.status_code == 200
    assert len(llamadas) == 1, "la cartera se ha recorrido %d veces" % len(llamadas)


def test_el_reparto_da_lo_mismo_que_antes(cartera_con_objetivos):
    """Cambiar la firma no puede cambiar el resultado."""
    detalle = rebalanceo.plan(cartera_con_objetivos.db, 1000.0)

    reparto = rebalanceo.reparto_de_aportacion(detalle, 1000.0)

    assert reparto, "con tres activos desviados tiene que haber reparto"
    assert sum(fila["importe"] for fila in reparto) == pytest.approx(1000.0, abs=0.01)


def test_sin_aportacion_no_hay_reparto(cartera_con_objetivos):
    detalle = rebalanceo.plan(cartera_con_objetivos.db, 0.0)

    assert rebalanceo.reparto_de_aportacion(detalle, 0.0) == []
