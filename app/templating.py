"""Instancia compartida de Jinja2Templates con filtros personalizados."""
import json
import time
import zlib
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.templating import Jinja2Templates
from jinja2.utils import htmlsafe_json_dumps

from .csrf import csrf_input

# Ruta absoluta derivada del propio módulo, no "app/templates": una ruta
# relativa al cwd solo funciona si el proceso arranca desde la raíz del repo.
# En Docker el WORKDIR /app lo salva, pero rompe cualquier otro modo de
# arranque (uvicorn desde otra carpeta, un systemd unit sin WorkingDirectory).
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _json_default(value):
    """Serializa lo que json no sabe. Los importes son Decimal y tienen que salir
    como NÚMERO: convertidos a str, Chart.js recibiría "42.50" y no lo pintaría."""
    if isinstance(value, Decimal):
        return float(value)
    return str(value)  # fechas, datetimes, enums...


def _tojson(value):
    """`tojson` que serializa fechas (default=str) SIN perder el escapado de Jinja.

    El filtro nativo no sabe serializar date/datetime (las series de precios van
    llenas), pero un json.dumps a secas deja pasar "</script>" y permite romper
    el bloque <script> desde cualquier dato guardado (nombre de categoría,
    región/sector de un activo, sector crudo devuelto por Yahoo).
    htmlsafe_json_dumps escapa <>&' como \\u003c... y devuelve Markup, así que el
    resultado ya es seguro de incrustar y el `| safe` de las plantillas sobra."""
    return htmlsafe_json_dumps(value, dumps=lambda v, **kw: json.dumps(v, default=_json_default, **kw))


templates.env.filters["tojson"] = _tojson

# Campo oculto con el token CSRF, para usar dentro de cada <form method="post">
templates.env.globals["csrf_input"] = csrf_input

# Cache-busting de assets estáticos: cambia en cada arranque (cada rebuild reinicia
# el proceso) para que el navegador no sirva CSS/JS viejos tras actualizar la app.
templates.env.globals["asset_v"] = int(time.time())


def hoy_iso() -> str:
    """Fecha de hoy para los formularios de base.html (el botón flotante).

    Es una función y no un valor: como global evaluado al importar se quedaría
    congelada en el día en que arrancó el contenedor, y el gasto rápido acabaría
    apuntándose con fecha vieja. Las vistas que ya reciben `hoy` de su router
    siguen usando el suyo."""
    return date.today().isoformat()


templates.env.globals["hoy_iso"] = hoy_iso


# Palabras que no distinguen a un activo de otro: casi todos los fondos las
# llevan, así que gastar en ellas una de las dos letras del avatar sería tirarla.
_RELLENO = {
    "inc", "inc.", "corp", "corp.", "corporation", "company", "co", "co.",
    "sa", "s.a.", "plc", "ltd", "ltd.", "ag", "nv", "se", "spa",
    "etf", "fund", "index", "acc", "dist", "class", "ucits",
    "usd", "eur", "gbp", "chf", "p", "a", "b", "c",
}


def _palabras_utiles(nombre: str) -> list[str]:
    """Palabras del nombre que sirven para distinguirlo, en orden."""
    utiles = []
    for palabra in nombre.split():
        limpia = palabra.strip("()[]{},.·-–—").strip()
        if not limpia or not limpia[0].isalnum():
            continue  # "(A)" y compañía: no aportan inicial
        if limpia.lower() in _RELLENO:
            continue
        utiles.append(limpia)
    return utiles


def iniciales(nombre: str | None, respaldo: str | None = None) -> str:
    """Dos letras para el avatar de un activo.

    Con varias palabras útiles se toma la inicial de las dos primeras
    ("MUESTRA Corporation" -> NV, porque "Corporation" es relleno); con una sola,
    sus dos primeros caracteres ("OKX" -> OK)."""
    for candidato in (nombre, respaldo):
        palabras = _palabras_utiles(candidato or "")
        if len(palabras) >= 2:
            return (palabras[0][0] + palabras[1][0]).upper()
        if palabras:
            return palabras[0][:2].upper()
    return "??"


def color_activo(clave: str | None) -> str:
    """Color estable a partir del ticker (o el nombre): mismo activo, mismo color
    en toda la app y entre sesiones.

    Se usa crc32 y no hash(): el hash de Python lleva sal por proceso, así que
    los colores cambiarían en cada reinicio del contenedor."""
    semilla = (clave or "?").strip().upper().encode("utf-8")
    return "hsl(%d, 55%%, 45%%)" % (zlib.crc32(semilla) % 360)


templates.env.globals["iniciales"] = iniciales
templates.env.globals["color_activo"] = color_activo


_MESES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


def mes_anio(fecha) -> str:
    """"Marzo 2026", para los separadores del historial.

    Los nombres van en una tabla propia y no por strftime("%B"): el contenedor
    corre en locale C y devolvería "March" en una app que está toda en español."""
    return "%s %d" % (_MESES[fecha.month - 1], fecha.year)


templates.env.globals["mes_anio"] = mes_anio


def dinero(value) -> str:
    """Formato monetario español: 1234567.5 -> '1.234.567,50'."""
    if value is None:
        return "-"
    formatted = f"{float(value):,.2f}"
    # Python formatea a la inglesa (1,234,567.50) y aquí se escribe al revés.
    # El rodeo por "X" es para no pisarse: cambiar "," por "." primero dejaría
    # los separadores de millar convertidos en decimales. Se hace a mano y no
    # con `locale` porque el contenedor corre en locale C y no tiene los datos
    # de es_ES instalados; y no con Babel porque sería una dependencia entera
    # para dos líneas.
    return formatted.replace(",", "X").replace(".", ",").replace("X", ".")


templates.env.filters["dinero"] = dinero
