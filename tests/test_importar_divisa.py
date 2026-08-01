"""Una fila de importación no puede colgarse de un activo en otra divisa.

Las operaciones no guardan divisa: heredan la del activo. Así que importar una
fila en euros sobre un activo que cotiza en dólares no convierte nada, solo
reinterpreta el precio, y el coste medio pasa a mezclar dos divisas sin dejar
rastro. Se provoca sin querer con facilidad: el extracto de Trade Republic va
siempre en euros (`trade_republic.parse` fija currency="EUR") y el mismo valor
puede existir ya en cartera comprado en dólares desde otro bróker.
"""
import pytest

from app.models import Asset, AssetType, Currency, Operation
from app.routers.imports import _conflicto_de_divisa, _match_asset
from app.services.importers import ParsedRow

CSV_TRADE_REPUBLIC = (
    "Date;Type;ISIN;Name;Shares;Price;Fee\n"
    "2025-04-02;Buy;US0378331005;Apple Inc.;3;168,25;1,00\n"
)

PAYLOAD_EN_EUROS = (
    '{"d": "2025-04-02", "t": "compra", "n": "Apple Inc.", "tk": null,'
    ' "i": "US0378331005", "q": 3, "p": 168.25, "f": 1.0, "c": "EUR", "k": "accion"}'
)


def _apple(currency: Currency) -> Asset:
    return Asset(name="Apple Inc.", asset_type=AssetType.ACCION,
                 currency=currency, ticker="AAPL")


def _fila_en_euros() -> ParsedRow:
    return ParsedRow(type="compra", name="Apple Inc.", isin="US0378331005",
                     quantity=3, unit_price=168.25, currency="EUR", kind="accion")


def test_se_detecta_la_fila_en_otra_divisa():
    apple_usd = _apple(Currency.USD)
    fila = _fila_en_euros()
    assert _match_asset(fila, [apple_usd]) is apple_usd, "el fallo depende de que primero case"

    motivo = _conflicto_de_divisa(fila, apple_usd)

    assert motivo is not None
    assert "USD" in motivo and "EUR" in motivo


def test_la_misma_divisa_no_es_conflicto():
    assert _conflicto_de_divisa(_fila_en_euros(), _apple(Currency.EUR)) is None


def test_una_fila_sin_activo_que_casar_no_es_conflicto():
    """Sin activo previo se crea uno nuevo con la divisa de la fila: no hay mezcla."""
    assert _conflicto_de_divisa(_fila_en_euros(), None) is None


@pytest.fixture
def apple_en_dolares(client):
    """Apple ya en cartera, comprada en dólares desde otro bróker."""
    client.db.add(_apple(Currency.USD))
    client.db.commit()
    return client


def test_el_preview_marca_la_fila_y_no_la_deja_enviar(apple_en_dolares):
    client = apple_en_dolares

    respuesta = client.post_form(
        "/operaciones/importar/preview",
        data={"formato": "trade_republic"},
        files={"archivo": ("tr.csv", CSV_TRADE_REPUBLIC.encode("utf-8"), "text/csv")},
    )

    assert respuesta.status_code == 200
    assert "divisa" in respuesta.text.lower()
    # Sin payload no hay campo oculto que enviar: la fila no es importable
    assert 'name="rows"' not in respuesta.text


def test_confirmar_rechaza_la_fila_aunque_se_fuerce_el_formulario(apple_en_dolares):
    """El payload viaja en un campo oculto: manda la revalidación del servidor."""
    client = apple_en_dolares

    respuesta = client.post_form("/operaciones/importar/confirmar",
                                 data={"rows": [PAYLOAD_EN_EUROS]}, follow_redirects=False)

    assert respuesta.status_code == 303
    assert client.db.query(Operation).count() == 0, "no debe crearse ninguna operación"


def test_se_importa_cuando_la_divisa_coincide(client):
    """Contraprueba: la misma fila sobre un activo en euros sí entra."""
    client.db.add(_apple(Currency.EUR))
    client.db.commit()

    client.post_form("/operaciones/importar/confirmar",
                     data={"rows": [PAYLOAD_EN_EUROS]}, follow_redirects=False)

    assert client.db.query(Operation).count() == 1
