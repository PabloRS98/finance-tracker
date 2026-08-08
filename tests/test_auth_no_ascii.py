"""[FT-C2] La autenticación tiene que aguantar contraseñas con tildes y eñes.

El `.env.example` está en español, así que `AUTH_PASSWORD=contraseña` es lo más
natural del mundo. Dos cosas lo rompían:

- `secrets.compare_digest` sobre `str` exige ASCII puro y lanza `TypeError` con
  cualquier tilde, así que unas credenciales incorrectas devolvían un 500 en vez
  de un 401.
- `fastapi.security.HTTPBasic` decodifica la cabecera con `.decode("ascii")`, de
  modo que la contraseña CORRECTA tampoco llegaba nunca a compararse.

El resultado combinado era quedarse fuera de la app sin ninguna pista, y la
reacción natural —desactivar ENABLE_AUTH para poder entrar— deja la aplicación
del patrimonio abierta en la LAN.
"""
import base64

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import auth
from app.config import settings

CLAVE_CON_TILDE = "contraseña-larga"


def _cabecera_basic(usuario: str, clave: str) -> str:
    """Cabecera `Authorization` como la manda un navegador: base64 de UTF-8."""
    par = "%s:%s" % (usuario, clave)
    return "Basic " + base64.b64encode(par.encode("utf-8")).decode("ascii")


def _peticion(cabecera: str | None) -> Request:
    cabeceras = [(b"authorization", cabecera.encode("utf-8"))] if cabecera else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": cabeceras})


@pytest.fixture
def auth_con_tilde(monkeypatch):
    monkeypatch.setattr(settings, "enable_auth", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", CLAVE_CON_TILDE)


def test_credenciales_no_ascii_devuelven_401(auth_con_tilde):
    """Con la contraseña configurada con tilde, fallar tiene que ser un 401.

    Antes reventaba con TypeError dentro de compare_digest, y el manejador
    global lo convertía en la página "Algo ha fallado": un 500."""
    with pytest.raises(HTTPException) as fallo:
        auth.verify_auth(_peticion(_cabecera_basic("admin", "otra")))

    assert fallo.value.status_code == 401
    assert fallo.value.headers["WWW-Authenticate"] == "Basic"


def test_credenciales_no_ascii_correctas_autentican(auth_con_tilde):
    assert auth.verify_auth(_peticion(_cabecera_basic("admin", CLAVE_CON_TILDE))) is True


def test_sin_cabecera_pide_credenciales(auth_con_tilde):
    with pytest.raises(HTTPException) as fallo:
        auth.verify_auth(_peticion(None))

    assert fallo.value.status_code == 401


def test_cabecera_corrupta_no_revienta(auth_con_tilde):
    """Un base64 inválido es un 401, no una excepción sin controlar."""
    with pytest.raises(HTTPException) as fallo:
        auth.verify_auth(_peticion("Basic esto-no-es-base64-valido"))

    assert fallo.value.status_code == 401


# ---------- Contra la app real ----------

def test_pagina_con_clave_no_ascii_correcta_da_acceso(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_auth", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", CLAVE_CON_TILDE)

    assert client.get("/activos", auth=("admin", CLAVE_CON_TILDE)).status_code == 200


def test_pagina_con_clave_no_ascii_incorrecta_devuelve_401(client, monkeypatch):
    monkeypatch.setattr(settings, "enable_auth", True)
    monkeypatch.setattr(settings, "auth_username", "admin")
    monkeypatch.setattr(settings, "auth_password", CLAVE_CON_TILDE)

    assert client.get("/activos", auth=("admin", "otra")).status_code == 401
