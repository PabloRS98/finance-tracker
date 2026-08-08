"""Interpreta texto transcrito por voz (ej. "gasté 20 euros en comida el lunes pasado")
y lo convierte en una transacción PENDIENTE de confirmar. También reconoce
operaciones de inversión ("compré 0,1 ethereum a 2.800") y las convierte en
operaciones pendientes.

Basado en reglas (regex + keywords), sin dependencias externas ni LLM: 100% local.
Fechas soportadas: hoy, ayer, anteayer, anoche, "hace N días/semanas/un mes",
"el lunes pasado" (cualquier día de la semana), "el día 3", "el 3 de mayo (de 2026)".
Si no se detecta un importe, amount es None y el endpoint avisa sin crear nada.
"""
import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..models import Asset, AssetType, Category

INGRESO_VERBS = [
    "cobré", "cobre", "ingresé", "ingrese", "recibí", "recibi",
    "me pagaron", "me ingresaron", "me han pagado", "me han ingresado",
]

WEEKDAYS = {
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3,
    "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
}

MONTHS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

NUM_WORDS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "veinte": 20, "treinta": 30,
}

_WEEKDAY_ALT = "|".join(WEEKDAYS)
_MONTH_ALT = "|".join(MONTHS)
_NUMWORD_ALT = "|".join(NUM_WORDS)

# Patrones de fecha, por orden de prioridad
HACE_RE = re.compile(rf"\bhace\s+(\d+|{_NUMWORD_ALT})\s+(d[ií]as?|semanas?|mes(?:es)?)\b", re.IGNORECASE)
EXPLICIT_RE = re.compile(
    rf"\bel\s+(?:d[ií]a\s+)?(\d{{1,2}})(?:\s+de\s+({_MONTH_ALT}))?(?:\s+(?:de|del)\s+(\d{{4}}))?\b",
    re.IGNORECASE,
)
WEEKDAY_RE = re.compile(
    rf"\b(?:el|la)\s+(?:(pasado|pasada)\s+)?({_WEEKDAY_ALT})(?:\s+(pasado|pasada))?\b", re.IGNORECASE
)

# Importes: primero con moneda explícita ("20 euros", "15,50 €", "20 euros con 50"),
# después números con decimales, y por último enteros sueltos.
AMOUNT_CURRENCY_RE = re.compile(
    r"(\d+(?:[.,]\d{1,2})?)\s*(euros?|€|eur\b|d[oó]lares?|\$|usd\b)(?:\s+con\s+(\d{1,2}))?",
    re.IGNORECASE,
)
AMOUNT_CON_RE = re.compile(r"(\d+)\s+con\s+(\d{1,2})\b", re.IGNORECASE)
AMOUNT_DECIMAL_RE = re.compile(r"\d+[.,]\d{1,2}")
AMOUNT_INT_RE = re.compile(r"\d+")

_LEAP_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
_NORMAL_DAYS = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]


def _days_in(year: int, month: int) -> int:
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    return (_LEAP_DAYS if leap else _NORMAL_DAYS)[month - 1]


def _num(raw: str) -> int:
    raw = raw.lower().strip()
    return int(raw) if raw.isdigit() else NUM_WORDS.get(raw, 1)


def parse_date(text: str) -> tuple[date, str | None]:
    """Devuelve (fecha, fragmento de texto que la expresaba) para poder excluirlo
    después al buscar el importe. Si no hay expresión de fecha: (hoy, None)."""
    lowered = text.lower()
    today = date.today()

    m = HACE_RE.search(lowered)
    if m:
        n, unit = _num(m.group(1)), m.group(2)
        if unit.startswith("semana"):
            return today - timedelta(weeks=n), m.group(0)
        if unit.startswith("mes"):
            month, year = today.month - n, today.year
            while month <= 0:
                month += 12
                year -= 1
            return date(year, month, min(today.day, _days_in(year, month))), m.group(0)
        return today - timedelta(days=n), m.group(0)

    m = EXPLICIT_RE.search(lowered)
    if m and (m.group(2) or "día" in m.group(0) or "dia" in m.group(0)):
        # Acepta "el 3 de mayo" o "el día 3"; un "el 3" a secas se ignora
        # para no confundirlo con un importe.
        day = int(m.group(1))
        month = MONTHS[m.group(2).lower()] if m.group(2) else today.month
        year = int(m.group(3)) if m.group(3) else today.year
        try:
            result = date(year, month, day)
        except ValueError:
            return today, None
        # Sin año explícito, una fecha futura se interpreta como del año anterior
        if not m.group(3) and result > today:
            result = date(year - 1, month, day)
        return result, m.group(0)

    m = WEEKDAY_RE.search(lowered)
    if m:
        target = WEEKDAYS[m.group(2).lower()]
        delta = (today.weekday() - target) % 7
        if delta == 0:
            delta = 7  # "el lunes (pasado)" dicho en lunes = hace una semana
        return today - timedelta(days=delta), m.group(0)

    if "anteayer" in lowered or "antes de ayer" in lowered:
        return today - timedelta(days=2), "anteayer"
    if "anoche" in lowered:
        return today - timedelta(days=1), "anoche"
    if "ayer" in lowered:
        return today - timedelta(days=1), "ayer"
    return today, None


def parse_amount(text: str, exclude: str | None = None) -> tuple[float | None, str, str]:
    """Extrae el importe, la moneda detectada y la confianza.

    Devuelve `(None, "EUR", "baja")` si no hay ningún número utilizable.
    `exclude` es el fragmento de fecha ya reconocido, que se elimina antes para
    no confundir "el 3 de mayo" con 3 euros.

    La confianza es "baja" cuando el importe salió del último recurso —el
    primer entero suelto de la frase, sin moneda ni decimales—. Ese fallback no
    se quita porque recupera casos legítimos ("3 cafés"), pero acierta por
    casualidad tanto como falla: «gasté en el súper de la calle 5» da 5 €. El
    daño está acotado porque todo entra como PENDIENTE, pero el mensaje de
    confirmación decía "💸 Gasto 5,00 EUR" con toda seguridad, y confirmar es un
    solo toque.
    """
    cleaned = text.lower()
    if exclude:
        cleaned = cleaned.replace(exclude.lower(), " ")

    m = AMOUNT_CURRENCY_RE.search(cleaned)
    if m:
        amount = float(m.group(1).replace(",", "."))
        if m.group(3):  # "20 euros con 50"
            amount = float(int(float(m.group(1)))) + int(m.group(3)) / 100
        currency = "USD" if re.search(r"d[oó]lar|\$|usd", m.group(2), re.IGNORECASE) else "EUR"
        return amount, currency, "alta"

    m = AMOUNT_CON_RE.search(cleaned)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 100, "EUR", "alta"

    m = AMOUNT_DECIMAL_RE.search(cleaned)
    if m:
        # Con decimales explícitos ("12,50") nadie está diciendo una fecha ni un
        # número de portal: es un importe.
        return float(m.group(0).replace(",", ".")), "EUR", "alta"

    m = AMOUNT_INT_RE.search(cleaned)
    if m:
        # Último recurso: el primer entero suelto. Aquí es donde se cuela
        # "la calle 5" como 5 euros.
        return float(m.group(0)), "EUR", "baja"

    return None, "EUR", "baja"


def parse_type(text: str) -> str:
    lowered = text.lower()
    if any(v in lowered for v in INGRESO_VERBS):
        return "ingreso"
    return "gasto"  # por defecto asumimos gasto: es el caso más frecuente


def guess_category(text: str, db: Session) -> Category | None:
    """Busca una categoría cuyo nombre o palabras clave aparezcan en el texto.
    Compara palabras completas: "gas" no debe casar con "gasté"."""
    lowered = text.lower()

    def word_in(needle: str) -> bool:
        return re.search(r"\b%s\b" % re.escape(needle), lowered) is not None

    for cat in db.query(Category).all():
        keywords = [k.strip().lower() for k in cat.keywords.split(",") if k.strip()]
        if any(word_in(k) for k in keywords) or word_in(cat.name.lower()):
            return cat
    return None


COMPRA_VERBS = ["compré", "compre", "he comprado", "compra de", "comprado"]
VENTA_VERBS = ["vendí", "vendi", "he vendido", "venta de", "vendido"]

# Precio unitario: "a 2.800", "por 2800", "a 2800 euros/dólares"
PRICE_RE = re.compile(r"\b(?:a|por)\s+(\d+(?:[.,]\d+)?)", re.IGNORECASE)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def _to_number(raw: str) -> float:
    """'2.800,50' -> 2800.5 · '0,1' -> 0.1 · '2800' -> 2800.0"""
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif raw.count(".") == 1 and len(raw.split(".")[1]) == 3:
        raw = raw.replace(".", "")  # "2.800" es separador de miles, no decimal
    return float(raw)


def _match_asset(lowered: str, db: Session) -> Asset | None:
    """Activo invertible cuyo nombre o ticker aparece en el texto (el más largo gana)."""
    best: Asset | None = None
    best_len = 0
    invertible = db.query(Asset).filter(Asset.asset_type.in_((AssetType.ACCION, AssetType.CRIPTO))).all()
    for asset in invertible:
        for needle in (asset.name.lower(), (asset.ticker or "").lower()):
            if needle and needle in lowered and len(needle) > best_len:
                best, best_len = asset, len(needle)
    return best


def parse_voice_operation(text: str, db: Session) -> dict | None:
    """Si el texto expresa una compra/venta de inversión devuelve
    {type, asset, date, quantity, unit_price, error}; si no, None (y el llamador
    lo tratará como gasto/ingreso normal)."""
    lowered = text.lower()
    if any(v in lowered for v in COMPRA_VERBS):
        op_type = "compra"
    elif any(v in lowered for v in VENTA_VERBS):
        op_type = "venta"
    else:
        return None

    # Solo es una operación si además menciona un activo invertible existente
    asset = _match_asset(lowered, db)
    if asset is None:
        return None

    op_date, date_fragment = parse_date(text)
    cleaned = lowered.replace(date_fragment.lower(), " ") if date_fragment else lowered

    error = None
    price_match = PRICE_RE.search(cleaned)
    unit_price = _to_number(price_match.group(1)) if price_match else None

    # Cantidad: primer número que no sea el precio
    quantity = None
    for m in NUMBER_RE.finditer(cleaned):
        if price_match and m.start() >= price_match.start(1) and m.end() <= price_match.end(1):
            continue
        quantity = _to_number(m.group(0))
        break

    if quantity is None or quantity <= 0:
        error = 'No he entendido la cantidad. Di por ejemplo: "compré 0,5 %s a 100".' % asset.name.lower()
    elif unit_price is None:
        error = 'No he entendido el precio. Añade "a <precio>", ej.: "%s 2 %s a 150".' % (
            "compré" if op_type == "compra" else "vendí", asset.name.lower())

    return {
        "type": op_type,
        "asset": asset,
        "date": op_date,
        "quantity": quantity,
        "unit_price": unit_price,
        "error": error,
    }


def parse_voice_text(text: str, db: Session) -> dict:
    """Devuelve un dict listo para crear una Transaction pendiente a partir de texto libre.
    Si amount es None, el llamador debe avisar al usuario y no crear nada."""
    tx_date, date_fragment = parse_date(text)
    amount, currency, confianza = parse_amount(text, exclude=date_fragment)
    category = guess_category(text, db)
    return {
        "amount": amount,
        "currency": currency,
        # "baja" = el importe salió del último patrón, sin moneda ni decimales.
        # Quien pinte la confirmación tiene que decirlo: confirmar es un toque.
        "confianza": confianza,
        "type": parse_type(text),
        "date": tx_date,
        "date_detected": date_fragment is not None,
        "category_id": category.id if category else None,
        "category_name": category.name if category else None,
        "description": text.strip(),
    }
