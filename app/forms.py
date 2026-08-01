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
from typing import Annotated

from fastapi import Form
from pydantic import BeforeValidator

_blank_to_none = BeforeValidator(lambda v: None if v in ("", None) else v)

OptFloat = Annotated[float | None, _blank_to_none, Form()]
OptInt = Annotated[int | None, _blank_to_none, Form()]
