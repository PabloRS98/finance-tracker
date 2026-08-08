"""Traducción de fallos de las APIs de precios a un diagnóstico accionable.

`get_stock_price` capturaba todo y devolvía `None`: un ticker que no existe, una
cuota agotada, un timeout y un corte de red eran indistinguibles, y la interfaz
decía lo mismo en los cuatro casos —"No se pudo actualizar el precio"—. Con eso
no hay forma de saber si hay que corregir el ticker, esperar veinte minutos o
mirar la red.

Se porta el planteamiento de `projects-dashboard/app/services/forge_errors.py`,
adaptado a lo que se usa aquí: Yahoo, CoinGecko y Frankfurter son públicas y sin
API key, así que no hay caso de "token caducado" ni de "repo privado"; a cambio
sí importa mucho el 429 de CoinGecko, que en el plan gratuito llega enseguida.

La acción que tiene que tomar quien lo lee es distinta en cada caso, y por eso se
distinguen.
"""
import httpx

ULTIMO_ESTADO: dict[str, str] = {}


def describir(exc: Exception, proveedor: str, recurso: str) -> str:
    """Frase corta y accionable a partir de la excepción.

    `proveedor` es el nombre visible ("Yahoo") y `recurso` lo que se pedía —el
    ticker o el id—, que es lo que hay que corregir cuando el fallo es un 404.
    """
    if isinstance(exc, httpx.TimeoutException):
        return "%s no respondió a tiempo" % proveedor
    if isinstance(exc, httpx.HTTPStatusError):
        estado = exc.response.status_code
        if estado == 404:
            return "%s no reconoce «%s»" % (proveedor, recurso)
        if estado == 429:
            return "%s ha cortado por exceso de peticiones; se reintenta en el próximo ciclo" % proveedor
        if 500 <= estado < 600:
            return "%s está caído (HTTP %d)" % (proveedor, estado)
        return "%s rechazó la petición (HTTP %d)" % (proveedor, estado)
    if isinstance(exc, httpx.HTTPError):
        # Sin respuesta: DNS, conexión rechazada, TLS... El contenedor no llega.
        return "No se pudo contactar con %s" % proveedor
    # Cualquier otra cosa es un fallo nuestro al interpretar la respuesta, no un
    # problema del proveedor, y conviene que se lea distinto.
    return "Respuesta inesperada de %s (%s)" % (proveedor, type(exc).__name__)


def registrar(exc: Exception, proveedor: str, recurso: str) -> str:
    """Describe el fallo y lo deja anotado por proveedor, para poder mirarlo."""
    detalle = describir(exc, proveedor, recurso)
    ULTIMO_ESTADO[proveedor] = detalle
    return detalle
