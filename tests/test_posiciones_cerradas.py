"""Posiciones cerradas: activos vendidos enteros.

Valen 0 y no suman a ningún subtotal, pero seguían en medio de la lista de
activos. Se apartan a su propia sección plegada sin tocar sus operaciones: son
las que sostienen la rentabilidad histórica.

De paso dejan de salir como duplicados. Un traspaso de bróker —vender en uno y
comprar en otro el mismo día— dejaba dos activos que /activos/duplicados marcaba
como repetidos, y el aviso era falso: no son la misma posición, sino dos etapas
de la misma historia.

Los tickers de aquí son inventados, como pide la sección "Datos reales" del
README.
"""
from datetime import date

import pytest

from app.models import Asset, AssetType, Currency, Operation, OperationType, TransactionStatus
from app.services.fusion import candidatos
from app.services.portfolio import compute_position, posicion_cerrada


def _activo(db, nombre, **kwargs):
    kwargs.setdefault("asset_type", AssetType.ACCION)
    kwargs.setdefault("currency", Currency.USD)
    asset = Asset(name=nombre, **kwargs)
    db.add(asset)
    db.flush()
    return asset


def _op(db, asset, tipo, cantidad, precio, dia="2025-06-30"):
    db.add(Operation(
        asset_id=asset.id, type=tipo, quantity=cantidad, unit_price=precio, fee=0.0,
        date=date.fromisoformat(dia), status=TransactionStatus.CONFIRMADO,
    ))
    db.flush()


# ---------- El predicado ----------

def test_vender_toda_la_posicion_la_deja_cerrada(db):
    asset = _activo(db, "Ejemplo Inc.", ticker="EJEM")
    _op(db, asset, OperationType.COMPRA, 10, 150.0, "2024-01-15")
    _op(db, asset, OperationType.VENTA, 10, 200.0, "2025-06-30")
    db.commit()

    assert posicion_cerrada(compute_position(asset.operations))


def test_una_posicion_viva_no_esta_cerrada(db):
    asset = _activo(db, "Muestra SA", ticker="MSTA.DE", currency=Currency.EUR)
    _op(db, asset, OperationType.COMPRA, 4, 230.0)
    db.commit()

    assert not posicion_cerrada(compute_position(asset.operations))


def test_un_activo_sin_operaciones_no_esta_cerrado(db):
    """Dado de alta y aún sin comprar: tampoco tiene cantidad, pero no hay
    historial detrás que apartar."""
    asset = _activo(db, "Prueba Corp", ticker="PRUE")
    db.commit()

    assert not posicion_cerrada(compute_position(asset.operations))


def test_el_resto_de_coma_flotante_cuenta_como_cerrada(db):
    """Vender en varios trozos deja la cantidad en 1e-16, no en 0 clavado."""
    asset = _activo(db, "Ejemplo Inc.", ticker="EJEM")
    _op(db, asset, OperationType.COMPRA, 8.367, 180.0, "2024-01-15")
    for trozo in (0.1, 0.2, 8.067):
        _op(db, asset, OperationType.VENTA, trozo, 271.58)
    db.commit()

    pos = compute_position(asset.operations)
    assert pos.quantity != 0.0 or posicion_cerrada(pos), "o sale 0 clavado o la tolerancia lo cubre"
    assert posicion_cerrada(pos)


def test_las_operaciones_pendientes_no_cierran_nada(db):
    """Una venta sin confirmar no ha ocurrido: la posición sigue viva."""
    asset = _activo(db, "Prueba Corp", ticker="PRUE")
    _op(db, asset, OperationType.COMPRA, 5, 300.0)
    db.add(Operation(
        asset_id=asset.id, type=OperationType.VENTA, quantity=5, unit_price=700.0, fee=0.0,
        date=date(2025, 8, 1), status=TransactionStatus.PENDIENTE,
    ))
    db.commit()

    assert not posicion_cerrada(compute_position(asset.operations))


# ---------- Duplicados ----------

@pytest.fixture
def traspaso_de_broker(db):
    """Vendido entero en un bróker y comprado en otro el mismo día. Los dos
    activos comparten nombre, así que el detector los casaba."""
    viejo = _activo(db, "Ejemplo Inc.", ticker="EJEM", currency=Currency.USD)
    _op(db, viejo, OperationType.COMPRA, 10, 100.0, "2024-01-15")
    _op(db, viejo, OperationType.VENTA, 10, 180.0, "2025-06-30")

    nuevo = _activo(db, "Ejemplo Inc.", ticker="EJM.DE", currency=Currency.EUR)
    _op(db, nuevo, OperationType.COMPRA, 12, 150.0, "2025-06-30")
    db.commit()
    return viejo, nuevo


def test_un_traspaso_de_broker_no_se_avisa_como_duplicado(db, traspaso_de_broker):
    assert candidatos(db) == []


def test_dos_posiciones_vivas_siguen_saliendo_como_duplicado(db):
    """El caso que la detección venía a resolver no se rompe."""
    por_isin = _activo(db, "Ejemplo Inc.", isin="XX0000000001", currency=Currency.EUR)
    _op(db, por_isin, OperationType.COMPRA, 3, 168.25, "2025-01-10")
    por_ticker = _activo(db, "Ejemplo Inc.", ticker="EJEM", currency=Currency.EUR)
    _op(db, por_ticker, OperationType.COMPRA, 12, 189.30, "2025-01-10")
    db.commit()

    grupos = candidatos(db)

    assert len(grupos) == 1
    assert {a.id for a in grupos[0]["activos"]} == {por_isin.id, por_ticker.id}


# ---------- La lista de activos ----------

def test_las_cerradas_salen_aparte_y_no_en_la_cartera(client):
    viva = _activo(client.db, "Muestra SA", ticker="MSTA.DE", currency=Currency.EUR)
    _op(client.db, viva, OperationType.COMPRA, 4, 263.05)
    cerrada = _activo(client.db, "Ejemplo Inc.", ticker="EJEM")
    _op(client.db, cerrada, OperationType.COMPRA, 2, 400.0, "2024-02-01")
    _op(client.db, cerrada, OperationType.VENTA, 2, 900.0, "2025-06-30")
    client.db.commit()

    html = client.get("/activos").text

    assert "Posiciones cerradas" in html
    # La cerrada aparece una sola vez (en su sección), no también en Inversión
    assert html.count(">Ejemplo Inc.<") == 1
    assert "Muestra SA" in html


def test_sin_cerradas_no_aparece_la_seccion(client):
    viva = _activo(client.db, "Muestra SA", ticker="MSTA.DE", currency=Currency.EUR)
    _op(client.db, viva, OperationType.COMPRA, 4, 263.05)
    client.db.commit()

    assert "Posiciones cerradas" not in client.get("/activos").text


def test_apartarlas_no_cambia_el_patrimonio(client):
    """Valen 0, así que el total tiene que salir idéntico con y sin ellas."""
    viva = _activo(client.db, "Muestra SA", ticker="MSTA.DE", currency=Currency.EUR,
                   current_price=263.05)
    _op(client.db, viva, OperationType.COMPRA, 4, 263.05)
    client.db.commit()
    solo_viva = client.get("/activos").text

    cerrada = _activo(client.db, "Ejemplo Inc.", ticker="EJEM", current_price=900.0)
    _op(client.db, cerrada, OperationType.COMPRA, 2, 400.0, "2024-02-01")
    _op(client.db, cerrada, OperationType.VENTA, 2, 900.0, "2025-06-30")
    client.db.commit()
    con_cerrada = client.get("/activos").text

    total = 'data-money="%s"' % (4 * 263.05)
    assert total in solo_viva and total in con_cerrada


def test_la_cerrada_enseña_su_pnl_realizado(client):
    """Es lo que justifica conservarla: 2 x (900 - 400) = 1.000."""
    cerrada = _activo(client.db, "Ejemplo Inc.", ticker="EJEM")
    _op(client.db, cerrada, OperationType.COMPRA, 2, 400.0, "2024-02-01")
    _op(client.db, cerrada, OperationType.VENTA, 2, 900.0, "2025-06-30")
    client.db.commit()

    html = client.get("/activos").text

    assert 'data-money="1000.0"' in html
    assert "30/06/2025" in html
