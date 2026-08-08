"""Tipos de campo de formulario que toleran valores vacíos del navegador.

Un <input> HTML vacío se envía como cadena vacía ("") —campo presente, no
ausente—, así que un `Form(None)` no ayuda: pydantic v2 falla al parsear ""
como número (422 float_parsing). Estos tipos convierten "" (y None) en None
ANTES de validar, conservando el 422 para basura real (p. ej. "abc").

IMPORTANTE: el `Form()` va DENTRO del Annotated. FastAPI solo respeta el
BeforeValidator si el default del parámetro se pone con "=" fuera del tipo:

    quantity: OptFloat = None        # OK  "" -> None
    quantity: OptFloat = Form(None)  # NO  FastAPI ignora el validator -> 422
"""
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import Form
from pydantic import BeforeValidator

_blank_to_none = BeforeValidator(lambda v: None if v in ("", None) else v)

OptFloat = Annotated[float | None, _blank_to_none, Form()]
OptInt = Annotated[int | None, _blank_to_none, Form()]


# Money es Numeric(12, 2): admite hasta 9 999 999 999,99. Por encima, SQLite
# guarda el valor sin quejarse y el dato vuelve mal al leerlo.
IMPORTE_MAXIMO = Decimal("1000000000")


def _texto_a_decimal(valor):
    """Texto del formulario a Decimal, aceptando la coma como separador.

    `Decimal(str(v))` y no `float`: los importes del libro se guardan en
    `Numeric` precisamente para que diez gastos de 0,10 € sumen 1,00 y no
    0.9999999999999999. Si la puerta de entrada es float, ese ruido se cuela
    igual y la decisión de diseño no sirve de nada.

    La coma es lo que teclea un usuario español y antes daba un 422.
    """
    if valor in ("", None):
        return None
    try:
        return Decimal(str(valor).strip().replace(",", "."))
    except InvalidOperation:
        # Basura de verdad: se deja pasar para que pydantic dé su 422, igual
        # que hacen OptFloat y OptInt con "abc".
        return valor


# Admite None y se declara con `= None`, no con `Form(...)`: el Form() ya va
# dentro del Annotated y ponerlo también fuera hace que FastAPI rechace la
# firma. La obligatoriedad la impone `validar_importe`, que además devuelve un
# aviso legible en vez del 422 de pydantic.
_a_decimal = BeforeValidator(_texto_a_decimal)

Importe = Annotated[Decimal | None, _a_decimal, Form()]


def validar_importe(amount: Decimal | None) -> str | None:
    """Motivo por el que el importe no es válido, o None.

    El resto de la app ya validaba lo suyo —cantidad y precio en las
    operaciones, valor en las alertas, rango en los pesos objetivo— y los
    importes del libro eran el único hueco. Un gasto negativo SUMA al balance
    del mes, descuadra el presupuesto de su categoría y se cuela en el CSV
    exportado, todo sin un solo error.
    """
    if amount is None:
        return "El importe es obligatorio"
    if amount <= 0:
        return "El importe tiene que ser mayor que 0"
    if amount > IMPORTE_MAXIMO:
        return "El importe excede el máximo admitido"
    return None
