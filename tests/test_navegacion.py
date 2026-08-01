"""Navegación: cuatro secciones en la barra y el resto en "Más".

Siete iconos no caben cómodos en 400px. El reparto es de CSS, pero lo que sí se
puede comprobar aquí es que el marcado lo soporta: que las secundarias van
marcadas, que existen las dos veces (barra y diálogo) y que el botón "Más"
queda señalado cuando estás dentro de una de ellas.
"""
import pytest

PRIMARIAS = ["/", "/activos", "/operaciones", "/transacciones"]
SECUNDARIAS = ["/analisis", "/recurrentes", "/categorias"]


def test_las_secundarias_van_marcadas_para_que_el_css_las_saque(client):
    html = client.get("/").text

    for ruta in SECUNDARIAS:
        assert 'href="%s" data-secundaria' % ruta in html, ruta
    # Las del día a día se quedan en la barra
    for ruta in PRIMARIAS:
        assert 'href="%s" data-secundaria' % ruta not in html, ruta


def test_las_secundarias_siguen_alcanzables_desde_el_dialogo(client):
    html = client.get("/").text

    assert 'id="dlg-mas"' in html
    menu = html.split('id="dlg-mas"', 1)[1].split("</dialog>", 1)[0]
    for ruta in SECUNDARIAS:
        assert 'href="%s"' % ruta in menu, ruta


@pytest.mark.parametrize("ruta", SECUNDARIAS)
def test_el_boton_mas_queda_marcado_dentro_de_una_seccion_suya(client, ruta):
    """Si no, en esas pantallas la barra inferior no señalaría nada."""
    html = client.get(ruta).text

    assert 'class="nav-mas active"' in html


@pytest.mark.parametrize("ruta", PRIMARIAS)
def test_el_boton_mas_no_se_marca_en_las_principales(client, ruta):
    html = client.get(ruta).text

    assert 'class="nav-mas active"' not in html


def test_el_dialogo_de_mas_sale_en_todas_las_paginas(client):
    """Va en base.html: la navegación tiene que funcionar desde donde estés."""
    for ruta in PRIMARIAS + SECUNDARIAS:
        assert 'id="dlg-mas"' in client.get(ruta).text, ruta
