"""Una instalación recién montada tiene que poder abrir todas las páginas.

Lo encontró el job de despliegue del CI en su primera ejecución: con la base
vacía, /analisis devolvía 500 con `KeyError: 'twr'`. No era un caso raro —es el
estado de cualquiera que clone el repositorio y arranque—, y ningún test lo veía
porque todos preparan datos antes de pedir la página.

La causa: `portfolio_evolution` tiene dos caminos y no devolvían la misma forma.
Sin operaciones emitía puntos sin las claves `twr` y `aportado`. El arranque
crea un snapshot de patrimonio, así que la lista no estaba vacía y el
`if evolution` de la vista no protegía de nada.
"""
from datetime import date

import pytest

from app.models import NetWorthSnapshot
from app.services.history import portfolio_evolution

RUTAS = [
    "/",
    "/activos",
    "/activos/duplicados",
    "/operaciones",
    "/operaciones/importar",
    "/transacciones",
    "/analisis",
    "/analisis/rebalanceo",
    "/recurrentes",
    "/categorias",
]


@pytest.mark.parametrize("ruta", RUTAS)
def test_con_la_base_vacia_todo_responde(client, ruta):
    assert client.get(ruta).status_code == 200


@pytest.fixture
def solo_un_snapshot(client):
    """El estado exacto tras el primer arranque: el lifespan guarda un snapshot
    de patrimonio y todavía no hay ninguna operación."""
    client.db.add(NetWorthSnapshot(date=date(2026, 1, 1), total_value=0.0))
    client.db.commit()
    return client


@pytest.mark.parametrize("ruta", RUTAS)
def test_con_un_snapshot_y_sin_operaciones_tambien(solo_un_snapshot, ruta):
    assert solo_un_snapshot.get(ruta).status_code == 200


def test_la_serie_tiene_las_mismas_claves_haya_o_no_operaciones(solo_un_snapshot):
    """Dos formas distintas para la misma serie es lo que causó el 500: quien la
    consume no debería tener que saber por qué camino salió."""
    sin_operaciones = portfolio_evolution(solo_un_snapshot.db)

    assert sin_operaciones
    assert set(sin_operaciones[0]) == {"fecha", "total", "invertido", "aportado", "twr"}
