"""Cliente de la API de Bot de Telegram (envío, edición, long polling y descarga
de archivos). Si no hay token configurado, todas las funciones son no-op.

El usuario crea el bot con @BotFather y mete el token en TELEGRAM_BOT_TOKEN;
el chat_id se lo dice el propio bot al escribirle (ver telegram_bot.py)."""
import logging

import httpx

from ..config import settings
from ._logging_utils import log_fallo_api

logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot%s/%s"
FILE_URL = "https://api.telegram.org/file/bot%s/%s"


def is_configured() -> bool:
    """True si el bot puede operar de verdad (token + chat autorizado)."""
    return bool(settings.telegram_bot_token and settings.telegram_chat_id)


def _api(method: str, http_timeout: float = 15, **payload):
    """Llama a un método de la API. Devuelve el `result` o None si falla.
    `http_timeout` va aparte del payload: getUpdates tiene su propio `timeout`."""
    if not settings.telegram_bot_token:
        return None
    try:
        resp = httpx.post(
            API_URL % (settings.telegram_bot_token, method),
            json=payload, timeout=http_timeout,
        )
        resp.raise_for_status()
        return resp.json().get("result")
    except Exception as exc:
        # Sin traza: el token va en la URL y el mensaje de HTTPStatusError la
        # incluye entera. Ver _logging_utils.
        log_fallo_api(logger, "Fallo en Telegram.%s", method, exc=exc)
        return None


def send_message(text: str, reply_markup: dict | None = None, chat_id: str | None = None):
    """Envía un mensaje HTML al chat configurado (u otro explícito, solo para el
    mensaje de bootstrap con el chat_id). Devuelve el mensaje enviado o None."""
    target = chat_id or settings.telegram_chat_id
    if not target:
        return None
    payload: dict = {
        "chat_id": target, "text": text, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _api("sendMessage", **payload)


def edit_message(message_id: int, text: str):
    """Reescribe un mensaje del bot (quita también los botones)."""
    return _api(
        "editMessageText",
        chat_id=settings.telegram_chat_id, message_id=message_id,
        text=text, parse_mode="HTML",
    )


def answer_callback(callback_id: str, text: str = ""):
    return _api("answerCallbackQuery", callback_query_id=callback_id, text=text)


def get_updates(offset: int | None, timeout: int = 25) -> list[dict]:
    """Long polling de getUpdates. `offset` confirma los updates ya procesados
    (Telegram no los reenvía). Lista vacía si no hay nada o falla."""
    payload: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    return _api("getUpdates", http_timeout=timeout + 10, **payload) or []


def download_file(file_id: str) -> bytes | None:
    """Descarga un archivo de Telegram (nota de voz) por su file_id."""
    info = _api("getFile", file_id=file_id)
    if not info or not info.get("file_path"):
        return None
    try:
        resp = httpx.get(
            FILE_URL % (settings.telegram_bot_token, info["file_path"]), timeout=60,
        )
        resp.raise_for_status()
        return resp.content
    except Exception as exc:
        # FILE_URL también lleva el token en la ruta.
        log_fallo_api(logger, "Fallo al descargar archivo de Telegram %s", file_id, exc=exc)
        return None
