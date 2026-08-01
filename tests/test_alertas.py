"""Alertas de precio por Telegram.

Se evalúan dentro del job que refresca precios, que es el único momento con
cotización nueva. Lo delicado no es la condición sino el rearme: sin él, un
activo que cruza a la baja sigue por debajo durante horas y avisaría en cada
refresco.
"""
import pytest

from app.models import Alerta, Asset, AssetType, Currency, TipoAlerta
from app.services.alertas import comprobar, mensaje


@pytest.fixture
def nvidia(db):
    asset = Asset(name="MUESTRA Corporation", asset_type=AssetType.ACCION,
                  currency=Currency.USD, ticker="NVDA",
                  current_price=100.0, previous_close=100.0)
    db.add(asset)
    db.commit()
    return asset


def _alerta(db, asset, tipo, valor):
    a = Alerta(asset_id=asset.id, tipo=tipo, valor=valor)
    db.add(a)
    db.commit()
    return a


# ---------- Condiciones ----------

def test_avisa_al_subir_por_encima(db, nvidia):
    _alerta(db, nvidia, TipoAlerta.POR_ENCIMA, 120.0)

    assert comprobar(db) == []          # a 100 no se cumple

    nvidia.current_price = 125.0
    db.commit()
    avisos = comprobar(db)

    assert len(avisos) == 1
    assert "MUESTRA" in avisos[0] and "por encima" in avisos[0]


def test_avisa_al_bajar_por_debajo(db, nvidia):
    _alerta(db, nvidia, TipoAlerta.POR_DEBAJO, 90.0)
    nvidia.current_price = 85.0
    db.commit()

    avisos = comprobar(db)

    assert len(avisos) == 1 and "por debajo" in avisos[0]


def test_avisa_por_caida_diaria(db, nvidia):
    """La caída se mide contra el cierre anterior, no contra el objetivo."""
    _alerta(db, nvidia, TipoAlerta.CAIDA_DIARIA, 5.0)
    nvidia.previous_close = 100.0
    nvidia.current_price = 93.0        # -7%
    db.commit()

    avisos = comprobar(db)

    assert len(avisos) == 1 and "cae hoy" in avisos[0]


def test_una_caida_menor_que_el_umbral_no_avisa(db, nvidia):
    _alerta(db, nvidia, TipoAlerta.CAIDA_DIARIA, 5.0)
    nvidia.current_price = 97.0        # -3%
    db.commit()

    assert comprobar(db) == []


def test_sin_precio_no_se_evalua(db, nvidia):
    """Un activo recién creado no tiene cotización: no puede disparar nada."""
    _alerta(db, nvidia, TipoAlerta.POR_DEBAJO, 90.0)
    nvidia.current_price = None
    db.commit()

    assert comprobar(db) == []


def test_las_alertas_desactivadas_se_ignoran(db, nvidia):
    alerta = _alerta(db, nvidia, TipoAlerta.POR_ENCIMA, 50.0)
    alerta.activa = False
    db.commit()

    assert comprobar(db) == []


# ---------- Rearme: lo que evita el spam ----------

def test_no_repite_mientras_la_condicion_siga_cumpliendose(db, nvidia):
    _alerta(db, nvidia, TipoAlerta.POR_ENCIMA, 90.0)
    nvidia.current_price = 120.0
    db.commit()

    assert len(comprobar(db)) == 1, "primera vez sí avisa"
    assert comprobar(db) == [], "sigue por encima: no repite"
    assert comprobar(db) == []


def test_se_rearma_al_dejar_de_cumplirse(db, nvidia):
    alerta = _alerta(db, nvidia, TipoAlerta.POR_ENCIMA, 90.0)
    nvidia.current_price = 120.0
    db.commit()
    comprobar(db)
    assert alerta.ultimo_disparo is not None

    nvidia.current_price = 80.0        # vuelve por debajo
    db.commit()
    comprobar(db)
    assert alerta.ultimo_disparo is None, "queda lista para el siguiente cruce"

    nvidia.current_price = 130.0       # cruza otra vez
    db.commit()
    assert len(comprobar(db)) == 1


# ---------- Mensaje ----------

def test_el_mensaje_lleva_nombre_precio_y_divisa(db, nvidia):
    alerta = _alerta(db, nvidia, TipoAlerta.POR_DEBAJO, 90.0)
    nvidia.current_price = 85.5
    db.commit()

    texto = mensaje(alerta)

    assert "MUESTRA Corporation" in texto
    assert "85,50" in texto and "USD" in texto


# ---------- Alta y baja desde la ficha ----------

def test_crear_alerta_desde_la_ficha(client):
    asset = Asset(name="MUESTRA", asset_type=AssetType.ACCION, currency=Currency.USD,
                  ticker="NVDA", current_price=100.0)
    client.db.add(asset)
    client.db.commit()

    client.post_form("/activos/%d/alertas" % asset.id,
                     data={"tipo": "por_encima", "valor": "150"}, follow_redirects=False)

    alerta = client.db.query(Alerta).one()
    assert alerta.tipo == TipoAlerta.POR_ENCIMA
    assert alerta.valor == 150.0
    assert alerta.activa is True


def test_un_valor_no_positivo_se_rechaza(client):
    """Un objetivo de 0 se cumpliría siempre y avisaría en cada refresco."""
    asset = Asset(name="MUESTRA", asset_type=AssetType.ACCION, currency=Currency.USD, ticker="NVDA")
    client.db.add(asset)
    client.db.commit()

    client.post_form("/activos/%d/alertas" % asset.id,
                     data={"tipo": "por_debajo", "valor": "0"}, follow_redirects=False)

    assert client.db.query(Alerta).count() == 0


def test_eliminar_alerta(client):
    asset = Asset(name="MUESTRA", asset_type=AssetType.ACCION, currency=Currency.USD, ticker="NVDA")
    client.db.add(asset)
    client.db.flush()
    alerta = Alerta(asset_id=asset.id, tipo=TipoAlerta.POR_ENCIMA, valor=10.0)
    client.db.add(alerta)
    client.db.commit()

    client.post_form("/activos/alertas/%d/eliminar" % alerta.id, follow_redirects=False)

    assert client.db.query(Alerta).count() == 0
