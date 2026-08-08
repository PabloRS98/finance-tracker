"""usuarios: cada persona con sus datos aparte

La app nació mono-usuario. Esta migración abre el modelo a varias personas sin
tocar un solo dato: crea el primer usuario, le asigna TODO lo que ya existe, y a
partir de ahí cada fila tiene dueño.

El orden importa y no es negociable:

1. Se crea `usuarios` y su primera fila. Si no hay a quién asignar, el paso 3
   dejaría `usuario_id` a NULL en una columna que va a ser NOT NULL.
2. La columna se añade **nullable** en cada tabla y se rellena con ese usuario.
3. Solo entonces se recrea la tabla con la forma definitiva: NOT NULL, la clave
   foránea, y los `unique` que pasan de globales a por-usuario.

Ese último paso usa `copy_from` con la definición del modelo, no una escrita a
mano: así la tabla resultante es exactamente la que declara `models.py` y no una
aproximación que se desincronice a la primera. Es el mismo motivo por el que la
migración de las cascadas tuvo que usarlo — en SQLite no se puede alterar una
restricción, hay que recrear la tabla entera.

Lo que NO recibe dueño es `price_history`: son cotizaciones públicas, y que dos
personas con el mismo ETF compartan la serie cacheada es lo correcto, no un
descuido.

Revision ID: a3e91f7c2d40
Revises: d4f1e8b02a37
"""
import sqlalchemy as sa
from alembic import op

revision = "a3e91f7c2d40"
down_revision = "d4f1e8b02a37"
branch_labels = None
depends_on = None

# Tablas con dueño. price_history queda fuera a propósito (ver arriba).
CON_DUENO = (
    "accounts",
    "assets",
    "categories",
    "transactions",
    "recurring_transactions",
    "net_worth_snapshots",
    "net_worth_intraday",
    "benchmarks",
    "watchlist",
    "alertas",
    "pesos_objetivo",
)

# Los unique que dejan de ser globales. Dos personas pueden tener una cuenta
# "Revolut" o una categoría "Comida"; antes la segunda reventaba con
# IntegrityError sin explicar por qué.
UNIQUES_NUEVOS = {
    "accounts": [("uq_accounts_usuario_name", ["usuario_id", "name"])],
    "categories": [("uq_categories_usuario_name", ["usuario_id", "name"])],
    "net_worth_snapshots": [("uq_snapshots_usuario_date", ["usuario_id", "date"])],
    "benchmarks": [
        ("uq_benchmarks_usuario_clave", ["usuario_id", "clave"]),
        ("uq_benchmarks_usuario_symbol", ["usuario_id", "symbol"]),
    ],
    "watchlist": [("uq_watchlist_usuario_ticker", ["usuario_id", "ticker"])],
}


def _tabla_destino(nombre: str) -> sa.Table:
    """La tabla tal y como la declara models.py, para pasarla a `copy_from`."""
    from app import models  # noqa: F401  registra los modelos en Base
    from app.database import Base

    return Base.metadata.tables[nombre]


def upgrade() -> None:
    op.create_table(
        "usuarios",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("nombre", sa.String(length=40), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("nombre"),
    )

    # El primer usuario hereda todo lo que había. Sin contraseña: quien la
    # quiera la pone después, y obligar a una aquí dejaría al dueño de los datos
    # fuera de su propia app en el primer arranque tras actualizar.
    op.execute(
        "INSERT INTO usuarios (id, nombre, password_hash, created_at) "
        "VALUES (1, 'Yo', NULL, CURRENT_TIMESTAMP)"
    )

    for tabla in CON_DUENO:
        op.add_column(tabla, sa.Column("usuario_id", sa.Integer(), nullable=True))
        op.execute("UPDATE %s SET usuario_id = 1" % tabla)

    # Segunda pasada: con la columna ya rellena se puede exigir NOT NULL y
    # recrear la tabla con su forma definitiva.
    for tabla in CON_DUENO:
        with op.batch_alter_table(tabla, copy_from=_tabla_destino(tabla)) as batch:
            batch.create_index("ix_%s_usuario_id" % tabla, ["usuario_id"])
            for nombre, columnas in UNIQUES_NUEVOS.get(tabla, []):
                batch.create_unique_constraint(nombre, columnas)


def downgrade() -> None:
    for tabla in CON_DUENO:
        op.drop_column(tabla, "usuario_id")
    op.drop_table("usuarios")
