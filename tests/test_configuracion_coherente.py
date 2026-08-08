"""[FT-M9] [FT-M17] La configuración que se documenta tiene que existir.

**FT-M17.** El bot decía «ponlo en FINANCE_TELEGRAM_CHAT_ID del .env», y esa
variable **no existe**: `config.py` no declara `env_prefix`, así que el nombre
real es `TELEGRAM_CHAT_ID`. Quien siguiera el mensaje al pie de la letra no
conseguiría nada y no tendría forma de saber por qué — y el mensaje interactivo
es justo el que se lee en el momento de configurar.

**FT-M9.** `secure=False` estaba fijado en el código, con un comentario que
decía «detrás de HTTPS conviene ponerlo a True»: para hacerlo había que editar
el fuente y reconstruir la imagen. Exactamente el tipo de cosa que nadie hace.
"""
import ast
import re
from pathlib import Path

import pytest

from app.config import Settings, settings

RAIZ = Path(__file__).resolve().parent.parent
APP = RAIZ / "app"

# Variables que lee docker-compose.yml, no la app: no son campos de Settings y
# no tienen por qué estarlo.
SOLO_COMPOSE = {"FINANCE_PORT", "FINANCE_BIND"}

# Palabras en mayúsculas que no son variables de entorno.
_RUIDO = re.compile(r"^(HTTP|HTTPS|HTML|JSON|CSV|PDF|SQL|SQLITE|UTC|CSRF|API|URL|ORM|WAL|ISIN)$")


def _posibles_variables(codigo: str) -> set[str]:
    """Nombres con pinta de variable de entorno que aparecen dentro de CADENAS.

    Se mira solo el contenido de los literales de texto —mensajes al usuario,
    docstrings, comentarios de configuración— y no el código: las constantes de
    módulo también van en mayúsculas, y no son variables de entorno."""
    arbol = ast.parse(codigo)
    encontradas: set[str] = set()
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
            encontradas.update(
                m for m in re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", nodo.value)
                if not _RUIDO.match(m)
            )
    return encontradas


def test_los_nombres_de_variables_en_los_mensajes_existen():
    """Barre app/ buscando lo que parezca una variable de entorno y comprueba
    que sea un campo real de Settings.

    Es un test de metaprogramación, pero barato y pilla la clase entera de
    error: cualquier mensaje que mande al usuario a una variable inventada."""
    campos = {nombre.upper() for nombre in Settings.model_fields}
    inventadas: dict[str, set[str]] = {}

    for fichero in APP.rglob("*.py"):
        encontradas = _posibles_variables(fichero.read_text(encoding="utf-8"))
        malas = {v for v in encontradas if v not in campos and v not in SOLO_COMPOSE}
        if malas:
            inventadas[fichero.name] = malas

    assert inventadas == {}, "variables que no existen: %s" % inventadas


def test_el_env_example_solo_documenta_variables_reales():
    campos = {nombre.upper() for nombre in Settings.model_fields}
    declaradas = {
        linea.split("=", 1)[0].strip()
        for linea in (RAIZ / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in linea and not linea.strip().startswith("#")
    }

    inventadas = declaradas - campos - SOLO_COMPOSE

    assert inventadas == set(), "el .env.example documenta lo que no existe: %s" % inventadas


# ---------- FT-M9: cookies Secure configurables ----------

@pytest.fixture
def cookies_seguras(monkeypatch):
    monkeypatch.setattr(settings, "cookies_seguras", True)


def _set_cookie_de(respuesta) -> list[str]:
    """Las cabeceras Set-Cookie, venga la respuesta de Starlette o de httpx."""
    cabeceras = respuesta.headers
    items = cabeceras.multi_items() if hasattr(cabeceras, "multi_items") else cabeceras.raw
    return [
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in items
        if (k.decode() if isinstance(k, bytes) else k).lower() == "set-cookie"
    ]


def test_cookie_csrf_respeta_la_configuracion(cookies_seguras):
    """Se llama a la función directamente y no por HTTP a propósito: activar
    `Secure` sobre HTTP hace que el cliente descarte la cookie, el token no
    vuelva y el POST se rechace con 403. Eso es lo correcto —y es justo por lo
    que el valor por defecto es `false`— pero impide probarlo de punta a punta
    sin montar TLS."""
    from fastapi.responses import Response

    from app.csrf import set_cookie

    respuesta = Response()
    set_cookie(respuesta, "un-token")

    assert "Secure" in _set_cookie_de(respuesta)[0]


def test_la_cookie_flash_tambien(cookies_seguras):
    from app.flash import redirect_flash

    flash = [c for c in _set_cookie_de(redirect_flash("/", "hola")) if c.startswith("flash=")]

    assert flash, "no se emitió la cookie flash"
    assert "Secure" in flash[0]


def test_por_defecto_no_van_marcadas(client):
    """Con HTTP en LAN, `Secure` haría que el navegador descartara la cookie —
    y sin cookie de CSRF no se puede enviar ningún formulario. De ahí el
    default, y de ahí que este test sí pueda ir contra la app real."""
    cabecera = client.get("/salud").headers.get("set-cookie", "")

    assert "csrftoken" in cabecera
    assert "Secure" not in cabecera


def test_activarlo_sin_tls_rompe_los_formularios(client, cookies_seguras):
    """Deja escrito el efecto, que no es obvio: si alguien pone
    COOKIES_SEGURAS=true sirviendo por HTTP, todo POST empieza a dar 403 y el
    motivo no aparece por ningún sitio."""
    respuesta = client.post_form("/categorias", data={"name": "Prueba", "keywords": ""},
                                 follow_redirects=False)

    assert respuesta.status_code == 403
