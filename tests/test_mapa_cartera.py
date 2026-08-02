"""Mapa de la cartera: se puede ampliar y ahí sí caben los nombres.

En la portada el mapa mide 260px de alto, y a ese tamaño solo se rotulan las
piezas grandes: el resto quedaba en el tooltip, que en el móvil no existe. Al
ampliarlo hay seis veces más superficie, así que entran el nombre completo, el
peso y la variación.

Aun así una astilla del 0,3% no admite rótulo por mucho que se amplíe, de ahí la
lista de debajo: es la que garantiza que salgan todas, y de paso es la
alternativa en texto de un gráfico que se lee por color.

El reparto de las piezas lo hace el JS en el navegador; lo que se comprueba aquí
es que el servidor manda lo que ese JS necesita y que el marcado lo permite.
"""
import json
import re
from datetime import date

import pytest

from app.models import Asset, AssetType, Currency, Operation, OperationType, TransactionStatus


@pytest.fixture
def con_posiciones(client):
    for nombre, ticker, precio in [("Ejemplo Inc.", "EJEM", 100.0), ("Muestra SA", "MSTA", 50.0)]:
        asset = Asset(name=nombre, asset_type=AssetType.ACCION, currency=Currency.EUR,
                      ticker=ticker, current_price=precio)
        client.db.add(asset)
        client.db.flush()
        client.db.add(Operation(asset_id=asset.id, type=OperationType.COMPRA, quantity=10,
                                unit_price=precio, fee=0.0, date=date(2025, 1, 10),
                                status=TransactionStatus.CONFIRMADO))
    client.db.commit()
    return client


def _datos_del_mapa(html: str) -> list[dict]:
    """Lo que va en el data-items del contenedor, que es lo que lee el JS."""
    m = re.search(r"id=\"heatmap\"[^>]*data-items='([^']*)'", html)
    assert m, "el mapa no lleva datos"
    return json.loads(m.group(1))


# ---------- Los datos que necesita el JS ----------

def test_cada_posicion_lleva_lo_necesario_para_pintarla(con_posiciones):
    datos = _datos_del_mapa(con_posiciones.get("/").text)

    assert len(datos) == 2
    for d in datos:
        assert set(d) >= {"id", "nombre", "ticker", "valor", "variacion"}


def test_lleva_el_id_para_poder_abrir_la_ficha(con_posiciones):
    """Mirar el mapa y querer abrir lo que se está mirando es el gesto
    siguiente: sin el id las piezas no podrían enlazar a ningún sitio."""
    datos = _datos_del_mapa(con_posiciones.get("/").text)
    ids = {d["id"] for d in datos}

    assert None not in ids
    for asset_id in ids:
        assert con_posiciones.get("/activos/%d" % asset_id).status_code == 200


# ---------- Ampliar ----------

def test_el_mapa_de_la_portada_abre_el_ampliado(con_posiciones):
    html = con_posiciones.get("/").text

    assert 'data-open-dialog="#dlg-mapa"' in html
    assert 'id="dlg-mapa"' in html


def test_el_mapa_es_alcanzable_con_el_teclado(con_posiciones):
    """Es un div, no un botón: sin rol ni tabindex, ampliar sería solo para
    quien use ratón."""
    html = con_posiciones.get("/").text
    contenedor = re.search(r"<div id=\"heatmap\"[^>]*>", html).group(0)

    assert 'role="button"' in contenedor
    assert 'tabindex="0"' in contenedor
    assert "aria-label=" in contenedor


def test_el_ampliado_pide_los_rotulos_detallados(con_posiciones):
    html = con_posiciones.get("/").text

    assert 'id="heatmap-grande"' in html
    assert 'data-detallado="1"' in html


def test_el_ampliado_trae_los_mismos_datos_que_el_pequeno(con_posiciones):
    """Dos consultas darían dos verdades: el diálogo reutiliza el mismo JSON."""
    html = con_posiciones.get("/").text
    pequeno = re.search(r"id=\"heatmap\"[^>]*data-items='([^']*)'", html).group(1)
    grande = re.search(r"id=\"heatmap-grande\"[^>]*data-items='([^']*)'", html).group(1)

    assert json.loads(pequeno) == json.loads(grande)


def test_hay_lista_para_las_piezas_que_no_admiten_rotulo(con_posiciones):
    assert 'id="heatmap-leyenda"' in con_posiciones.get("/").text


# ---------- Sin datos ----------

def test_sin_posiciones_no_se_ofrece_ampliar_nada(client):
    """El estado vacío no debe dejar un diálogo que abriría un mapa en blanco."""
    html = client.get("/").text

    assert 'id="dlg-mapa"' not in html
    assert "Sin posiciones que mapear" in html


# ---------- El diálogo tiene que estar cerrado hasta que se abra ----------

def test_el_dialogo_no_se_pinta_solo():
    """Un <dialog> cerrado se oculta porque el navegador le pone display:none.
    Declarar `display: flex` a secas lo pisa y lo deja visible siempre: el mapa
    salía incrustado en la portada, sin haberse abierto y sin forma de cerrarlo,
    porque el botón de cerrar solo funciona sobre un diálogo abierto.

    Solo puede fijarse el display cuando el sujeto del selector es el propio
    diálogo Y lleva [open]. Sobre un descendiente (`dialog h3`) no hay problema.
    """
    from pathlib import Path

    css = Path("app/static/css/style.css").read_text(encoding="utf-8")
    for bloque in re.findall(r"([^{}]*\{[^{}]*\})", css):
        selector, cuerpo = bloque.split("{", 1)
        if "display:" not in cuerpo:
            continue
        for parte in selector.split(","):
            sujeto = parte.strip().split()[-1] if parte.strip() else ""
            if not sujeto.startswith("dialog") or "::" in sujeto:
                continue
            assert "[open]" in sujeto, (
                "%s fija el display sin [open]: el diálogo se vería estando cerrado" % sujeto
            )
