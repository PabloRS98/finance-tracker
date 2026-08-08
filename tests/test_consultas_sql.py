"""Conteo de sentencias SQL: la línea base contra la que medir la Fase 4.

Los hallazgos de rendimiento de la auditoría están razonados sobre lectura de
código, no sobre medición. Estos tests fijan el número de partida de las páginas
más caras, para que cualquier optimización tenga que demostrar que bajó — y para
que un cambio futuro que las encarezca lo diga en voz alta en vez de degradarse
en silencio.

Los topes son deliberadamente flojos respecto a lo medido: lo que se vigila es
el orden de magnitud, no el número exacto, que cambia con cada activo que se
añada al fixture.
"""
from datetime import date

import pytest

from app.medicion import contar_consultas
from app.models import Asset, AssetType, Currency, Operation, OperationType, Transaction, TransactionType


@pytest.fixture
def cartera(client):
    """Una cartera pequeña pero realista: cinco activos con operaciones y gastos."""
    for i in range(5):
        activo = Asset(
            name="ACTIVO %d" % i, asset_type=AssetType.ACCION, ticker="TCK%d" % i,
            currency=Currency.EUR if i % 2 else Currency.USD, current_price=100.0 + i,
        )
        client.db.add(activo)
        client.db.flush()
        for mes in range(1, 4):
            client.db.add(Operation(
                asset_id=activo.id, type=OperationType.COMPRA, quantity=1.0 + i,
                unit_price=90.0 + i, date=date(2026, mes, 5),
            ))
    for mes in range(1, 7):
        client.db.add(Transaction(
            date=date(2026, mes, 10), amount=25, type=TransactionType.GASTO,
            description="gasto %d" % mes,
        ))
    client.db.commit()
    return client


def _consultas_de(client, ruta: str) -> int:
    motor = client.db.get_bind()
    with contar_consultas(motor) as consultas:
        respuesta = client.get(ruta)
    assert respuesta.status_code == 200, ruta
    return consultas.total


@pytest.mark.parametrize(("ruta", "tope"), [
    ("/", 40),                        # 39 antes de FT-M1, 28 después
    ("/activos", 15),                 # medido: 4
    ("/analisis", 30),                # medido: 15
    ("/analisis/rebalanceo", 15),     # medido: 4
    ("/transacciones", 15),           # medido: 4
    ("/operaciones", 15),             # medido: 5
])
def test_las_paginas_caras_no_se_desmadran(cartera, ruta, tope):
    assert _consultas_de(cartera, ruta) <= tope


def test_el_dashboard_es_la_pagina_mas_cara(cartera):
    """Deja escrito cuál hay que vigilar, que es lo que motiva FT-M1.

    Con cinco activos la portada cuesta ~10 veces más que cualquier otra
    página, y el coste crece con el histórico sin nada que lo frene."""
    portada = _consultas_de(cartera, "/")

    for otra in ("/activos", "/transacciones", "/operaciones"):
        assert _consultas_de(cartera, otra) < portada


def test_el_contador_cuenta_algo(cartera):
    """Un contador que devuelve siempre 0 pasaría todos los tests de arriba."""
    assert _consultas_de(cartera, "/") > 0


def test_el_contador_se_restaura_al_salir(cartera):
    """Anidar dos mediciones no puede dejar el contador roto."""
    motor = cartera.db.get_bind()

    with contar_consultas(motor) as externa:
        cartera.get("/salud")
        with contar_consultas(motor) as interna:
            cartera.get("/salud")

    assert interna.total > 0
    assert externa.total > 0
