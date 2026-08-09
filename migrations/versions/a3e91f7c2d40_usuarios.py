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

# En cinco de ellas, además, los `unique` dejan de ser globales: dos personas
# pueden tener una cuenta "Revolut" o una categoría "Comida", y antes la segunda
# reventaba con IntegrityError sin explicar por qué. No hace falta listarlos aquí
# —salen de `__table_args__` al recrear la tabla desde el modelo—, pero conviene
# saber que ese cambio ocurre.


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

    # Segunda pasada: con la columna ya rellena, se recrea cada tabla con la
    # forma que declara el modelo — NOT NULL, la clave foránea, el índice y los
    # unique compuestos salen de ahí, no de una lista escrita a mano.
    #
    # `recreate="always"` no es decorativo. Batch mode solo reconstruye la tabla
    # si alguna operación lo exige, y añadir un índice no lo exige: sin esto,
    # las seis tablas que no cambian ningún `unique` se quedaban con el
    # `usuario_id` nullable y sin clave foránea. Lo cazó el test que compara las
    # migraciones con los modelos, no una lectura del código.
    for tabla in CON_DUENO:
        destino = _tabla_destino(tabla)
        with op.batch_alter_table(tabla, copy_from=destino, recreate="always"):
            pass

        # Y los índices se rehacen a mano, porque la recreación NO los conserva:
        # sin esto la migración se llevaba por delante el índice de fecha de
        # transacciones —el que hace rápido el filtro que más se usa— y el
        # UNIQUE de pesos_objetivo.asset_id, que además es una restricción, no
        # solo una optimización. También lo cazó el test de coherencia entre
        # migraciones y modelos.
        for indice in sorted(destino.indexes, key=lambda i: i.name):
            op.create_index(
                indice.name, tabla,
                [columna.name for columna in indice.columns],
                unique=indice.unique,
            )


def downgrade() -> None:
    for tabla in CON_DUENO:
        op.drop_column(tabla, "usuario_id")
    op.drop_table("usuarios")
