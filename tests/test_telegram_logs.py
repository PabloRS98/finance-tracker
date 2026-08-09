"""[FT-A1] El token del bot no puede acabar escrito en los logs.

`API_URL` lleva el token en la ruta, y el mensaje de `httpx.HTTPStatusError`
incluye la URL entera. `logger.exception` vuelca ese mensaje tal cual, y con el
driver `json-file` del compose el log persiste en disco: es lo primero que uno
pega en un issue pidiendo ayuda.

Un token filtrado permite enviar mensajes suplantando al bot, leer los updates
pendientes vía getUpdates -que en esta app llevan importes y nombres de
activos- y secuestrarlo cambiando su webhook.
"""
import logging

import httpx

from app.config import settings
from app.services import telegram

TOKEN_DE_MENTIRA = "7123456789:AAHesto-no-es-un-token-real-solo-para-el-test"


# Aquí había una fixture que reactivaba `telegram.logger` antes de cada test:
# migrar dentro del proceso lo dejaba `disabled`, así que estos tests pasaban o
# no según qué fichero se hubiera ejecutado antes. Era un parche sobre el
# síntoma (FT-X1). Arreglada la causa en migrations/env.py, sobra.


def test_el_log_de_telegram_no_contiene_el_token(monkeypatch, caplog):
    monkeypatch.setattr(settings, "telegram_bot_token", TOKEN_DE_MENTIRA)

    def _post_que_falla(url, **kwargs):
        return httpx.Response(401, request=httpx.Request("POST", url))

    monkeypatch.setattr(telegram.httpx, "post", _post_que_falla)

    with caplog.at_level(logging.DEBUG):
        assert telegram._api("sendMessage", chat_id="1", text="hola") is None

    assert TOKEN_DE_MENTIRA not in caplog.text
    assert "sendMessage" in caplog.text
    assert "401" in caplog.text  # el diagnóstico útil sí se conserva


def test_el_log_de_descarga_no_contiene_el_token(monkeypatch, caplog):
    """download_file construye su propia URL con el token, aparte de _api."""
    monkeypatch.setattr(settings, "telegram_bot_token", TOKEN_DE_MENTIRA)
    monkeypatch.setattr(telegram, "_api", lambda metodo, **kw: {"file_path": "voice/file_1.oga"})

    def _get_que_falla(url, **kwargs):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(telegram.httpx, "get", _get_que_falla)

    with caplog.at_level(logging.DEBUG):
        assert telegram.download_file("id-de-archivo") is None

    assert TOKEN_DE_MENTIRA not in caplog.text
    assert "404" in caplog.text


def test_un_timeout_se_registra_por_su_tipo(monkeypatch, caplog):
    """Sin respuesta no hay código HTTP: queda el tipo de excepción, que es lo
    que distingue 'la red no va' de 'Telegram me ha rechazado'."""
    monkeypatch.setattr(settings, "telegram_bot_token", TOKEN_DE_MENTIRA)

    def _post_que_expira(url, **kwargs):
        raise httpx.ConnectTimeout("se agotó el tiempo", request=httpx.Request("POST", url))

    monkeypatch.setattr(telegram.httpx, "post", _post_que_expira)

    with caplog.at_level(logging.DEBUG):
        assert telegram._api("sendMessage", chat_id="1", text="hola") is None

    assert TOKEN_DE_MENTIRA not in caplog.text
    assert "ConnectTimeout" in caplog.text


def test_las_apis_publicas_siguen_volcando_la_traza_completa():
    """Yahoo, CoinGecko y Frankfurter no llevan credenciales en la URL.

    Ahí la traza entera es información útil y se deja como está: este hallazgo
    es sobre secretos filtrados, no sobre logs verbosos."""
    from pathlib import Path

    servicios = Path(telegram.__file__).resolve().parent
    for modulo in ("market_data.py", "history.py", "classify.py"):
        assert "logger.exception" in (servicios / modulo).read_text(encoding="utf-8")
