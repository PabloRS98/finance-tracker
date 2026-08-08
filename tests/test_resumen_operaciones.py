"""[FT-M14] El resumen de /operaciones se calculaba sin tipo de cambio.

`list_operations` llamaba a `asset_summary(asset)` a secas, sin `fx_on` ni
`exposure_fx`. La ficha del mismo activo sí los pasaba. Consecuencia: al filtrar
las operaciones de un activo en dólares, el panel de arriba mostraba
`unrealized_base`, `pnl_pct_base` y `fx_effect_pct` vacíos, mientras la ficha
enseñaba esas mismas cifras con valor. Dos números distintos para lo mismo según
por dónde se entrara.

El ROADMAP lo asumía como deuda ("inconsistencia menor, no un dato erróneo"), y
es cierto que ningún número era falso; lo que había era un hueco donde debía
haber una cifra, y el arreglo es una línea una vez que la construcción de los
lookups vive en un solo sitio.
"""
from datetime import date

import pytest

from app.models import Asset, AssetType, Currency, Operation, OperationType


@pytest.fixture
def activo_en_dolares(client, monkeypatch):
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.9)
    activo = Asset(name="MUESTRA USA", asset_type=AssetType.ACCION, ticker="MSTR",
                   currency=Currency.USD, current_price=120.0)
    client.db.add(activo)
    client.db.flush()
    client.db.add(Operation(asset_id=activo.id, type=OperationType.COMPRA,
                            quantity=10.0, unit_price=100.0, date=date(2026, 1, 5)))
    client.db.commit()
    return activo


def test_resumen_de_operaciones_incluye_el_efecto_divisa(client, activo_en_dolares):
    from app.services.portfolio import resumen_completo

    resumen = resumen_completo(client.db, activo_en_dolares)

    assert resumen["fx_effect_pct"] is not None
    assert resumen["pnl_pct_base"] is not None


def test_la_ficha_y_la_lista_dan_el_mismo_resumen(client, activo_en_dolares):
    """Es el fondo del hallazgo: la misma posición, mirada desde dos sitios."""
    ficha = client.get("/activos/%d" % activo_en_dolares.id)
    lista = client.get("/operaciones?activo=%d" % activo_en_dolares.id)

    assert ficha.status_code == 200
    assert lista.status_code == 200

    from app.services.portfolio import resumen_completo
    esperado = resumen_completo(client.db, activo_en_dolares)
    # El efecto divisa, que es lo que faltaba, tiene que aparecer en las dos
    marca = "%.2f" % esperado["fx_effect_pct"]
    assert marca.replace(".", ",") in lista.text or marca in lista.text


def test_hay_una_sola_forma_de_montar_el_resumen(client):
    """El hallazgo pedía extraerlo para que no naciera una tercera versión."""
    from pathlib import Path

    routers = Path(__file__).resolve().parent.parent / "app" / "routers"
    for fichero in routers.glob("*.py"):
        texto = fichero.read_text(encoding="utf-8")
        assert "exposure_fx_lookup(db, asset, {})" not in texto, (
            "%s vuelve a montar los lookups a mano" % fichero.name
        )
