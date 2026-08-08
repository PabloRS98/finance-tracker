"""[FT-M16] El parser de voz aceptaba cualquier número suelto como importe.

Agotados los patrones con moneda explícita y con decimales, el último recurso es
"el primer entero que aparezca". Frases perfectamente naturales que salen mal:

    "gasté en el súper de la calle 5"  -> 5 €
    "pagué la factura del 2"           -> 2 €
    "3 cafés"                          -> 3 €  (aquí acierta, por casualidad)

El fallback no se quita —perdería casos legítimos— pero deja de presentarse con
la misma seguridad que un importe dictado bien. El daño estaba acotado porque
todo entra como PENDIENTE, pero el mensaje decía "💸 Gasto 5,00 EUR" sin más y
confirmar es un solo toque.
"""
import pytest

from app.services.voice_parser import parse_amount, parse_voice_text


@pytest.mark.parametrize("frase", [
    "gasté 20 euros en comida",
    "me ingresaron 1500 euros de nómina",
    "gasté 20 dólares en el aeropuerto",
    "pagué 12,50 en el cine",
    "gasté 20 euros con 50 en la farmacia",
])
def test_un_importe_con_moneda_o_decimales_es_de_confianza_alta(frase):
    _, _, confianza = parse_amount(frase)

    assert confianza == "alta"


@pytest.mark.parametrize("frase", [
    "gasté en el súper de la calle 5",
    "pagué la factura del 2",
    "3 cafés",
])
def test_un_entero_suelto_marca_confianza_baja(frase):
    importe, _, confianza = parse_amount(frase)

    assert importe is not None, "el fallback sigue existiendo: no se ha quitado"
    assert confianza == "baja"


def test_sin_ningun_numero_tambien_es_baja():
    importe, _, confianza = parse_amount("gasté en el súper")

    assert importe is None
    assert confianza == "baja"


def test_parse_voice_text_expone_la_confianza(db):
    assert parse_voice_text("gasté 20 euros en comida", db)["confianza"] == "alta"
    assert parse_voice_text("gasté en la calle 5", db)["confianza"] == "baja"


def test_el_aviso_llega_a_la_confirmacion_web(client):
    """Lo que ve el usuario, que es lo que importa: la duda tiene que salir."""
    respuesta = client.post_json("/transacciones/voz", json={"text": "gasté en la calle 5"})

    datos = respuesta.json()
    assert datos["ok"] is True
    assert "seguro" in datos["summary"].lower()


def test_un_importe_claro_no_lleva_aviso(client):
    respuesta = client.post_json("/transacciones/voz", json={"text": "gasté 20 euros en comida"})

    assert "seguro" not in respuesta.json()["summary"].lower()


def test_el_aviso_llega_tambien_por_telegram(db):
    from app.services.telegram_bot import process_text

    reply, _ = process_text(db, "gasté en la calle 5")

    assert "seguro" in reply.lower()
