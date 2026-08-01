"""Gasto rápido del botón flotante.

El diálogo vive en base.html, que sale en todas las páginas y no recibe la lista
de categorías de ningún router: se piden aparte en JSON. Y la fecha tiene que
ser la de hoy de verdad, no la del día en que arrancó el contenedor.
"""
from datetime import date

from app.models import Category, Transaction, TransactionType
from app.templating import hoy_iso


def test_las_categorias_se_sirven_en_json(client):
    client.db.add_all([Category(name="Comida", keywords=""), Category(name="Ocio", keywords="")])
    client.db.commit()

    respuesta = client.get("/categorias/opciones")

    assert respuesta.status_code == 200
    assert [c["name"] for c in respuesta.json()] == ["Comida", "Ocio"]
    assert all("id" in c for c in respuesta.json())


def test_la_fecha_del_formulario_es_la_de_hoy():
    """Como global evaluado al importar se quedaría congelada en el día del
    arranque, y el gasto se apuntaría con fecha vieja."""
    assert hoy_iso() == date.today().isoformat()


def test_el_boton_flotante_sale_en_todas_las_paginas(client):
    """Va en base.html a propósito: la gracia es apuntar desde donde estés."""
    for ruta in ("/", "/activos", "/analisis", "/recurrentes"):
        html = client.get(ruta).text
        assert 'id="fab"' in html, ruta
        assert 'id="dlg-gasto"' in html, ruta


def test_el_gasto_rapido_se_apunta(client):
    """El formulario del diálogo publica en el mismo endpoint que el alta manual."""
    cat = Category(name="Comida", keywords="")
    client.db.add(cat)
    client.db.commit()

    client.post_form("/transacciones", data={
        "date": date.today().isoformat(), "type": "gasto",
        "category_id": cat.id, "amount": "12.50", "description": "Café",
    }, follow_redirects=False)

    tx = client.db.query(Transaction).one()
    assert tx.type == TransactionType.GASTO
    assert float(tx.amount) == 12.50
    assert tx.date == date.today()
