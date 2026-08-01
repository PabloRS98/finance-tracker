"""El sistema visual tiene planos y jerarquía, y no debe volver a aplanarse.

La versión anterior tenía 1,10 de contraste entre tarjeta y fondo: sobre el
papel había once tarjetas, en pantalla la página era una mancha oscura uniforme.
Y las once compartían fondo, borde, radio y sombra idénticos, así que el
patrimonio recibía el mismo tratamiento que "Gastos por categoría".

Esto no comprueba que sea bonito —eso no se testea— sino que las decisiones que
lo sostienen siguen ahí: tres planos separables y un hero distinto del resto.
"""
from pathlib import Path

import pytest

CSS = Path("app/static/css/style.css").read_text(encoding="utf-8")


def _token(nombre: str) -> str:
    """Valor de una variable CSS declarada en :root."""
    for linea in CSS.splitlines():
        if linea.strip().startswith("--%s:" % nombre):
            return linea.split(":", 1)[1].split(";")[0].strip()
    raise AssertionError("no existe el token --%s" % nombre)


def _luminancia(hexa: str) -> float:
    c = hexa.lstrip("#")
    canales = []
    for i in (0, 2, 4):
        x = int(c[i:i + 2], 16) / 255
        canales.append(x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4)
    r, g, b = canales
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a: str, b: str) -> float:
    x, y = sorted((_luminancia(a), _luminancia(b)), reverse=True)
    return (x + 0.05) / (y + 0.05)


# ---------- Los tres planos ----------

def test_la_tarjeta_se_distingue_del_fondo():
    """1,10 era invisible. Por debajo de 1,25 se vuelve a perder el borde entre
    lo que es tarjeta y lo que es página."""
    assert contraste(_token("card"), _token("bg")) >= 1.25


def test_el_plano_elevado_esta_por_encima_de_la_tarjeta():
    assert _luminancia(_token("card-alto")) > _luminancia(_token("card"))


def test_el_borde_se_ve_sobre_la_tarjeta():
    assert contraste(_token("border"), _token("card")) >= 1.4


@pytest.mark.parametrize("token", ["text", "text-2", "text-3"])
def test_el_texto_sigue_siendo_legible_sobre_la_tarjeta(token):
    """Aclarar la tarjeta baja el contraste del texto: el gris más apagado se
    quedaba por debajo de lo legible y hubo que subirlo."""
    assert contraste(_token(token), _token("card")) >= 4.4


# ---------- Jerarquía ----------

def test_hay_dos_elevaciones_y_no_una_sola_sombra():
    assert "--sombra-1:" in CSS and "--sombra-2:" in CSS


def test_el_hero_no_usa_la_misma_caja_que_las_demas_tarjetas():
    """Es lo único de la página con acento en el borde: si se iguala al resto,
    la portada vuelve a no tener dónde aterrizar la mirada."""
    bloque = CSS.split(".hero-card {")[1].split("}")[0]

    assert "--card-alto" in bloque
    assert "--sombra-2" in bloque
    assert "--accent" in bloque


def test_los_dialogos_flotan_sobre_el_contenido():
    bloque = CSS.split("\ndialog {")[1].split("}")[0]

    assert "--card-alto" in bloque and "--sombra-2" in bloque
