"""Normalización de nombres de activo para comparación aproximada.

Vive en la capa de servicios y no en el router de importación, que es donde
estaba: `services/fusion.py` la necesitaba y la importaba **desde el router**,
con un import perezoso para no crear un ciclo. Ese import perezoso era la señal
de que la dirección de la dependencia estaba al revés — routers dependen de
servicios, no al contrario.

El criterio tiene que ser el mismo en los dos sitios: si el importador y el
detector de duplicados normalizaran distinto, un activo que el importador casa
con otro no saldría luego como candidato a fusión, o al revés.
"""
import re

# Patrones que se eliminan al normalizar nombres para fuzzy matching
_RE_SUFIJO = re.compile(
    r'\b(Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|'
    r'SA\.?|S\.A\.|AG|GmbH|SE|NV|PLC|LLC|LP|'
    r'\([A-Z]\)|Class [A-Z])\b', re.IGNORECASE
)
_RE_PUNTUACION = re.compile(r'[,.()]+')


def normalizar_nombre(name: str) -> str:
    """Nombre de activo listo para comparar: sin sufijos legales, sin clases de
    acción y sin puntuación, en mayúsculas y con los espacios colapsados."""
    name = _RE_SUFIJO.sub('', name)
    name = _RE_PUNTUACION.sub(' ', name)
    return ' '.join(name.upper().split())
