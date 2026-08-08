"""Protección CSRF por double-submit cookie.

El problema: todas las mutaciones son POST de formulario y la autenticación es
HTTP Basic, que el navegador reenvía SOLO con las credenciales cacheadas. Una
página maliciosa abierta en otra pestaña podía hacer un POST a /activos/3/eliminar
y el navegador lo firmaba por su cuenta. La cookie `flash` lleva SameSite=Lax,
pero eso no protege nada aquí: la sesión no vive en una cookie.

La solución: en cada respuesta se emite una cookie `csrftoken` aleatoria. Toda
petición que modifique estado debe repetir ese mismo valor por un canal que un
sitio de terceros no controla:

  - los formularios, en un campo oculto `_csrf` (lo pone `csrf_input`);
  - las llamadas fetch, en la cabecera `X-CSRF-Token`.

Un atacante puede provocar que el navegador envíe la cookie, pero no puede
leerla (es HttpOnly) ni añadir cabeceras a un formulario cross-site, así que no
puede hacer que ambos valores coincidan.

El token se expone al JS propio en un <meta> de base.html, no relajando el
HttpOnly de la cookie: así un XSS futuro tampoco podría robarlo del almacén.
"""
import secrets

from fastapi import HTTPException, Request, status
from markupsafe import Markup

from .config import settings

COOKIE_NAME = "csrftoken"
FORM_FIELD = "_csrf"
HEADER_NAME = "X-CSRF-Token"

# Métodos que no cambian estado: no se validan (y no deberían cambiar nada).
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})

_TOKEN_BYTES = 32
# 12 h: más que una sesión de uso normal y menos que un día, así que un
# formulario abierto y olvidado desde ayer no se envía con un token caducado
# —que daría un 403 sin explicación— y a la vez el token no vive indefinidamente.
# Se renueva sola en la siguiente visita, así que en uso diario nunca expira.
_MAX_AGE = 60 * 60 * 12


def issue_token(request: Request) -> str:
    """Token de esta petición: el de la cookie si ya venía, o uno nuevo.
    Lo deja en request.state para que lo vean la plantilla y el middleware."""
    token = request.cookies.get(COOKIE_NAME) or secrets.token_urlsafe(_TOKEN_BYTES)
    request.state.csrf_token = token
    return token


def set_cookie(response, token: str) -> None:
    """Fija la cookie del token.

    `Secure` viene apagado por defecto a propósito: la app se sirve por HTTP en
    LAN o VPN y con `Secure` el navegador no guardaría la cookie — y sin cookie
    de CSRF no se puede enviar ningún formulario. Detrás de un proxy con TLS hay
    que encenderlo, y ahora se hace con `COOKIES_SEGURAS=true` en el `.env` en
    vez de editando este fichero y reconstruyendo la imagen."""
    response.set_cookie(
        COOKIE_NAME, token,
        max_age=_MAX_AGE, path="/", httponly=True, samesite="lax",
        secure=settings.cookies_seguras,
    )


async def verify_csrf(request: Request) -> None:
    """Dependencia global (se declara en la app, no router a router, para que
    cubra también las rutas que se añadan en el futuro)."""
    if request.method in SAFE_METHODS:
        return

    expected = request.cookies.get(COOKIE_NAME)
    submitted = request.headers.get(HEADER_NAME)
    if submitted is None:
        # Starlette cachea el formulario en la propia Request, así que leerlo
        # aquí no se lo quita al endpoint: lo reutiliza tal cual.
        form = await request.form()
        submitted = form.get(FORM_FIELD)

    if not expected or not submitted or not secrets.compare_digest(expected, str(submitted)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Petición rechazada por protección CSRF. Recarga la página e inténtalo de nuevo.",
        )


def csrf_input(request: Request) -> Markup:
    """Campo oculto para los formularios: `{{ csrf_input(request) }}`."""
    token = getattr(request.state, "csrf_token", "") or ""
    return Markup('<input type="hidden" name="%s" value="%s">') % (FORM_FIELD, token)
