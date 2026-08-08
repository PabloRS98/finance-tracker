"""[FT-M15] Los fallos de las APIs de precios se distinguen entre sí.

`get_stock_price` capturaba todo y devolvía `None`: un ticker inexistente, una
cuota agotada, un timeout y un corte de red eran indistinguibles, y la interfaz
decía lo mismo en los cuatro casos. Con eso no hay forma de saber si hay que
corregir el ticker, esperar veinte minutos o mirar la red.

Se porta el planteamiento de `forge_errors.py` de `projects-dashboard`,
adaptado: aquí las APIs son públicas y sin key, así que no hay "token caducado";
a cambio importa mucho el 429 de CoinGecko, que en el plan gratuito llega
enseguida.
"""
import httpx
import pytest

from app.services import market_data
from app.services.errores_api import ULTIMO_ESTADO, describir


def _fallo_http(estado: int) -> httpx.HTTPStatusError:
    peticion = httpx.Request("GET", "https://ejemplo.invalid/precio")
    return httpx.HTTPStatusError(
        "error", request=peticion, response=httpx.Response(estado, request=peticion),
    )


@pytest.mark.parametrize(("excepcion", "fragmento"), [
    (_fallo_http(404), "no reconoce"),
    (_fallo_http(429), "exceso de peticiones"),
    (_fallo_http(503), "está caído"),
    (_fallo_http(400), "rechazó la petición"),
    (httpx.ConnectTimeout("tarde"), "no respondió a tiempo"),
    (httpx.ConnectError("sin red"), "No se pudo contactar"),
    (ValueError("json raro"), "Respuesta inesperada"),
])
def test_market_data_distingue_errores(excepcion, fragmento):
    """El criterio de aceptación del hallazgo: cuatro casos, cuatro mensajes."""
    assert fragmento in describir(excepcion, "Yahoo", "MSTR")


def test_el_mensaje_nombra_el_recurso_cuando_hay_que_corregirlo():
    """Con un 404 lo que hay que arreglar es el ticker, así que se dice cuál."""
    assert "MSTR" in describir(_fallo_http(404), "Yahoo", "MSTR")


def test_todos_los_mensajes_son_distintos():
    """Si dos casos dieran la misma frase, no se habría arreglado nada."""
    casos = [_fallo_http(404), _fallo_http(429), _fallo_http(503),
             httpx.ConnectTimeout("t"), httpx.ConnectError("c")]
    mensajes = {describir(e, "Yahoo", "X") for e in casos}

    assert len(mensajes) == len(casos)


def test_el_precio_de_una_accion_registra_el_diagnostico(monkeypatch, caplog):
    import logging

    def _404(*args, **kwargs):
        raise _fallo_http(404)

    monkeypatch.setattr(market_data.httpx, "get", _404)
    ULTIMO_ESTADO.pop("Yahoo", None)

    with caplog.at_level(logging.WARNING):
        assert market_data.get_stock_price("NOEXISTE") is None

    assert "no reconoce" in ULTIMO_ESTADO["Yahoo"]


def test_el_precio_de_una_cripto_distingue_la_cuota(monkeypatch):
    def _429(*args, **kwargs):
        raise _fallo_http(429)

    monkeypatch.setattr(market_data.httpx, "get", _429)
    ULTIMO_ESTADO.pop("CoinGecko", None)

    assert market_data.get_crypto_price("bitcoin") is None
    assert "exceso de peticiones" in ULTIMO_ESTADO["CoinGecko"]
