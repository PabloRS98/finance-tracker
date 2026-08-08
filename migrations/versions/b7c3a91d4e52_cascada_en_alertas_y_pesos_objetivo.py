"""cascada en alertas y pesos objetivo

[FT-A3] Dos cosas, y las dos hacen falta:

1. Limpiar las filas que YA quedaron huérfanas. Sin esto, el ON DELETE nuevo no
   arregla el daño hecho: una alerta apuntando a un activo borrado sigue
   rompiendo la comprobación de todas las demás.
2. Poner ON DELETE CASCADE en la clave foránea.

Sobre el punto 2 y SQLite: no se puede alterar una FK existente, así que hay que
recrear la tabla. `batch_alter_table` lo hace, pero `create_foreign_key` a secas
**añade** una constraint en vez de sustituir la que ya hay, y la original es
anónima (viene inline de `create_table`), de modo que la tabla acababa con dos
FK hacia assets: la nueva con CASCADE y la vieja sin ella. Con el PRAGMA de
claves foráneas activado, la vieja bloquearía el borrado y el arreglo no
serviría de nada. Comprobado sobre el esquema resultante:

    (0, 0, 'assets', 'asset_id', 'id', 'NO ACTION', 'NO ACTION', 'NONE')
    (1, 0, 'assets', 'asset_id', 'id', 'NO ACTION', 'CASCADE',   'NONE')

Por eso se pasa `copy_from` con la tabla declarada sin ninguna FK: batch recrea
la tabla exactamente así y después se añade la única que queremos. Los índices
van declarados ahí también, porque lo que no esté en `copy_from` no sobrevive a
la recreación.

El orden importa: primero se borran los huérfanos y después se recrea la tabla.
Al revés, la copia arrastraría filas que apuntan a nada.

Revision ID: b7c3a91d4e52
Revises: e0a5c7f2e8c6
"""
import sqlalchemy as sa
from alembic import op

revision = "b7c3a91d4e52"
down_revision = "e0a5c7f2e8c6"
branch_labels = None
depends_on = None


def _alertas_sin_fk() -> sa.Table:
    """La tabla tal cual está, pero sin declarar la clave foránea."""
    return sa.Table(
        "alertas",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("tipo", sa.String(length=12), nullable=False),
        sa.Column("valor", sa.Float(), nullable=False),
        sa.Column("activa", sa.Boolean(), nullable=False),
        sa.Column("ultimo_disparo", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("ix_alertas_asset_id", "asset_id"),
    )


def _pesos_sin_fk() -> sa.Table:
    return sa.Table(
        "pesos_objetivo",
        sa.MetaData(),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("asset_id", sa.Integer(), nullable=False),
        sa.Column("porcentaje", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.Index("ix_pesos_objetivo_asset_id", "asset_id", unique=True),
    )


def upgrade() -> None:
    op.execute("DELETE FROM alertas WHERE asset_id NOT IN (SELECT id FROM assets)")
    op.execute("DELETE FROM pesos_objetivo WHERE asset_id NOT IN (SELECT id FROM assets)")

    with op.batch_alter_table("alertas", copy_from=_alertas_sin_fk()) as batch:
        batch.create_foreign_key(
            "fk_alertas_asset_id_assets", "assets", ["asset_id"], ["id"], ondelete="CASCADE",
        )

    with op.batch_alter_table("pesos_objetivo", copy_from=_pesos_sin_fk()) as batch:
        batch.create_foreign_key(
            "fk_pesos_objetivo_asset_id_assets", "assets", ["asset_id"], ["id"], ondelete="CASCADE",
        )


def downgrade() -> None:
    # Se recrean las tablas sin ninguna FK hacia assets, que es como estaban:
    # la original era anónima e inline, y reproducirla con nombre no sería
    # volver al estado anterior. Los huérfanos borrados en upgrade() no vuelven:
    # eran datos rotos, no datos.
    with op.batch_alter_table("alertas", copy_from=_alertas_sin_fk()):
        pass

    with op.batch_alter_table("pesos_objetivo", copy_from=_pesos_sin_fk()):
        pass
