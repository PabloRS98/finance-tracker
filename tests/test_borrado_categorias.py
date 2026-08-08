"""[FT-M6] Borrar una categoría dejaba transacciones apuntando a un id inexistente.

`Transaction.category_id` y `RecurringTransaction.category_id` son claves
foráneas sin `ondelete`, y SQLite no las aplica. Tras el borrado quedaban filas
con un `category_id` que no lleva a ninguna parte.

La app lo toleraba —`t.category` devuelve None y la plantilla escribe "Sin
categoría"—, pero el peligro es otro: SQLite **reutiliza los ids** (no hay
AUTOINCREMENT declarado), así que una categoría nueva puede recibir el id de la
borrada y adoptar transacciones antiguas que no le corresponden.

El fichero de al lado ya lo hacía bien: `delete_account` pone a None las
referencias antes de borrar.
"""
import datetime

from sqlalchemy import text

from app.models import Category, RecurringTransaction, Transaction, TransactionType


def _categoria(client, nombre):
    cat = Category(name=nombre, keywords="")
    client.db.add(cat)
    client.db.commit()
    return cat


def _transaccion(client, cat, descripcion="compra"):
    tx = Transaction(date=datetime.date(2026, 1, 1), amount=10, type=TransactionType.GASTO,
                     description=descripcion, category_id=cat.id)
    client.db.add(tx)
    client.db.commit()
    return tx


def test_borrar_categoria_desasigna_sus_transacciones(client):
    cat = _categoria(client, "Temporal")
    _transaccion(client, cat)
    _transaccion(client, cat, "otra")

    client.post_form("/categorias/%d/eliminar" % cat.id, follow_redirects=False)

    client.db.expire_all()
    assert client.db.query(Category).filter_by(name="Temporal").count() == 0
    assert client.db.query(Transaction).filter(Transaction.category_id.isnot(None)).count() == 0


def test_borrar_categoria_desasigna_sus_recurrentes(client):
    cat = _categoria(client, "Temporal")
    client.db.add(RecurringTransaction(
        name="cuota", amount=10, type=TransactionType.GASTO,
        day_of_month=1, start_date=datetime.date(2026, 1, 1), category_id=cat.id,
    ))
    client.db.commit()

    client.post_form("/categorias/%d/eliminar" % cat.id, follow_redirects=False)

    client.db.expire_all()
    assert client.db.query(RecurringTransaction).one().category_id is None


def test_una_categoria_nueva_no_hereda_transacciones(client):
    """El caso peligroso: SQLite reutiliza el id de la categoría borrada."""
    vieja = _categoria(client, "Vieja")
    _transaccion(client, vieja)
    id_reutilizable = vieja.id

    client.post_form("/categorias/%d/eliminar" % vieja.id, follow_redirects=False)
    client.post_form("/categorias", data={"name": "Nueva", "keywords": ""},
                     follow_redirects=False)

    client.db.expire_all()
    nueva = client.db.query(Category).filter_by(name="Nueva").one()
    heredadas = client.db.query(Transaction).filter_by(category_id=nueva.id).count()

    assert heredadas == 0, "la nueva ha adoptado transacciones ajenas (id %d)" % id_reutilizable


def test_borrar_categoria_no_borra_las_transacciones(client):
    """Se desasignan, no se pierden: el gasto ocurrió igual."""
    cat = _categoria(client, "Temporal")
    _transaccion(client, cat)

    client.post_form("/categorias/%d/eliminar" % cat.id, follow_redirects=False)

    assert client.db.query(Transaction).count() == 1


def test_el_aviso_dice_cuantas_quedaron_sin_categoria(client):
    """Información que el usuario querrá: acaba de descategorizar N gastos."""
    cat = _categoria(client, "Temporal")
    for i in range(3):
        _transaccion(client, cat, "compra %d" % i)

    respuesta = client.post_form("/categorias/%d/eliminar" % cat.id, follow_redirects=False)

    import urllib.parse
    aviso = urllib.parse.unquote(respuesta.cookies.get("flash", ""))

    assert "3" in aviso


def test_la_migracion_limpia_las_referencias_huerfanas(client):
    """Las filas que ya quedaron colgando de borrados anteriores."""
    client.db.execute(text(
        "INSERT INTO transactions (date, amount, type, description, category_id, status, source, created_at) "
        "VALUES ('2026-01-01', 10, 'gasto', 'huerfana', 9999, 'confirmado', 'manual', '2026-01-01 00:00:00')"
    ))
    client.db.commit()

    from app.services.mantenimiento import desasignar_categorias_huerfanas

    limpiadas = desasignar_categorias_huerfanas(client.db)
    client.db.commit()

    assert limpiadas == 1
    assert client.db.query(Transaction).filter_by(description="huerfana").one().category_id is None
