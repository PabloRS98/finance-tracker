"""Puesta al día del esquema desde una base de datos anterior a Alembic.

Es el caso que rompió el despliegue real. Aquellas bases se creaban con
`create_all()`, que no altera tablas ya existentes: a cada una le falta lo que
se añadiera al modelo después de su creación (a la desplegada le faltaba
`assets.avg_cost_override`). Además no tienen tabla `alembic_version`, así que
un `upgrade` las trata como vacías e intenta crear tablas que ya existen.

`init_db()` tiene que detectarlas, completarlas y marcarlas sin intervención
manual: un `alembic stamp` a mano deja la app rota hasta que alguien lo
recuerda, y marcar sin completar las columnas esconde el fallo en vez de
arreglarlo.
"""
import pytest
from alembic import command
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401  registra los modelos en Base
from app.database import REVISION_INICIAL, Base, _config_alembic, init_db
from app.models import Asset, AssetType, Currency

TABLAS_DEL_MODELO = sorted(Base.metadata.tables)


@pytest.fixture
def engine_temporal(tmp_path):
    """Motor sobre fichero: las migraciones usan ALTER TABLE y modo batch, que
    necesitan una base real, no la de memoria."""
    engine = create_engine("sqlite:///%s" % (tmp_path / "prueba.db"))
    yield engine
    # En Windows el fichero queda bloqueado si el pool no se cierra.
    engine.dispose()


def _revision_actual(engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _revision_head(engine) -> str:
    return ScriptDirectory.from_config(_config_alembic(engine)).get_current_head()


def _base_anterior_a_alembic(engine) -> None:
    """Reproduce una base como las de antes de Alembic: el esquema de la v3,
    sin marca de versión y sin la columna que se olvidó de migrar."""
    command.upgrade(_config_alembic(engine), REVISION_INICIAL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("ALTER TABLE assets DROP COLUMN avg_cost_override"))


def _columnas(engine, tabla: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(tabla)}


def test_una_base_anterior_a_alembic_se_pone_al_dia(engine_temporal):
    _base_anterior_a_alembic(engine_temporal)
    assert "avg_cost_override" not in _columnas(engine_temporal, "assets")
    assert _revision_actual(engine_temporal) is None

    init_db(bind=engine_temporal)

    assert _revision_actual(engine_temporal) == _revision_head(engine_temporal)
    assert "avg_cost_override" in _columnas(engine_temporal, "assets")


@pytest.mark.parametrize("tabla", TABLAS_DEL_MODELO)
def test_tras_migrar_no_falta_ninguna_columna_del_modelo(engine_temporal, tabla):
    """El síntoma real era un 500 en todas las páginas por una columna ausente:
    tras la puesta al día no puede faltar ninguna, de ninguna tabla."""
    _base_anterior_a_alembic(engine_temporal)
    init_db(bind=engine_temporal)

    del_modelo = {c.name for c in Base.metadata.tables[tabla].columns}
    assert del_modelo - _columnas(engine_temporal, tabla) == set()


def test_se_puede_consultar_tras_migrar(engine_temporal):
    """Sin la reconciliación esto lanzaba
    OperationalError("no such column: assets.avg_cost_override")."""
    _base_anterior_a_alembic(engine_temporal)
    init_db(bind=engine_temporal)

    session = sessionmaker(bind=engine_temporal)()
    try:
        session.add(Asset(name="Piso", asset_type=AssetType.OTRO,
                          currency=Currency.EUR, manual_value=1000))
        session.commit()
        assert [a.avg_cost_override for a in session.query(Asset).all()] == [None]
    finally:
        session.close()


def test_una_base_nueva_se_crea_al_dia(engine_temporal):
    init_db(bind=engine_temporal)

    assert _revision_actual(engine_temporal) == _revision_head(engine_temporal)
    tablas = set(inspect(engine_temporal).get_table_names())
    assert set(TABLAS_DEL_MODELO) - tablas == set()


def test_init_db_es_idempotente(engine_temporal):
    """Se ejecuta en cada arranque: repetirlo no puede fallar ni cambiar nada."""
    init_db(bind=engine_temporal)
    antes = _revision_actual(engine_temporal)

    init_db(bind=engine_temporal)

    assert _revision_actual(engine_temporal) == antes
