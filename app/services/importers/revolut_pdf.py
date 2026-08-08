"""Parser del PDF de cuenta de inversión de Revolut (\"statement\" de acciones/ETFs).

El PDF extrae las tablas celda por celda: cada campo en su propia línea.
Las filas de trading tienen 9 columnas:
  Date | Symbol | Type | Quantity | Price | Side | Value | Fees | Commission
Las filas de efectivo (Cash top-up/withdrawal) tienen 5 columnas (se ignoran).

Solo importa compras/ventas (Buy/Sell).
"""
import re
from datetime import datetime

from . import ParsedRow, ParseResult

# Moneda de la sección: "USD Transactions" o "EUR Transactions"
SECTION_RE = re.compile(r"^(USD|EUR)\s+Transactions$", re.IGNORECASE)

# Línea de fecha: "06 Jul 2026 17:58:47 GMT"
DATE_RE = re.compile(
    r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{4})\s+\d{2}:\d{2}:\d{2}\s+GMT$",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Línea de precio/valor: "US$297.48", "€0.93", "US$0.02"
AMOUNT_RE = re.compile(r"^[A-Z]+\$[\d.,\-]+$|^€[\d.,\-]+$|^-?[A-Z]+\$[\d.,]+$|^-?€[\d.,]+$")


def _is_amount(line: str) -> bool:
    return bool(AMOUNT_RE.match(line))


def _is_cash_op(line: str) -> bool:
    """Detecta operaciones de efectivo: Cash top-up, Cash withdrawal."""
    return line.lower() in ("cash top-up", "cash withdrawal")


def _parse_amount(raw: str) -> float | None:
    cleaned = re.sub(r"[^\d.,\-]", "", raw)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


# Cabecera de la tabla: "Date" seguido de "Symbol", "Type", etc.
HEADER_COLS = ["Date", "Symbol", "Type", "Quantity", "Price", "Side", "Value", "Fees", "Commission"]
TABLE_COLS = len(HEADER_COLS)


def parse(text: str) -> ParseResult:
    result = ParseResult()
    lines = [linea.strip() for linea in text.split("\n") if linea.strip()]

    current_currency = "USD"
    in_table = False
    awaiting_header = False
    header_idx = 0
    idx = 0

    while idx < len(lines):
        line = lines[idx]

        # Detectar sección de transacciones
        sec = SECTION_RE.match(line)
        if sec:
            current_currency = sec.group(1).upper()
            awaiting_header = True
            in_table = False
            idx += 1
            continue

        # Esperando cabecera
        if awaiting_header:
            if line == HEADER_COLS[0]:
                header_idx = 1
                awaiting_header = False
            idx += 1
            continue

        # Leyendo cabecera
        if 1 <= header_idx < TABLE_COLS:
            if line == HEADER_COLS[header_idx]:
                header_idx += 1
                if header_idx == TABLE_COLS:
                    in_table = True
            else:
                header_idx = 0
            idx += 1
            continue

        if not in_table:
            idx += 1
            continue

        # Modo tabla: cada fila empieza con fecha
        if not DATE_RE.match(line):
            in_table = False
            idx += 1
            continue

        # Determinar tipo de fila leyendo la 3ª línea (Type)
        # Fila trading: Date, Symbol, Type, Qty, Price, Side, Value, Fees, Commission (9)
        # Fila efectivo: Date, Type, Amount, Fees, Commission (5)
        date_str = line

        # Necesito al menos 3 líneas más para decidir
        if idx + 3 >= len(lines):
            break

        # Mirar la 2ª línea: si es "Cash top-up" o "Cash withdrawal", es efectivo
        second = lines[idx + 1]

        if _is_cash_op(second):
            # Fila de efectivo: Date, Type, Amount, Fees, Commission (5 líneas)
            if idx + 5 > len(lines):
                break
            result.skip("efectivo")
            idx += 5
            continue

        if _is_amount(second):
            # También puede ser efectivo (sin tipo explícito)
            result.skip("efectivo")
            idx += 5  # asumimos 5 columnas
            continue

        # Fila de trading: 9 columnas
        if idx + TABLE_COLS > len(lines):
            break

        # Se nombran las nueve columnas aunque tres no se usen: el parseo es
        # posicional y con los nombres delante se comprueba de un vistazo que
        # los desplazamientos cuadran con el extracto. Sustituirlos por índices
        # sueltos haría el error de columna imposible de ver.
        symbol = second
        type_raw = lines[idx + 2]        # noqa: F841
        qty_str = lines[idx + 3]
        price_str = lines[idx + 4]
        side = lines[idx + 5]
        value_str = lines[idx + 6]       # noqa: F841
        fee_str = lines[idx + 7]
        comm_str = lines[idx + 8]        # noqa: F841

        idx += TABLE_COLS

        side_lower = side.strip().lower()
        if side_lower == "buy":
            op_type = "compra"
        elif side_lower == "sell":
            op_type = "venta"
        else:
            result.skip("tipo no soportado (%s)" % side)
            continue

        # Parsear fecha
        dm = DATE_RE.match(date_str)
        day = int(dm.group(1))
        month = MONTHS[dm.group(2).lower()[:3]]
        year = int(dm.group(3))
        row_date = datetime(year, month, day).date()

        # Cantidad
        qty = _parse_amount(qty_str)
        if not qty or qty <= 0:
            result.skip("cantidad no reconocida")
            continue
        qty = abs(qty)

        # Precio
        price = _parse_amount(price_str)
        if price is None or price < 0:
            result.skip("precio no reconocido")
            continue

        # Fee
        fee = _parse_amount(fee_str) or 0.0

        row = ParsedRow(
            type=op_type,
            kind="accion",
            ticker=symbol,
            name=symbol,
            date=row_date,
            quantity=qty,
            unit_price=price,
            fee=fee,
            currency=current_currency,
        )
        result.rows.append(row)

    if not result.rows and not result.skipped:
        result.error = "Ninguna transacción reconocida: ¿es un PDF de cuenta de Revolut?"

    return result
