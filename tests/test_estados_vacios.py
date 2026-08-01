"""Estados vacíos con acción.

Antes decían qué hacer en prosa ("Registra la primera arriba"), lo que obliga a
buscar dónde está ese "arriba". Ahora el sitio al que ir es un botón.
"""
import pytest

from app.models import Asset, AssetType, Currency

# Páginas que en una instalación recién estrenada están vacías
VACIAS = ["/", "/activos", "/operaciones", "/analisis", "/recurrentes", "/categorias"]


@pytest.mark.parametrize("ruta", VACIAS)
def test_las_paginas_vacias_ofrecen_una_salida(client, ruta):
    """Sin datos, cada página tiene que decir a dónde ir, no solo que está vacía."""
    html = client.get(ruta).text

    assert 'class="empty' in html, ruta
    assert "empty-acciones" in html, "%s no ofrece ninguna acción" % ruta


def test_la_lista_de_activos_lleva_a_crear_e_importar(client):
    html = client.get("/activos").text
    bloque = html.split("empty-acciones", 1)[1].split("</div>", 1)[0]

    assert "Añadir activo" in bloque
    assert "/operaciones/importar" in bloque


def test_el_historial_vacio_lleva_a_registrar_e_importar(client):
    """Con activos pero sin operaciones: las dos formas de empezar."""
    client.db.add(Asset(name="MUESTRA", asset_type=AssetType.ACCION,
                        currency=Currency.USD, ticker="NVDA"))
    client.db.commit()

    html = client.get("/operaciones").text

    assert "Sin operaciones" in html
    assert "/operaciones/importar" in html


def test_con_datos_no_sale_el_estado_vacio(client):
    """La comprobación inversa: el bloque no puede colarse cuando sí hay algo."""
    client.db.add(Asset(name="MUESTRA", asset_type=AssetType.ACCION,
                        currency=Currency.USD, ticker="NVDA", current_price=10.0))
    client.db.commit()

    html = client.get("/activos").text

    assert "Aún no tienes activos" not in html
