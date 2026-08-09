"""Sesión de usuario en una cookie firmada, y hash de las contraseñas.

Sustituye al HTTP Basic. El motivo es que Basic no sabe hacer lo que aquí hace
falta: una contraseña **por usuario** y **opcional**. El navegador manda unas
credenciales o ninguna, y no hay forma de decirle "para esta persona no pidas
nada y para esta otra sí".

La cookie no lleva estado del servidor, solo el id firmado con HMAC-SHA256. No
hay tabla de sesiones que purgar ni memoria que se pierda al reiniciar, y una
cookie manipulada no valida: para falsificarla haría falta el secreto.

El secreto se genera solo la primera vez y vive en el volumen de datos, junto a
la base. Si estuviera en el `.env` habría que documentar un paso más en el
despliegue y una instalación sin él tendría un secreto por defecto, que es lo
mismo que no tener ninguno.

Las contraseñas van con PBKDF2-HMAC-SHA256 y sal por usuario, de la biblioteca
estándar: bcrypt o argon2 serían mejores, pero son una dependencia compilada
más en una app que presume de instalarse con `pip install -r requirements.txt`
y arrancar. Para el modelo de amenaza de esto —gente de casa, en la LAN— 480 000
iteraciones sobran.
"""
import base64
import contextlib
import hashlib
import hmac
import logging
import os
import secrets

from fastapi import Request, Response

from .config import settings

logger = logging.getLogger(__name__)

COOKIE = "sesion"
_MAX_AGE = 60 * 60 * 24 * 30  # 30 días: en casa nadie quiere volver a entrar cada día
_ITERACIONES = 480_000
_FICHERO_CLAVE = ".clave-sesion"


def _ruta_clave() -> str:
    return os.path.join(os.path.dirname(settings.db_path) or ".", _FICHERO_CLAVE)


def _clave() -> bytes:
    """Secreto de firma, generado la primera vez y persistido junto a la base.

    Tiene que sobrevivir a los reinicios: si cambiara en cada arranque, todo el
    mundo se encontraría la sesión caducada cada vez que se actualiza la app."""
    ruta = _ruta_clave()
    try:
        with open(ruta, "rb") as fichero:
            clave = fichero.read().strip()
        if clave:
            return clave
    except FileNotFoundError:
        pass

    clave = base64.urlsafe_b64encode(secrets.token_bytes(32))
    with open(ruta, "wb") as fichero:
        fichero.write(clave)
    with contextlib.suppress(OSError):
        os.chmod(ruta, 0o600)  # en Windows no hace nada, y tampoco hace falta
    logger.info("Clave de firma de sesiones generada en %s", ruta)
    return clave


# ---------- Contraseñas ----------

def hashear(password: str) -> str:
    """`iteraciones$sal$hash`, todo en hex. Formato propio y legible: lo que se
    guarda dice con qué parámetros se generó, así que subir las iteraciones más
    adelante no invalida las contraseñas ya puestas."""
    sal = secrets.token_bytes(16)
    derivado = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), sal, _ITERACIONES)
    return "%d$%s$%s" % (_ITERACIONES, sal.hex(), derivado.hex())


def comprobar(password: str, guardado: str | None) -> bool:
    """True si la contraseña casa. Sin contraseña guardada, cualquiera vale:
    ese usuario entra sin más, que es lo que significa no ponerla."""
    if not guardado:
        return True
    try:
        iteraciones, sal_hex, esperado_hex = guardado.split("$")
        derivado = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(sal_hex), int(iteraciones)
        )
    except (ValueError, TypeError):
        logger.warning("Hash de contraseña con formato ilegible; se rechaza")
        return False
    # compare_digest y no ==: comparar hashes con == filtra por el tiempo de
    # respuesta cuántos bytes coincidían.
    return hmac.compare_digest(derivado.hex(), esperado_hex)


# ---------- Cookie ----------

def _firmar(valor: str) -> str:
    firma = hmac.new(_clave(), valor.encode("utf-8"), hashlib.sha256).hexdigest()
    return "%s.%s" % (valor, firma)


def _verificar(firmado: str | None) -> str | None:
    if not firmado or "." not in firmado:
        return None
    valor, _, firma = firmado.rpartition(".")
    esperada = hmac.new(_clave(), valor.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(firma, esperada):
        return None
    return valor


def leer(request: Request) -> int | None:
    """Id del usuario de la sesión, o None si no hay cookie o está manipulada."""
    valor = _verificar(request.cookies.get(COOKIE))
    if valor is None:
        return None
    try:
        return int(valor)
    except ValueError:
        return None


def abrir(response: Response, usuario_id: int) -> None:
    response.set_cookie(
        COOKIE, _firmar(str(usuario_id)),
        max_age=_MAX_AGE, path="/", httponly=True, samesite="lax",
        secure=settings.cookies_seguras,
    )


def cerrar(response: Response) -> None:
    """Cierra la sesión vaciando la cookie y caducándola en el pasado.

    No se usa `delete_cookie`: emite la cabecera con una fecha de caducidad en
    el FUTURO, así que el cliente se queda la cookie y la sesión seguía abierta.
    Se comprobó mirando la respuesta real, no leyendo la firma de la función.
    Los atributos se repiten iguales que al abrirla, porque una cookie solo se
    sustituye si coinciden nombre, dominio y ruta."""
    response.set_cookie(
        COOKIE, "",
        max_age=0, expires="Thu, 01 Jan 1970 00:00:00 GMT",
        path="/", httponly=True, samesite="lax",
        secure=settings.cookies_seguras,
    )
