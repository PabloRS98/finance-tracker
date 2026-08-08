"""Motor y sesión de SQLAlchemy sobre SQLite, y arranque del esquema con Alembic."""
import os

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

os.makedirs(os.path.dirname(settings.db_path) or ".", exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Primera revisión de Alembic: describe el esquema tal y como quedó en la v3.
REVISION_INICIAL = "0e760c1309e1"

# Columnas que se fueron añadiendo al modelo antes de que existiera Alembic.
#
# Solo se usan para reconciliar una base anterior a Alembic: esas bases se
# crearon con `create_all()`, que no altera tablas ya existentes, así que a cada
# una le falta todo lo que se añadiera después de su creación. Antes de marcarla
# en REVISION_INICIAL hay que completarlas, porque esa revisión afirma que la
# tabla ya tiene estas columnas. Si no, la app arranca y revienta en la primera
# consulta con "no such column: ...". Lo vigila tests/test_migraciones.py.
COLUMNAS_POST_V2: dict[str, dict[str, str]] = {
    "assets": {
        "previous_close": "FLOAT",
        "account_id": "INTEGER REFERENCES accounts(id)",
        "isin": "VARCHAR(20)",
        "region": "VARCHAR(40)",
        "sector": "VARCHAR(40)",
        "exposure_currency": "VARCHAR(3)",
        "avg_cost_override": "FLOAT",
    },
    "net_worth_snapshots": {
        "manual_value": "FLOAT",
    },
    "recurring_transactions": {
        "currency": "VARCHAR(3) DEFAULT 'EUR'",
        "interval_months": "INTEGER DEFAULT 1",
    },
}


def ensure_columns(table: str, columns: dict[str, str], bind=None) -> list[str]:
    """Añade a `table` las columnas de `columns` ({nombre: DDL}) que aún no
    existan, con ALTER TABLE ADD COLUMN. Solo para columnas nullable o con
    default: no rompe bases de datos existentes. Devuelve las que añadió.

    `bind` permite apuntar a otro motor (los tests migran bases temporales)."""
    added: list[str] = []
    with (bind or engine).begin() as conn:
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                added.append(name)
    return added


def _config_alembic(target):
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config = Config(os.path.join(root, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(root, "migrations"))
    config.set_main_option("sqlalchemy.url", target.url.render_as_string(hide_password=False))
    return config


def revision_pendiente(bind=None) -> tuple[str | None, str | None]:
    """(revisión de la BD, revisión objetivo). Iguales = esquema al día.

    Solo lee `alembic_version`, sin abrir transacción de escritura ni tocar el
    DDL, así que es seguro llamarlo desde el arranque del servidor: es la
    diferencia con `init_db()`, que sí puede quedarse esperando un lock."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    target = bind or engine
    with target.connect() as conn:
        actual = MigrationContext.configure(conn).get_current_revision()
    head = ScriptDirectory.from_config(_config_alembic(target)).get_current_head()
    return actual, head


def init_db(bind=None):
    """Deja el esquema al día aplicando las migraciones pendientes de Alembic.

    Alembic sustituye al antiguo `ensure_columns` como mecanismo general: aquel
    solo sabía hacer ADD COLUMN, no podía cambiar tipos ni crear índices.

    Una base anterior a Alembic no tiene tabla `alembic_version`, así que
    `upgrade` la trataría como vacía e intentaría crear tablas que ya existen.
    Se detecta y se marca en la revisión inicial, completando antes las columnas
    que le falten para que la marca no mienta. Es automático a propósito: pedir
    un `alembic stamp` a mano dejaba la app rota hasta que alguien lo recordara.
    """
    from alembic import command

    from . import models  # noqa: F401  asegura que los modelos queden registrados

    target = bind or engine
    config = _config_alembic(target)

    tablas = set(inspect(target).get_table_names())
    if "assets" in tablas and "alembic_version" not in tablas:
        for tabla, columnas in COLUMNAS_POST_V2.items():
            if tabla in tablas:
                ensure_columns(tabla, columnas, target)
        command.stamp(config, REVISION_INICIAL)

    command.upgrade(config, "head")
