"""Evaluación de las alertas de precio.

Se comprueban dentro del job que refresca precios: es el único momento en que
hay cotización nueva, así que mirarlas en otro sitio solo repetiría trabajo
sobre datos que no han cambiado.

Cada alerta que salta se rearma sola: mientras la condición siga cumpliéndose no
vuelve a avisar (un activo que cruza a la baja sigue por debajo durante horas, y
avisar en cada refresco sería insoportable), y en cuanto deja de cumplirse queda
lista para el siguiente cruce.
"""
import logging

from sqlalchemy.orm import Session, joinedload

from ..models import Alerta, TipoAlerta, utcnow
from ..templating import dinero
from ._telegram_fmt import escapar

logger = logging.getLogger(__name__)


def _se_cumple(alerta: Alerta) -> bool:
    """True si la condición de la alerta se da con el precio actual."""
    asset = alerta.asset
    if asset is None:
        # Fila huérfana: el activo se borró sin llevarse su alerta. Ya no debería
        # ocurrir (hay cascada en el modelo y ON DELETE en el esquema), pero una
        # base restaurada a medias o un borrado por SQL puede dejar una, y
        # entonces reventaba el ciclo entero: el AttributeError subía hasta el
        # except del scheduler y las alertas dejaban de comprobarse para todos
        # los activos, sin más rastro que una línea en el log.
        logger.warning("Alerta %s apunta a un activo inexistente (%s); se ignora",
                       alerta.id, alerta.asset_id)
        return False
    precio = asset.current_price
    if precio is None:
        return False

    if alerta.tipo == TipoAlerta.POR_ENCIMA:
        return precio > alerta.valor
    if alerta.tipo == TipoAlerta.POR_DEBAJO:
        return precio < alerta.valor
    # Caída diaria: necesita cierre anterior para saber cuánto lleva hoy
    if not asset.previous_close:
        return False
    caida = 100.0 * (precio - asset.previous_close) / asset.previous_close
    return caida <= -abs(alerta.valor)


def mensaje(alerta: Alerta) -> str:
    """Texto del aviso, en el mismo tono que el resumen diario.

    El nombre va escapado: se envía con `parse_mode: "HTML"` y los nombres se
    autorrellenan desde Yahoo, así que llegan tal cual del mercado. Uno con `&`
    —hay de sobra— hacía que Telegram devolviera `400 can't parse entities` y el
    aviso no llegara nunca."""
    asset = alerta.asset
    divisa = asset.currency.value
    precio = dinero(asset.current_price)
    nombre = escapar(asset.name)

    if alerta.tipo == TipoAlerta.POR_ENCIMA:
        return "🔔 <b>%s</b> ha subido por encima de %s %s\nAhora: %s %s" % (
            nombre, dinero(alerta.valor), divisa, precio, divisa,
        )
    if alerta.tipo == TipoAlerta.POR_DEBAJO:
        return "🔔 <b>%s</b> ha bajado por debajo de %s %s\nAhora: %s %s" % (
            nombre, dinero(alerta.valor), divisa, precio, divisa,
        )
    caida = 100.0 * (asset.current_price - asset.previous_close) / asset.previous_close
    return "🔻 <b>%s</b> cae hoy un %s\nAhora: %s %s" % (
        nombre, ("%.2f%%" % abs(caida)).replace(".", ","), precio, divisa,
    )


def _pendientes(db: Session) -> list[tuple[Alerta, str]]:
    """Alertas que deberían avisar ahora, con su mensaje. Rearma las que toque.

    **No marca ninguna como disparada**: eso pasó a `comprobar_y_enviar`, y solo
    si el envío funcionó. Antes se marcaba aquí, con el resultado de que un
    envío fallido -un nombre con `&` que Telegram rechaza, la red caída- daba la
    alerta por avisada y no volvía a intentarse hasta el siguiente rearme. El
    aviso se perdía en silencio.

    El rearme sí se hace aquí: dejar una alerta lista para el próximo cruce no
    puede perder nada.
    """
    pendientes: list[tuple[Alerta, str]] = []
    alertas = (
        db.query(Alerta)
        .options(joinedload(Alerta.asset))  # evita una consulta por alerta
        .filter(Alerta.activa.is_(True))
        .all()
    )

    for alerta in alertas:
        cumple = _se_cumple(alerta)
        if cumple and alerta.ultimo_disparo is None:
            pendientes.append((alerta, mensaje(alerta)))
        elif not cumple and alerta.ultimo_disparo is not None:
            # Se rearma: la condición dejó de darse y el próximo cruce sí avisa
            alerta.ultimo_disparo = None

    return pendientes


def comprobar(db: Session) -> list[str]:
    """Mensajes de las alertas que deberían avisar ahora.

    No envía nada y no marca nada: así se puede probar sin tocar la red."""
    return [texto for _, texto in _pendientes(db)]


def comprobar_y_enviar(db: Session) -> int:
    """Comprueba y manda por Telegram lo que haya saltado. Devuelve cuántos.

    Marca cada alerta **solo si su envío funcionó**. Sin bot configurado no se
    marca nada: no se ha avisado a nadie, y darlo por hecho perdería el aviso el
    día que se configure."""
    from . import telegram

    pendientes = _pendientes(db)
    if not pendientes or not telegram.is_configured():
        return 0

    enviadas = 0
    for alerta, texto in pendientes:
        if telegram.send_message(texto):
            alerta.ultimo_disparo = utcnow().replace(microsecond=0)
            enviadas += 1
        else:
            logger.warning(
                "La alerta %s no se pudo enviar; se reintentará en el próximo ciclo",
                alerta.id,
            )

    if enviadas:
        logger.info("Alertas de precio enviadas: %d", enviadas)
    return enviadas
