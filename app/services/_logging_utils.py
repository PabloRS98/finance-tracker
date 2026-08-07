"""Ayuda para loguear fallos de red sin filtrar credenciales.

La API de Telegram lleva el token del bot en la propia ruta de la URL. Cuando
la petición falla, `httpx.Response.raise_for_status()` lanza una excepción cuyo
mensaje incluye la URL COMPLETA, token incluido:

    Client error '401 Unauthorized' for url
    'https://api.telegram.org/bot7123456789:AAH...token.../sendMessage'

`logger.exception(...)` vuelca esa excepción tal cual al log, y con
`docker-compose.yml` usando el driver `json-file`, ese log persiste en disco y
acaba fácilmente pegado en un issue de GitHub. Esta función registra solo el
código HTTP (o el tipo de excepción si no hay respuesta, p. ej. un timeout),
nunca la excepción cruda.

Un token de bot filtrado permite enviar mensajes suplantando al bot, leer los
updates pendientes con getUpdates —que en esta app llevan importes y nombres de
activos— y secuestrarlo cambiando su webhook.

Solo se usa donde la URL lleva secreto. Yahoo, CoinGecko y Frankfurter son
públicas: ahí la traza completa es información útil y se deja como está."""
import logging


def log_fallo_api(logger: logging.Logger, mensaje: str, *args, exc: Exception) -> None:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    sufijo = " (HTTP %s)" % status if status else " (%s)" % type(exc).__name__
    logger.warning(mensaje + sufijo, *args)
