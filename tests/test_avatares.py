"""Avatar de activo: dos iniciales y color estable.

Las listas eran columnas de texto del mismo peso: había que leer para distinguir
una fila de otra. El avatar da un ancla de color antes de leer nada.
"""
import pytest

from app.models import Asset, AssetType, Currency
from app.templating import color_activo, iniciales


# ---------- Iniciales ----------

@pytest.mark.parametrize("nombre,esperado", [
    # Palabras de relleno: casi todos los fondos las llevan, gastar en ellas una
    # de las dos letras sería tirarla
    ("MUESTRA Corporation", "MU"),
    ("Amazon.com, Inc.", "AM"),
    ("Delaney's Corporation", "DE"),
    ("ACME Therapeutics SA", "AT"),
    ("International Ejemplo Machines Corporation", "IE"),
    # Los paréntesis no aportan inicial: "Alfabeto (A)" no puede dar "A("
    ("Alfabeto (A)", "AL"),
    ("Fondo Global USD (Acc)", "FG"),
    ("FTSE All-World USD (Acc)", "FA"),
    # Una sola palabra útil: sus dos primeros caracteres
    ("OKX", "OK"),
    ("Apple", "AP"),
    ("Ejemplo", "EJ"),
    ("Bróker X", "BX"),
])
def test_iniciales_de_nombres_con_las_trampas_tipicas(nombre, esperado):
    assert iniciales(nombre) == esperado


def test_si_el_nombre_no_sirve_se_usa_el_ticker():
    assert iniciales("(Acc)", "IGLO.DE") == "IG"


def test_sin_nada_no_revienta():
    assert iniciales(None) == "??"
    assert iniciales("") == "??"
    assert iniciales("   ") == "??"


# ---------- Color ----------

def test_el_color_es_estable_entre_llamadas():
    """Si cambiara, el mismo activo saldría de un color en la lista y de otro en
    su ficha. Por eso se usa crc32 y no hash(), que lleva sal por proceso."""
    assert color_activo("AAPL") == color_activo("AAPL")


def test_el_color_no_depende_de_mayusculas_ni_espacios():
    assert color_activo(" aapl ") == color_activo("AAPL")


def test_activos_distintos_tienden_a_colores_distintos():
    claves = ["AAPL", "MSFT", "NVDA", "TSLA", "GOOGL", "AMZN", "META", "IBM"]
    colores = {color_activo(c) for c in claves}

    assert len(colores) >= len(claves) - 1, "demasiadas colisiones de color"


def test_sin_clave_no_revienta():
    assert color_activo(None).startswith("hsl(")


# ---------- En las páginas ----------

@pytest.fixture
def cartera(client):
    client.db.add_all([
        Asset(name="MUESTRA Corporation", asset_type=AssetType.ACCION,
              currency=Currency.USD, ticker="NVDA", current_price=100.0),
        Asset(name="Alfabeto (A)", asset_type=AssetType.ACCION,
              currency=Currency.EUR, ticker="ALFA.DE", current_price=250.0),
    ])
    client.db.commit()
    return client


def test_la_lista_de_activos_pinta_avatares(cartera):
    html = cartera.get("/activos").text

    assert 'class="avatar-activo"' in html
    assert ">MU<" in html and ">AL<" in html


def test_la_ficha_pinta_el_avatar(cartera):
    asset = cartera.db.query(Asset).filter(Asset.ticker == "NVDA").one()

    html = cartera.get("/activos/%d" % asset.id).text

    assert 'class="avatar-activo"' in html
    assert ">MU<" in html


def test_el_avatar_no_lo_lee_un_lector_de_pantalla(cartera):
    """Es decoración: el nombre del activo ya está al lado en texto, y leer "MU"
    antes de "MUESTRA Corporation" solo estorba."""
    html = cartera.get("/activos").text

    assert 'aria-hidden="true"' in html
