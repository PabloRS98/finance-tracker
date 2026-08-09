"""Quién está usando la app, y si puede.

Son dos cosas distintas y conviene no confundirlas:

- **Quién**: qué persona de la casa. Lo resuelve la cookie de sesión, y de ahí
  sale el aislamiento de datos. Es lo que hace esta capa.
- **Si puede**: si quien llega tiene derecho a entrar. Eso lo dan el binding a
  loopback y `ENABLE_AUTH`, no el selector de usuario. Elegir "Pablo" en una
  lista no es una credencial.

Por eso la contraseña por usuario es opcional: protege de que alguien de casa
abra tus cuentas por error o por curiosidad, no de un tercero en la red. Quien
exponga la app fuera necesita `ENABLE_AUTH` **además** de esto.

El HTTP Basic anterior se retira porque no sabe hacer lo que hace falta: una
contraseña por persona y opcional. El navegador manda unas credenciales o
ninguna, y no hay manera de decirle "para esta no pidas nada y para esta sí".
"""
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from . import sesion
from .config import settings
from .database import get_db
from .models import Usuario


def acceso_permitido(request: Request) -> None:
    """Puerta de entrada opcional (ENABLE_AUTH), delante de todo lo demás.

    Las rutas de elegir usuario dependen SOLO de esta, no de `usuario_actual`:
    son las que sirven para tener usuario, así que no pueden exigirlo. Pero sí
    quedan detrás de la puerta — si no, con la app expuesta cualquiera de fuera
    podría darse de alta sin pasar por `ENABLE_AUTH`. Fuera de ella se quedan
    solo `/static` y `/salud`, que es lo que consulta el healthcheck de Docker
    y no lleva credenciales ni debe llevarlas.

    Se conserva el mecanismo del HTTP Basic con parseo manual y comparación en
    bytes: `secrets.compare_digest` sobre `str` revienta con cualquier tilde, y
    `fastapi.security.HTTPBasic` decodifica la cabecera en ASCII y rechaza una
    contraseña acentuada correcta antes de llegar a compararse."""
    if not settings.enable_auth:
        return

    import base64
    import binascii

    cabecera = request.headers.get("authorization")
    credenciales = None
    if cabecera:
        esquema, _, parametro = cabecera.partition(" ")
        if esquema.lower() == "basic" and parametro:
            try:
                usuario, separador, clave = base64.b64decode(parametro).decode("utf-8").partition(":")
                if separador:
                    credenciales = (usuario, clave)
            except (ValueError, UnicodeDecodeError, binascii.Error):
                credenciales = None

    if credenciales is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Autenticación requerida",
            headers={"WWW-Authenticate": "Basic"},
        )
    usuario, clave = credenciales
    ok_usuario = secrets.compare_digest(
        usuario.encode("utf-8"), settings.auth_username.encode("utf-8")
    )
    ok_clave = secrets.compare_digest(
        clave.encode("utf-8"), settings.auth_password.encode("utf-8")
    )
    if not (ok_usuario and ok_clave):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Basic"},
        )


def usuario_actual(request: Request, db: Session = Depends(get_db)) -> Usuario:
    """El usuario de la sesión. Redirige a elegir uno si no hay.

    La redirección va como excepción 303 y no como valor de retorno para que
    cualquier ruta la herede sin escribir nada: una vista nueva queda protegida
    por existir, no por acordarse.
    """
    acceso_permitido(request)

    usuario_id = sesion.leer(request)
    usuario = db.get(Usuario, usuario_id) if usuario_id is not None else None
    if usuario is None:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            detail="Elige quién eres",
            headers={"Location": "/entrar"},
        )

    # Desde aquí, TODA consulta de esta sesión queda acotada a este usuario.
    # Se fija en un solo sitio a propósito: ver app/multiusuario.py.
    from .multiusuario import fijar_usuario

    fijar_usuario(db, usuario.id)
    # Para la plantilla base, que enseña de quién es la sesión y ofrece salir.
    # Va en request.state y no en el contexto de cada vista: son 30 vistas y
    # ninguna tiene por qué acordarse de pasarlo.
    request.state.usuario = usuario
    return usuario
