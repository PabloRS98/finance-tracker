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


# ---------- La barra superior cabe ----------
# .topbar es un flex sin wrap: con las siete etiquetas necesita 1.232px, y por
# debajo de eso los botones de la derecha se salían y hacían que toda la página
# tuviera scroll horizontal. Entre 721px y 1.279px se dejan solo los iconos.

def test_los_enlaces_llevan_titulo_para_cuando_solo_se_ven_los_iconos(client):
    """Sin etiqueta a la vista, el título es lo único que dice a dónde lleva."""
    html = client.get("/").text

    for ruta, titulo in [("/", "Dashboard"), ("/activos", "Activos"),
                         ("/operaciones", "Operaciones"), ("/transacciones", "Movimientos"),
                         ("/analisis", "Análisis"), ("/recurrentes", "Recurrentes"),
                         ("/categorias", "Categorías")]:
        assert 'href="%s"' % ruta in html and 'title="%s"' % titulo in html, ruta


def _css() -> str:
    from pathlib import Path

    return Path("app/static/css/style.css").read_text(encoding="utf-8")


def test_existe_la_banda_de_solo_iconos():
    assert "(min-width: 721px) and (max-width: 1279px)" in _css()


def test_las_etiquetas_se_ocultan_sin_sacarlas_del_arbol_de_accesibilidad():
    """Con `display: none` un lector de pantalla anunciaría siete enlaces sin
    nombre. Se ocultan recortándolas, que las deja anunciables."""
    banda = _css().split("(min-width: 721px) and (max-width: 1279px)")[1].split("}\n}")[0]

    assert "clip-path" in banda
    assert "display: none" not in banda
