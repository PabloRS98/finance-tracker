"""Manifest de la PWA.

Instalada tiene que comportarse como una app: icono propio del lanzador (no una
pegatina dentro de un cuadro blanco), pantalla completa y splash con los colores
de la app.
"""
import json
import pathlib

import pytest

ESTATICOS = pathlib.Path(__file__).resolve().parent.parent / "app" / "static"
MANIFEST = json.loads((ESTATICOS / "manifest.webmanifest").read_text(encoding="utf-8"))


def test_el_manifest_es_json_valido_y_se_sirve(client):
    respuesta = client.get("/static/manifest.webmanifest")

    assert respuesta.status_code == 200
    assert json.loads(respuesta.text)["short_name"] == "Patrimonio"


def test_hay_un_icono_maskable():
    """Sin purpose=maskable, Android mete el icono dentro de un cuadro blanco y
    queda como una pegatina en vez de adaptarse a la forma del lanzador."""
    maskables = [i for i in MANIFEST["icons"] if "maskable" in i.get("purpose", "")]

    assert maskables, "falta un icono maskable"
    assert all((ESTATICOS / i["src"].removeprefix("/static/")).exists() for i in maskables)


def test_queda_un_png_de_respaldo():
    """Los iconos SVG los entiende Chrome moderno, pero si un navegador no los
    soporta tiene que quedarle algo que sí: si no, la instalación se queda sin
    icono."""
    pngs = [i for i in MANIFEST["icons"] if i["type"] == "image/png"]

    assert pngs
    assert all((ESTATICOS / i["src"].removeprefix("/static/")).exists() for i in pngs)


@pytest.mark.parametrize("icono", MANIFEST["icons"])
def test_todos_los_iconos_existen(icono):
    assert (ESTATICOS / icono["src"].removeprefix("/static/")).exists(), icono["src"]


def test_el_splash_tiene_los_colores_de_la_app():
    """Android compone la pantalla de arranque con background_color, el icono y
    el nombre: sin background_color sale en blanco y da un fogonazo."""
    assert MANIFEST["background_color"] == "#0b0d12"
    assert MANIFEST["theme_color"] == "#4f8ef7"
    assert MANIFEST["display"] == "standalone"


def test_ios_declara_el_modo_standalone(client):
    """iOS ignora el manifest: sin estas metas, "Añadir a pantalla de inicio"
    abre la app en Safari con su barra de direcciones."""
    html = client.get("/").text

    assert 'name="apple-mobile-web-app-capable" content="yes"' in html
    assert 'name="apple-mobile-web-app-status-bar-style"' in html


def test_el_maskable_va_a_sangre_y_sin_esquinas_propias():
    """El lanzador aplica su propia máscara: si el SVG trae sus esquinas
    redondeadas, se ven recortadas dentro de la del sistema."""
    svg = (ESTATICOS / "icon-maskable.svg").read_text(encoding="utf-8")

    assert 'width="64" height="64" fill=' in svg
    assert "rx=" not in svg, "el icono maskable no debe redondear sus esquinas"
