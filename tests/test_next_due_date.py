"""[FT-M11] `next_due_date` podía colgar el hilo y devolvía None implícitamente.

Dos cosas:

- `_occurrences` es un generador **infinito** (`while True`). Si `last_generated`
  llevara una fecha absurdamente lejana, el bucle no termina nunca: no devuelve
  None, cuelga. Nada impide ese valor —lo escriben la generación y el toggle, y
  una base restaurada a medias o una edición manual puede dejar cualquier cosa—.
- El tipo de retorno declaraba `date`, pero el `for` sin `return` al final
  devolvería `None` en cuanto el generador dejara de ser infinito.

Y el parámetro `today` se recibía y se ignoraba por completo, mientras el router
se lo pasaba.
"""
from datetime import date

import pytest

from app.models import RecurringTransaction, TransactionType
from app.services.recurring import MAX_OCURRENCIAS, next_due_date


def _regla(**kw) -> RecurringTransaction:
    base = {
        "name": "Regla", "type": TransactionType.GASTO, "amount": 100,
        "interval_months": 1, "day_of_month": 10,
        "start_date": date(2026, 1, 10),
    }
    base.update(kw)
    return RecurringTransaction(**base)


def test_no_propone_un_cargo_en_el_ano_2999():
    """Con un `last_generated` absurdo, antes recorría ~11.700 ocurrencias y
    acababa devolviendo 2999-01-10, que se pintaba tal cual como "próximo
    cargo". Ahora se acota y se devuelve None, que el router ya sabe mostrar."""
    regla = _regla(last_generated=date(2999, 1, 1))

    assert next_due_date(regla) is None


def test_una_fecha_imposible_no_revienta_la_pagina():
    """Con `date.max` el generador pasaba del año 9999 y lanzaba
    `ValueError: year 10000 is out of range`, sin capturar, en mitad de
    /recurrentes. Comprobado contra el código anterior."""
    regla = _regla(last_generated=date.max)

    assert next_due_date(regla) is None


def test_la_cota_cubre_un_siglo_de_ocurrencias_mensuales():
    """La cota tiene que ser holgada: acotar de menos rompería reglas legítimas."""
    assert MAX_OCURRENCIAS >= 1200


def test_devuelve_la_primera_sin_generar():
    regla = _regla(last_generated=date(2026, 3, 10))

    assert next_due_date(regla) == date(2026, 4, 10)


def test_sin_nada_generado_devuelve_la_primera():
    assert next_due_date(_regla()) == date(2026, 1, 10)


def test_respeta_el_intervalo():
    regla = _regla(interval_months=3, last_generated=date(2026, 1, 10))

    assert next_due_date(regla) == date(2026, 4, 10)


# ---------- El parámetro `today`, que antes se ignoraba ----------

def test_next_due_date_usa_el_parametro_today():
    """Con `today`, no se propone como "próximo cargo" una fecha ya pasada.

    Ocurre con una regla reactivada tras meses parada: `last_generated` se quedó
    atrás y la siguiente ocurrencia teórica cae en el pasado."""
    regla = _regla(last_generated=date(2026, 1, 10))

    assert next_due_date(regla, today=date(2026, 6, 1)) == date(2026, 6, 10)


def test_sin_today_no_se_filtra_por_la_fecha_de_hoy():
    """Quien no pasa `today` quiere la siguiente sin generar, pasada o no: es lo
    que necesita el catch-up."""
    regla = _regla(last_generated=date(2026, 1, 10))

    assert next_due_date(regla) == date(2026, 2, 10)


def test_today_justo_en_una_ocurrencia_la_incluye():
    regla = _regla(last_generated=date(2026, 1, 10))

    assert next_due_date(regla, today=date(2026, 2, 10)) == date(2026, 2, 10)


@pytest.mark.parametrize("dia", [29, 30, 31])
def test_los_meses_cortos_no_rompen_la_cuenta(dia):
    """El día se ajusta al último del mes, y eso no puede saltarse ocurrencias."""
    regla = _regla(day_of_month=dia, start_date=date(2026, 1, dia))

    assert next_due_date(regla) == date(2026, 1, dia)
