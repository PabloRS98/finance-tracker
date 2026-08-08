"""limpiar categorias huerfanas

[FT-M6] Borrar una categoría dejaba `category_id` apuntando a un id inexistente,
en transacciones y en recurrentes. La app lo toleraba —se veían como "Sin
categoría"—, pero SQLite reutiliza los ids: una categoría nueva podía recibir el
de la borrada y adoptar transacciones ajenas.

El borrado ya no las deja (`routers/categories.py`), pero las que se acumularon
hasta ahora siguen ahí, y son justamente las que pueden ser adoptadas.

La limpieza vive en `services/mantenimiento.py` y no aquí para poder probarla:
una migración se ejecuta una vez y no admite casos.

Revision ID: d4f1e8b02a37
Revises: b7c3a91d4e52
"""
from alembic import op
from sqlalchemy.orm import Session

from app.services.mantenimiento import desasignar_categorias_huerfanas

revision = "d4f1e8b02a37"
down_revision = "b7c3a91d4e52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sesion = Session(bind=op.get_bind())
    desasignar_categorias_huerfanas(sesion)
    sesion.flush()


def downgrade() -> None:
    # No hay vuelta: las referencias apuntaban a categorías que ya no existen,
    # así que no hay valor al que devolverlas.
    pass
