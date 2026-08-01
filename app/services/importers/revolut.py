"""Parser del CSV de cuenta de inversión de Revolut ("statement" de acciones/ETFs).

Columnas típicas: Date, Ticker, Type, Quantity, Price per share, Total Amount,
Currency, FX Rate. Tipos BUY */SELL * se importan; dividendos y movimientos de
efectivo se omiten. Para cripto en Revolut, exporta el CSV cripto y usa el
importador genérico (formato distinto según producto)."""
from . import ParsedRow, ParseResult, pick, read_csv, to_date, to_float

SKIP_WORDS = {
    "dividendo": ("dividend",),
    "movimiento de efectivo": ("top-up", "top up", "cash", "withdrawal", "deposit", "custody", "fee"),
}


def parse(text: str) -> ParseResult:
    result = ParseResult()
    rows = read_csv(text)
    if not rows:
        result.error = "El CSV está vacío o no tiene cabeceras reconocibles"
        return result

    for raw in rows:
        tipo_raw = pick(raw, "type", "tipo").lower()
        if not tipo_raw:
            result.skip("sin tipo")
            continue

        skipped = False
        for reason, words in SKIP_WORDS.items():
            if any(w in tipo_raw for w in words):
                result.skip(reason)
                skipped = True
                break
        if skipped:
            continue

        if "buy" in tipo_raw or "compra" in tipo_raw:
            op_type = "compra"
        elif "sell" in tipo_raw or "venta" in tipo_raw:
            op_type = "venta"
        else:
            result.skip('tipo no soportado ("%s")' % tipo_raw)
            continue

        row = ParsedRow(type=op_type, kind="accion")
        row.date = to_date(pick(raw, "date", "fecha", "completed date"))
        row.ticker = pick(raw, "ticker", "symbol", "simbolo") or None
        row.name = row.ticker or ""
        qty = to_float(pick(raw, "quantity", "cantidad", "shares"))
        row.quantity = abs(qty) if qty is not None else None  # el signo lo da el tipo, no la cantidad
        row.unit_price = to_float(pick(raw, "price per share", "precio por accion", "price", "precio"))
        currency = pick(raw, "currency", "divisa", "moneda").upper()

        if row.unit_price is None and row.quantity:
            total = to_float(pick(raw, "total amount", "importe total", "amount", "total"))
            if total is not None:
                row.unit_price = abs(total) / row.quantity

        if currency in ("EUR", "USD"):
            row.currency = currency
        elif currency:
            row.error = 'divisa "%s" no soportada (solo EUR/USD)' % currency
        else:
            row.currency = "USD"  # las acciones de Revolut suelen cotizar en USD

        if row.error is None:
            if not row.date:
                row.error = "fecha no reconocida"
            elif not row.quantity or row.quantity <= 0:
                row.error = "cantidad no reconocida"
            elif row.unit_price is None or row.unit_price < 0:
                row.error = "precio no reconocido"
        result.rows.append(row)

    if not result.rows and not result.skipped:
        result.error = "Ninguna fila reconocida: ¿seguro que es el CSV de inversión de Revolut?"
    return result
