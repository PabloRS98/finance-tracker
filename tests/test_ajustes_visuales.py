"""Áreas seguras, alturas de tarjeta, tablas ordenadas y la cotización invertible.

Lo que se puede comprobar sin navegador es que las reglas y el marcado que
sostienen estos arreglos siguen ahí. La medición de verdad (alturas de fila,
insets) se hizo en el navegador; esto evita que se caiga por descuido.
"""
import re
from pathlib import Path

import pytest

CSS = Path("app/static/css/style.css").read_text(encoding="utf-8")
DASHBOARD = Path("app/templates/dashboard.html").read_text(encoding="utf-8")


# ---------- Notch y áreas seguras ----------

@pytest.mark.parametrize("lado", ["top", "right", "bottom", "left"])
def test_se_respetan_las_cuatro_areas_seguras(lado):
    """Con viewport-fit=cover la página ocupa TODA la pantalla, recorte
    incluido. Solo se contemplaba el borde inferior: instalada como PWA la
    barra superior se metía bajo el notch, y en horizontal los extremos de la
    barra inferior caían bajo el redondeo."""
    assert "env(safe-area-inset-%s)" % lado in CSS


def test_la_barra_inferior_respeta_los_lados():
    """Es donde están Dashboard, Activos y demás: si el primero y el último
    quedan bajo el redondeo, se pierde la mitad de su área táctil."""
    # Hay varios selectores `.nav-links {`; el de la barra inferior es el que
    # lleva position:fixed. El primero es el de la barra de arriba.
    reglas = [b for b in re.findall(r"\.nav-links \{([^}]*)\}", CSS) if "position: fixed" in b]

    assert reglas, "no se encuentra la barra inferior fija"
    assert "safe-area-inset-left" in reglas[0] and "safe-area-inset-right" in reglas[0]


def test_el_viewport_declara_que_ocupa_toda_la_pantalla():
    base = Path("app/templates/base.html").read_text(encoding="utf-8")

    assert "viewport-fit=cover" in base, "sin esto env(safe-area-*) vale siempre 0"


# ---------- Tarjetas del dashboard ----------

def test_las_tarjetas_abiertas_de_una_fila_se_igualan():
    bloque = CSS.split(".grid-charts > details[open] {")[1].split("}")[0]

    assert "align-self: stretch" in bloque


def test_las_plegadas_no_se_estiran():
    """Estirar una sección plegada dejaría una cabecera de 60px dentro de una
    caja de 400. Por eso el selector lleva [open] y no vale align-items."""
    assert ".grid-charts > details[open] {" in CSS
    assert "align-items: stretch" not in CSS.split(".grid-charts {")[1].split("}")[0]


def test_el_hueco_se_lo_lleva_la_grafica():
    """El crecimiento va primero a ::details-content: el navegador envuelve ahí
    lo que sigue al <summary>, así que el hijo flex es esa caja y no la
    gráfica."""
    assert "details[open]::details-content" in CSS


# ---------- Tablas ----------

def test_los_nombres_largos_no_parten_la_fila():
    """Un nombre de fondo partía la fila en cuatro líneas y las alturas iban de
    48 a 102px: la tabla se leía como un serrucho."""
    bloque = CSS.split("table .celda-activo > strong {")[1].split("}")[0]

    assert "text-overflow: ellipsis" in bloque
    assert "white-space: nowrap" in bloque


def test_el_nombre_completo_sigue_disponible_al_recortarlo():
    ops = Path("app/templates/operations.html").read_text(encoding="utf-8")

    assert 'title="{{ op.asset.name }}"' in ops


def test_las_etiquetas_no_se_parten():
    assert ".tag { white-space: nowrap; }" in CSS


def test_las_celdas_se_alinean_al_medio():
    """Con avatares, etiquetas y texto en la misma fila, la línea base dejaba
    cada cosa a una altura distinta."""
    bloque = CSS.split("th, td {")[1].split("}")[0]

    assert "vertical-align: middle" in bloque


# ---------- Euro / Dólar ----------

def test_se_puede_invertir_la_cotizacion():
    assert 'id="fx-invertir"' in DASHBOARD
    assert "EUR por 1 USD" in DASHBOARD and "USD por 1 EUR" in DASHBOARD


def test_la_preferencia_de_inversion_se_recuerda():
    """Quien piensa en dólares la quiere siempre así, no una vez."""
    assert "dash.fx.invertido" in DASHBOARD


def test_hay_rango_diario():
    """Sin él, al pasar el porcentaje a seguir el rango se perdía el dato del
    día, que era el único que se mostraba antes."""
    assert 'data-days="1"' in DASHBOARD


def test_cada_rango_tiene_su_etiqueta():
    """El porcentaje era siempre el del día dijera lo que dijera el botón
    pulsado; ahora la etiqueta tiene que decir a qué periodo corresponde."""
    etiquetas = re.search(r"FX_ETIQUETAS = \{([^}]*)\}", DASHBOARD).group(1)

    for dias in ["1", "7", "30", "90", "365", "0"]:
        assert dias + ":" in etiquetas.replace(" ", "")


def test_la_frase_usa_la_contraccion():
    """«frente a el dólar» no es español."""
    # Sin los comentarios: uno de ellos cita la forma incorrecta para explicar
    # por qué se corrigió, y hacía fallar al propio test.
    codigo = [l for l in DASHBOARD.splitlines() if not l.strip().startswith("//")]

    assert any("frente al " in l for l in codigo)
    assert not any("frente a el" in l for l in codigo)
