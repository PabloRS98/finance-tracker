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

logger = logging.getLogger(__name__)


def _se_cumple(alerta: Alerta) -> bool:
    """True si la condición de la alerta se da con el precio actual."""
    asset = alerta.asset
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
    """Texto del aviso, en el mismo tono que el resumen diario."""
    asset = alerta.asset
    divisa = asset.currency.value
    precio = dinero(asset.current_price)

    if alerta.tipo == TipoAlerta.POR_ENCIMA:
        return "🔔 <b>%s</b> ha subido por encima de %s %s\nAhora: %s %s" % (
            asset.name, dinero(alerta.valor), divisa, precio, divisa,
        )
    if alerta.tipo == TipoAlerta.POR_DEBAJO:
        return "🔔 <b>%s</b> ha bajado por debajo de %s %s\nAhora: %s %s" % (
            asset.name, dinero(alerta.valor), divisa, precio, divisa,
        )
    caida = 100.0 * (asset.current_price - asset.previous_close) / asset.previous_close
    return "🔻 <b>%s</b> cae hoy un %s\nAhora: %s %s" % (
        asset.name, ("%.2f%%" % abs(caida)).replace(".", ","), precio, divisa,
    )


def comprobar(db: Session) -> list[str]:
    """Evalúa las alertas activas y devuelve los mensajes que hay que enviar.

    No envía nada: así se puede probar sin tocar la red, y quien llama decide
    qué hacer con ellos."""
    avisos: list[str] = []
    alertas = (
        db.query(Alerta)
        .options(joinedload(Alerta.asset))  # evita una consulta por alerta
        .filter(Alerta.activa.is_(True))
        .all()
    )

    for alerta in alertas:
        cumple = _se_cumple(alerta)
        if cumple and alerta.ultimo_disparo is None:
            avisos.append(mensaje(alerta))
            alerta.ultimo_disparo = utcnow().replace(microsecond=0)
        elif not cumple and alerta.ultimo_disparo is not None:
            # Se rearma: la condición dejó de darse y el próximo cruce sí avisa
            alerta.ultimo_disparo = None

    if avisos:
        logger.info("Alertas de precio disparadas: %d", len(avisos))
    return avisos


def comprobar_y_enviar(db: Session) -> int:
    """Comprueba y manda por Telegram lo que haya saltado. Devuelve cuántos."""
    from . import telegram

    avisos = comprobar(db)
    if avisos and telegram.is_configured():
        for aviso in avisos:
            telegram.send_message(aviso)
    return len(avisos)
