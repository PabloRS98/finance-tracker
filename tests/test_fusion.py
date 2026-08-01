"""Fusión de activos duplicados.

El mismo valor comprado en dos sitios acaba como dos activos: Trade Republic
exporta por ISIN y lo llama "Apple Inc.", Revolut exporta por ticker y lo llama
"AAPL". La cartera enseña dos líneas de lo mismo y los pesos del X-Ray salen
partidos.
"""
from datetime import date

import pytest

from app.models import Account, Asset, AssetType, Currency, Operation, OperationType
from app.services.fusion import candidatos, fusionar, posicion_por_cuenta, puede_fusionar
from app.services.portfolio import compute_position


def _activo(nombre, **kwargs):
    kwargs.setdefault("asset_type", AssetType.ACCION)
    kwargs.setdefault("currency", Currency.USD)
    return Asset(name=nombre, **kwargs)


def _compra(asset, cantidad, precio, dia="2025-01-10", cuenta=None):
    return Operation(
        asset_id=asset.id, type=OperationType.COMPRA, quantity=cantidad, unit_price=precio,
        fee=0.0, date=date.fromisoformat(dia), account_id=cuenta,
    )


@pytest.fixture
def apple_duplicado(db):
    """Apple dos veces: por ISIN desde TR y por ticker desde Revolut.

    Los dos acaban con el mismo nombre porque el refresco de precios renombra
    "AAPL" con el nombre largo de Yahoo; es lo que los hace casar, ya que no
    comparten ni ISIN ni ticker."""
    tr = _activo("Apple Inc.", isin="US0378331005")
    revolut = _activo("Apple Inc.", ticker="AAPL")
    db.add_all([tr, revolut])
    db.flush()
    db.add(_compra(tr, 3, 168.25))
    db.add(_compra(revolut, 12, 189.30))
    db.commit()
    return tr, revolut


# ---------- Detección ----------

def test_se_detecta_el_duplicado_por_nombre(db, apple_duplicado):
    """Uno llega por ISIN y el otro por ticker: no comparten identificador, así
    que lo único que los relaciona es el nombre normalizado."""
    grupos = candidatos(db)

    assert len(grupos) == 1
    assert grupos[0]["motivo"] == "nombre"
    assert len(grupos[0]["activos"]) == 2


def test_el_isin_manda_sobre_el_resto(db):
    a = _activo("Apple Inc.", isin="US0378331005", ticker="EJM.DE")
    b = _activo("Apple", isin="US0378331005", ticker="AAPL")
    db.add_all([a, b])
    db.commit()

    grupo = candidatos(db)[0]

    assert grupo["motivo"] == "ISIN"
    assert grupo["clave"] == "US0378331005"


def test_un_activo_solo_no_es_duplicado(db):
    db.add(_activo("Apple Inc.", isin="US0378331005"))
    db.commit()

    assert candidatos(db) == []


def test_un_activo_no_sale_en_dos_grupos(db):
    """Si casara por ISIN y también por nombre, fusionarlo desde dos sitios
    dejaría el segundo intento apuntando a un activo ya borrado."""
    a = _activo("Apple Inc.", isin="US0378331005")
    b = _activo("Apple Inc.", isin="US0378331005")
    c = _activo("Apple Inc.")
    db.add_all([a, b, c])
    db.commit()

    ids = [x.id for g in candidatos(db) for x in g["activos"]]

    assert len(ids) == len(set(ids))


def test_las_divisas_distintas_se_marcan_como_no_fusionables(db):
    db.add_all([
        _activo("Apple Inc.", isin="US0378331005", currency=Currency.USD),
        _activo("Apple Inc.", isin="US0378331005", currency=Currency.EUR),
    ])
    db.commit()

    grupo = candidatos(db)[0]

    assert grupo["fusionable"] is False
    assert grupo["divisas"] == ["EUR", "USD"]


# ---------- Guardas ----------

def test_no_se_fusionan_divisas_distintas(db):
    """Mismo motivo que al importar: las operaciones heredan la divisa del
    activo, así que juntarlas mezclaría dos escalas de precio."""
    a = _activo("Apple", currency=Currency.USD)
    b = _activo("Apple", currency=Currency.EUR)
    db.add_all([a, b])
    db.commit()

    motivo = puede_fusionar(a, [b])

    assert motivo is not None and "divisas distintas" in motivo


def test_no_se_fusiona_una_accion_con_una_cripto(db):
    a = _activo("Solana", asset_type=AssetType.ACCION)
    b = _activo("Solana", asset_type=AssetType.CRIPTO)
    db.add_all([a, b])
    db.commit()

    assert puede_fusionar(a, [b]) is not None


def test_un_activo_no_se_fusiona_consigo_mismo(db):
    a = _activo("Apple")
    db.add(a)
    db.commit()

    assert puede_fusionar(a, [a]) is not None


# ---------- Fusión ----------

def test_fusionar_junta_las_operaciones(db, apple_duplicado):
    tr, revolut = apple_duplicado

    resumen = fusionar(db, tr, [revolut])

    assert resumen["movidas"] == 1
    assert db.query(Asset).count() == 1
    assert db.query(Operation).count() == 2, "no se pierde ninguna operación"
    assert len(db.query(Asset).one().operations) == 2


def test_la_posicion_resultante_es_la_suma(db, apple_duplicado):
    """No se recalcula nada: la posición se deriva de las operaciones, así que
    juntarlas da el coste medio ponderado correcto."""
    tr, revolut = apple_duplicado

    fusionar(db, tr, [revolut])

    posicion = compute_position(db.query(Asset).one().operations)
    assert posicion.quantity == 15
    # (3 * 168.25 + 12 * 189.30) / 15
    assert posicion.avg_cost == pytest.approx(185.09, abs=0.01)


def test_el_destino_hereda_el_identificador_que_le_faltaba(db, apple_duplicado):
    """Es lo que evita que el duplicado reaparezca en la siguiente importación."""
    tr, revolut = apple_duplicado

    fusionar(db, tr, [revolut])

    resultado = db.query(Asset).one()
    assert resultado.isin == "US0378331005"
    assert resultado.ticker == "AAPL"


def test_la_cantidad_manual_se_limpia_al_fusionar(db, apple_duplicado):
    """Heredada de la v2: si se quedara, se sumaría a la posición real."""
    tr, revolut = apple_duplicado
    tr.quantity = 99
    db.commit()

    fusionar(db, tr, [revolut])

    assert db.query(Asset).one().quantity is None


# ---------- Desglose por cuenta ----------

def test_el_desglose_reparte_por_broker(db):
    cuenta_tr = Account(name="Trade Republic")
    cuenta_rev = Account(name="Revolut")
    db.add_all([cuenta_tr, cuenta_rev])
    db.flush()
    asset = _activo("Apple Inc.", ticker="AAPL", current_price=200.0)
    db.add(asset)
    db.flush()
    db.add(_compra(asset, 3, 168.25, cuenta=cuenta_tr.id))
    db.add(_compra(asset, 12, 189.30, cuenta=cuenta_rev.id))
    db.commit()

    filas = {f["cuenta"]: f for f in posicion_por_cuenta(asset)}

    assert filas["Trade Republic"]["cantidad"] == 3
    assert filas["Revolut"]["cantidad"] == 12
    assert filas["Revolut"]["valor"] == pytest.approx(2400.0)


def test_sin_varias_cuentas_no_hay_desglose(db):
    """Todo en el mismo sitio: una tabla de una fila no aporta nada."""
    asset = _activo("Apple Inc.", ticker="AAPL")
    db.add(asset)
    db.flush()
    db.add(_compra(asset, 3, 168.25))
    db.commit()

    assert posicion_por_cuenta(asset) == []


# ---------- Extremo a extremo ----------

def test_fusionar_desde_la_pagina(client):
    a = _activo("Apple Inc.", isin="US0378331005")
    b = _activo("Apple Inc.", isin="US0378331005")
    client.db.add_all([a, b])
    client.db.flush()
    client.db.add(_compra(b, 5, 100.0))
    client.db.commit()

    respuesta = client.post_form("/activos/duplicados/fusionar",
                                 data={"destino_id": a.id, "origen_ids": [b.id]},
                                 follow_redirects=False)

    assert respuesta.status_code == 303
    assert client.db.query(Asset).count() == 1
    assert len(client.db.query(Asset).one().operations) == 1


def test_la_pagina_de_duplicados_responde(client):
    assert client.get("/activos/duplicados").status_code == 200
