"""Filtros por chips y separadores de mes en el historial de operaciones."""
from datetime import date

import pytest

from app.models import Account, Asset, AssetType, Currency, Operation, OperationType, TransactionStatus
from app.templating import mes_anio


@pytest.fixture
def cartera(client):
    tr = Account(name="Trade Republic")
    revolut = Account(name="Revolut")
    client.db.add_all([tr, revolut])
    client.db.flush()
    asset = Asset(name="MUESTRA Corporation", asset_type=AssetType.ACCION,
                  currency=Currency.USD, ticker="NVDA", current_price=100.0)
    client.db.add(asset)
    client.db.flush()
    client.db.add_all([
        Operation(asset_id=asset.id, type=OperationType.COMPRA, date=date(2026, 3, 10),
                  quantity=2, unit_price=90.0, account_id=tr.id,
                  status=TransactionStatus.CONFIRMADO),
        Operation(asset_id=asset.id, type=OperationType.VENTA, date=date(2026, 2, 5),
                  quantity=1, unit_price=110.0, account_id=revolut.id,
                  status=TransactionStatus.CONFIRMADO),
        Operation(asset_id=asset.id, type=OperationType.COMPRA, date=date(2026, 2, 20),
                  quantity=3, unit_price=80.0, account_id=revolut.id,
                  status=TransactionStatus.CONFIRMADO),
    ])
    client.db.commit()
    client.cuentas = {"tr": tr.id, "revolut": revolut.id}
    return client


# ---------- Filtros ----------

def test_sin_filtro_salen_todas(cartera):
    html = cartera.get("/operaciones").text

    assert html.count('data-col="Cantidad"') == 3


def test_filtrar_por_compras(cartera):
    html = cartera.get("/operaciones?tipo=compra").text

    assert html.count('data-col="Cantidad"') == 2


def test_filtrar_por_ventas(cartera):
    html = cartera.get("/operaciones?tipo=venta").text

    assert html.count('data-col="Cantidad"') == 1


def test_filtrar_por_cuenta(cartera):
    html = cartera.get("/operaciones?cuenta=%d" % cartera.cuentas["revolut"]).text

    assert html.count('data-col="Cantidad"') == 2


def test_los_filtros_se_combinan(cartera):
    """Compras de Revolut: una sola de las tres."""
    html = cartera.get("/operaciones?tipo=compra&cuenta=%d" % cartera.cuentas["revolut"]).text

    assert html.count('data-col="Cantidad"') == 1


def test_un_tipo_inventado_no_filtra_ni_revienta(cartera):
    respuesta = cartera.get("/operaciones?tipo=loquesea")

    assert respuesta.status_code == 200
    assert respuesta.text.count('data-col="Cantidad"') == 3


def test_el_chip_activo_queda_marcado(cartera):
    html = cartera.get("/operaciones?tipo=compra").text

    assert 'class="chip active"' in html


def test_todas_esta_marcado_cuando_no_hay_filtro(cartera):
    html = cartera.get("/operaciones").text
    chip_todas = html.split(">Todas<")[0].rsplit("<a", 1)[1]

    assert "active" in chip_todas


# ---------- Separadores de mes ----------

def test_el_historial_separa_por_mes(cartera):
    html = cartera.get("/operaciones").text

    assert "Marzo 2026" in html
    assert "Febrero 2026" in html
    assert html.count('class="separador-mes"') == 2, "un separador por mes, no por fila"


def test_los_meses_van_en_espanol():
    """El contenedor corre en locale C: strftime("%B") devolvería "March"."""
    assert mes_anio(date(2026, 3, 10)) == "Marzo 2026"
    assert mes_anio(date(2026, 12, 1)) == "Diciembre 2026"


# ---------- Formulario plegado ----------

def test_el_formulario_va_plegado(cartera):
    """A esta página se entra a consultar el historial, no a registrar."""
    html = cartera.get("/operaciones").text
    detalle = html.split("Registrar operación")[0].rsplit("<details", 1)[1]

    assert "open" not in detalle


def test_se_abre_al_venir_filtrado_por_un_activo(cartera):
    """Es el caso de "pasar a cartera" desde la watchlist: vienes justo a
    registrar la compra de ese activo."""
    asset = cartera.db.query(Asset).one()

    html = cartera.get("/operaciones?activo=%d" % asset.id).text
    detalle = html.split("Registrar operación")[0].rsplit("<details", 1)[1]

    assert "open" in detalle
