"""[FT-A3] Borrar un activo no puede dejar alertas ni pesos objetivo huérfanos.

`Asset.operations` tenía cascada; `Alerta` y `PesoObjetivo` no. Y SQLite no
aplica claves foráneas salvo que se active el PRAGMA, así que `db.delete(asset)`
dejaba filas apuntando a un id inexistente.

Lo grave venía después: `_se_cumple` hacía `alerta.asset.current_price` sobre un
`asset` que ya era None, el `AttributeError` quedaba enterrado en el
`try/except` del scheduler, y **las alertas dejaban de comprobarse para todos
los activos, indefinidamente**, con una sola línea en el log como rastro.

Se arregla en las tres capas porque cada una tapa un hueco distinto: la cascada
en la relación (lo que usa la app), el ON DELETE en el esquema (lo que protege
si algún día se activa el PRAGMA o se borra por SQL), y la defensa en
`_se_cumple` para que una fila huérfana que llegue por cualquier otra vía no
tumbe el ciclo entero.
"""
import pytest
from alembic import command
from sqlalchemy import create_engine, text

from app.database import _config_alembic
from app.models import Alerta, Asset, AssetType, Currency, PesoObjetivo, TipoAlerta
from app.services import alertas

REVISION_ANTERIOR = "e0a5c7f2e8c6"
REVISION_CASCADAS = "b7c3a91d4e52"


def _activo(db, nombre="Activo de prueba"):
    asset = Asset(
        name=nombre, asset_type=AssetType.ACCION, ticker="XXXX",
        currency=Currency.EUR, current_price=10.0,
    )
    db.add(asset)
    db.commit()
    return asset


def test_borrar_activo_borra_sus_alertas(db):
    asset = _activo(db)
    db.add(Alerta(asset_id=asset.id, tipo=TipoAlerta.POR_ENCIMA, valor=1.0))
    db.commit()

    db.delete(asset)
    db.commit()

    assert db.query(Alerta).count() == 0


def test_borrar_activo_borra_sus_pesos_objetivo(db):
    asset = _activo(db)
    db.add(PesoObjetivo(asset_id=asset.id, porcentaje=25.0))
    db.commit()

    db.delete(asset)
    db.commit()

    assert db.query(PesoObjetivo).count() == 0


def test_borrar_un_activo_no_toca_las_alertas_de_otro(db):
    uno = _activo(db, "Uno")
    otro = _activo(db, "Otro")
    db.add(Alerta(asset_id=uno.id, tipo=TipoAlerta.POR_ENCIMA, valor=1.0))
    db.add(Alerta(asset_id=otro.id, tipo=TipoAlerta.POR_DEBAJO, valor=2.0))
    db.commit()

    db.delete(uno)
    db.commit()

    assert [a.asset_id for a in db.query(Alerta).all()] == [otro.id]


def test_comprobar_alertas_sobrevive_a_una_huerfana(db):
    """Una fila huérfana no puede tumbar la comprobación de todas las demás.

    Se inyecta por SQL crudo justamente porque el ORM ya no deja llegar a este
    estado: lo que se prueba es que si aparece por otra vía -una base vieja, una
    restauración a medias, un borrado manual- el ciclo sigue funcionando."""
    vivo = _activo(db, "Vivo")
    db.add(Alerta(asset_id=vivo.id, tipo=TipoAlerta.POR_ENCIMA, valor=1.0))
    db.commit()

    db.execute(text(
        "INSERT INTO alertas (asset_id, tipo, valor, activa, created_at) "
        "VALUES (9999, 'por_encima', 1.0, 1, '2026-01-01 00:00:00')"
    ))
    db.commit()

    avisos = alertas.comprobar(db)

    # La del activo vivo sí salta: la huérfana se ignora, no envenena el resto
    assert len(avisos) == 1
    assert "Vivo" in avisos[0]


def test_la_alerta_huerfana_no_se_considera_cumplida(db):
    huerfana = Alerta(asset_id=9999, tipo=TipoAlerta.POR_ENCIMA, valor=1.0)

    assert alertas._se_cumple(huerfana) is False


def test_el_esquema_declara_el_borrado_en_cascada(db):
    """El ON DELETE del esquema, aparte de la cascada del ORM.

    Son dos protecciones distintas: la del ORM cubre lo que hace la app, y esta
    cubre un borrado por SQL directo el día que se active el PRAGMA."""
    for tabla in ("alertas", "pesos_objetivo"):
        claves = db.execute(text("PRAGMA foreign_key_list(%s)" % tabla)).mappings().all()
        hacia_assets = [k for k in claves if k["table"] == "assets"]

        assert hacia_assets, "%s debería tener una FK hacia assets" % tabla
        assert hacia_assets[0]["on_delete"] == "CASCADE"


# ---------- La migración, que es lo que corre sobre la base de verdad ----------
#
# Los tests de arriba usan `create_all`, que construye el esquema desde el
# modelo y nunca ejecuta la migración. Sobre una base ya existente -la que hay
# desplegada- el camino es el otro, y ahí es donde estaba el error de la primera
# versión de esta migración: `create_foreign_key` añadía una FK más en vez de
# sustituir la anónima, dejando dos, una de ellas sin cascada.

@pytest.fixture
def engine_temporal(tmp_path):
    """Motor sobre fichero: el modo batch recrea tablas y necesita una base
    real, no la de memoria."""
    engine = create_engine("sqlite:///%s" % (tmp_path / "cascadas.db"))
    yield engine
    # En Windows el fichero queda bloqueado si el pool no se cierra.
    engine.dispose()


def _fks_hacia_assets(engine, tabla: str) -> list[dict]:
    with engine.connect() as conn:
        claves = conn.execute(text("PRAGMA foreign_key_list(%s)" % tabla)).mappings().all()
    return [dict(k) for k in claves if k["table"] == "assets"]


def test_la_migracion_limpia_los_huerfanos_que_ya_existian(engine_temporal):
    config = _config_alembic(engine_temporal)
    command.upgrade(config, REVISION_ANTERIOR)

    with engine_temporal.begin() as conn:
        conn.execute(text(
            "INSERT INTO assets (id, name, asset_type, currency, created_at) "
            "VALUES (1, 'Vivo', 'accion', 'EUR', '2026-01-01 00:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO alertas (asset_id, tipo, valor, activa, created_at) VALUES "
            "(1, 'por_encima', 1.0, 1, '2026-01-01 00:00:00'), "
            "(9999, 'por_encima', 1.0, 1, '2026-01-01 00:00:00')"
        ))
        conn.execute(text(
            "INSERT INTO pesos_objetivo (asset_id, porcentaje, created_at) VALUES "
            "(1, 20.0, '2026-01-01 00:00:00'), "
            "(9999, 30.0, '2026-01-01 00:00:00')"
        ))

    command.upgrade(config, REVISION_CASCADAS)

    with engine_temporal.connect() as conn:
        alertas_restantes = conn.execute(text("SELECT asset_id FROM alertas")).scalars().all()
        pesos_restantes = conn.execute(text("SELECT asset_id FROM pesos_objetivo")).scalars().all()

    assert alertas_restantes == [1]
    assert pesos_restantes == [1]


def test_la_migracion_deja_una_sola_clave_foranea_y_con_cascada(engine_temporal):
    """La primera versión de esta migración dejaba dos: la nueva con CASCADE y
    la anónima original sin ella. Con el PRAGMA activo, la vieja habría seguido
    bloqueando el borrado y el arreglo no habría servido de nada."""
    command.upgrade(_config_alembic(engine_temporal), REVISION_CASCADAS)

    for tabla in ("alertas", "pesos_objetivo"):
        claves = _fks_hacia_assets(engine_temporal, tabla)

        assert len(claves) == 1, "%s tiene %d FK hacia assets" % (tabla, len(claves))
        assert claves[0]["on_delete"] == "CASCADE"


def test_la_migracion_conserva_los_indices(engine_temporal):
    """`copy_from` recrea la tabla: lo que no esté declarado ahí se pierde, y el
    de pesos_objetivo además es UNIQUE."""
    command.upgrade(_config_alembic(engine_temporal), REVISION_CASCADAS)

    with engine_temporal.connect() as conn:
        indices = conn.execute(text(
            "SELECT name, sql FROM sqlite_master WHERE type='index' "
            "AND tbl_name IN ('alertas','pesos_objetivo')"
        )).mappings().all()

    por_nombre = {i["name"]: i["sql"] for i in indices}

    assert "ix_alertas_asset_id" in por_nombre
    assert "UNIQUE" in por_nombre["ix_pesos_objetivo_asset_id"]
