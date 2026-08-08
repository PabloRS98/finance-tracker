"""[FT-M7] La capa de servicios no puede depender de los routers.

`services/fusion.py` importaba `_normalize_name` desde `routers/imports.py`, y
lo hacía con un import perezoso para no crear un ciclo. Ese import perezoso era
la señal: la dirección correcta es routers → servicios, no al revés.

El criterio de normalización tiene que ser el mismo en los dos sitios, y por eso
la función es compartida y no copiada: si el importador y el detector de
duplicados normalizaran distinto, un activo que el importador casa con otro no
saldría después como candidato a fusión.
"""
from pathlib import Path

import pytest

from app.services.nombres import normalizar_nombre

SERVICIOS = Path(__file__).resolve().parent.parent / "app" / "services"


def test_ningun_servicio_importa_de_los_routers():
    """`grep -rn "from ..routers" app/services/` no devuelve nada."""
    culpables = [
        fichero.name
        for fichero in SERVICIOS.rglob("*.py")
        if "from ..routers" in fichero.read_text(encoding="utf-8")
    ]

    assert culpables == [], "importan del router: %s" % ", ".join(culpables)


@pytest.mark.parametrize(("crudo", "esperado"), [
    ("Apple Inc.", "APPLE"),
    ("Apple Incorporated", "APPLE"),
    ("Siemens AG", "SIEMENS"),
    ("Alphabet Inc. Class A", "ALPHABET"),
    ("  espacios   de   sobra  ", "ESPACIOS DE SOBRA"),
    # Estos tres NO quedan limpios del todo, y es el comportamiento actual: el
    # patrón de sufijos corre ANTES que el de puntuación, así que cuando le toca
    # el turno los paréntesis y los puntos siguen ahí y algunos `\b` no casan.
    # Se fijan tal cual porque mover una función no puede cambiar lo que hace;
    # afinar los patrones sería otro cambio, con sus propios casos de prueba.
    ("Nestlé S.A.", "NESTLÉ S A"),
    ("Vanguard FTSE All-World (Acc)", "VANGUARD FTSE ALL-WORLD ACC"),
    ("iShares Core MSCI World (A)", "ISHARES CORE MSCI WORLD A"),
])
def test_la_normalizacion_no_cambia_de_criterio(crudo, esperado):
    """Fija el comportamiento al mover la función: el fuzzy matching de los
    importadores y el de duplicados dependen de que sea exactamente este."""
    assert normalizar_nombre(crudo) == esperado


def test_el_importador_y_el_detector_usan_la_misma(client):
    """No copiada en dos sitios: la misma. Si divergieran, un activo casado al
    importar no saldría luego como candidato a fusión."""
    from app.routers import imports as router_imports
    from app.services import fusion

    assert router_imports.normalizar_nombre is normalizar_nombre
    assert fusion.normalizar_nombre is normalizar_nombre
