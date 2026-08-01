"""Parser del CSV de transacciones de Trade Republic.

Las columnas cambian según el idioma de la app (es/en/de); se aceptan sinónimos.
Identifica los activos por ISIN. Dividendos, depósitos e intereses se omiten.
Si no hay columna de precio, se deriva de importe/cantidad."""
from . import ParsedRow, ParseResult, _strip_accents, looks_like_isin, pick, read_csv, to_date, to_float

BUY_WORDS = ("buy", "compra", "kauf", "savings plan", "plan de inversion", "sparplan", "ejecucion")
SELL_WORDS = ("sell", "venta", "verkauf")
SKIP_WORDS = {
    "dividendo/interés": ("dividend", "dividendo", "interest", "interes", "zinsen"),
    "movimiento de efectivo": ("deposit", "deposito", "withdrawal", "retirada", "transfer",
                               "transferencia", "einzahlung", "auszahlung", "card", "tarjeta",
                               "reward", "recompensa", "tax", "impuesto"),
}


def parse(text: str) -> ParseResult:
    result = ParseResult()
    rows = read_csv(text)
    if not rows:
        result.error = "El CSV está vacío o no tiene cabeceras reconocibles"
        return result

    for raw in rows:
        tipo_raw = _strip_accents(pick(raw, "type", "tipo", "typ", "transaction type", "tipo de transaccion").lower())
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

        if any(w in tipo_raw for w in BUY_WORDS):
            op_type = "compra"
        elif any(w in tipo_raw for w in SELL_WORDS):
            op_type = "venta"
        else:
            result.skip('tipo no soportado ("%s")' % tipo_raw)
            continue

        row = ParsedRow(type=op_type, currency="EUR", kind="accion")
        row.date = to_date(pick(raw, "date", "fecha", "datum", "time", "hora"))
        # El identificador llega en la columna "isin" o, en algunos exports, en
        # "symbol": si tiene forma de ISIN se guarda como ISIN, si no como ticker.
        ident = pick(raw, "isin", "symbol", "simbolo", "wkn", "identifier")
        if ident:
            if looks_like_isin(ident):
                row.isin = ident.upper()
            else:
                row.ticker = ident.upper()
        row.name = pick(raw, "name", "nombre", "note", "nota", "description", "descripcion",
                        "instrument", "instrumento", "asset", "activo") or (row.isin or row.ticker or "")
        qty = to_float(pick(raw, "shares", "anzahl", "cantidad", "titulos", "quantity", "units"))
        row.quantity = abs(qty) if qty is not None else None  # TR exporta ventas con cantidad negativa; el signo lo da el tipo
        row.unit_price = to_float(pick(raw, "price", "precio", "preis", "kurs", "price per share",
                                       "precio por accion"))
        fee = to_float(pick(raw, "fee", "fees", "comision", "comisiones", "gebuhr", "gebuhren"))
        row.fee = abs(fee) if fee else 0.0

        # Sin columna de precio: derivarlo del importe total
        if row.unit_price is None and row.quantity:
            value = to_float(pick(raw, "value", "valor", "wert", "amount", "importe", "total"))
            if value is not None:
                row.unit_price = abs(value) / row.quantity

        if not row.date:
            row.error = "fecha no reconocida"
        elif not row.quantity or row.quantity <= 0:
            row.error = "cantidad no reconocida"
        elif row.unit_price is None or row.unit_price < 0:
            row.error = "precio no reconocido"
        result.rows.append(row)

    if not result.rows and not result.skipped:
        result.error = "Ninguna fila reconocida: ¿seguro que es el CSV de Trade Republic?"
    return result
