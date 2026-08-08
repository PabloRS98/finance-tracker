"""Limpiezas de integridad referencial que SQLite no hace por su cuenta.

SQLite no aplica claves foráneas salvo que se active el PRAGMA, así que un
borrado puede dejar filas apuntando a un id inexistente. Estas funciones viven
aquí, y no dentro de una migración, para poder probarlas: una migración solo se
ejecuta una vez y no se le pueden pasar casos."""
from sqlalchemy import text
from sqlalchemy.orm import Session

_TABLAS_CON_CATEGORIA = ("transactions", "recurring_transactions")


def desasignar_categorias_huerfanas(db: Session) -> int:
    """Pone a NULL los `category_id` que ya no llevan a ninguna categoría.

    No se borran las filas: el gasto ocurrió igual, lo que falta es la etiqueta.
    Devuelve cuántas se han tocado. Quien llama decide cuándo hacer commit."""
    tocadas = 0
    for tabla in _TABLAS_CON_CATEGORIA:
        resultado = db.execute(text(
            "UPDATE %s SET category_id = NULL "
            "WHERE category_id IS NOT NULL "
            "AND category_id NOT IN (SELECT id FROM categories)" % tabla
        ))
        tocadas += resultado.rowcount or 0
    return tocadas
