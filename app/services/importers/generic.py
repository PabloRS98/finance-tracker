"""Parser del CSV genérico de la propia app.

Cabeceras esperadas (es o en, en cualquier orden):
fecha,tipo,nombre,ticker,isin,cantidad,precio,comision,divisa,clase

- tipo: compra | venta
- ticker: símbolo Yahoo (acciones) o id CoinGecko (cripto), opcional
- divisa: EUR | USD (por defecto EUR)
- clase: accion | cripto (por defecto accion)

Sirve de puente para brokers aún no soportados (MyInvestor, XTB, Degiro, IBKR...):
exporta desde el broker, renombra columnas y listo."""
from ...models import CURRENCY_CODES
from . import ParsedRow, ParseResult, pick, read_csv, to_date, to_float


def parse(text: str) -> ParseResult:
    result = ParseResult()
    rows = read_csv(text)
    if not rows:
        result.error = "El CSV está vacío o no tiene cabeceras reconocibles"
        return result

    for raw in rows:
        tipo = pick(raw, "tipo", "type").lower().strip()
        if tipo in ("compra", "buy"):
            op_type = "compra"
        elif tipo in ("venta", "sell"):
            op_type = "venta"
        else:
            result.skip('tipo no reconocido ("%s")' % (tipo or "vacío"))
            continue

        row = ParsedRow(type=op_type)
        row.date = to_date(pick(raw, "fecha", "date"))
        row.name = pick(raw, "nombre", "name")
        row.ticker = pick(raw, "ticker", "simbolo", "symbol") or None
        row.isin = pick(raw, "isin") or None
        qty = to_float(pick(raw, "cantidad", "quantity"))
        row.quantity = abs(qty) if qty is not None else None  # el signo lo da el tipo, no la cantidad
        row.unit_price = to_float(pick(raw, "precio", "price"))
        fee = to_float(pick(raw, "comision", "fee"))
        row.fee = abs(fee) if fee else 0.0

        currency = (pick(raw, "divisa", "moneda", "currency") or "EUR").upper()
        if currency in CURRENCY_CODES:
            row.currency = currency
        else:
            row.error = 'divisa "%s" no soportada' % currency

        kind = (pick(raw, "clase", "kind", "asset class") or "accion").lower()
        row.kind = "cripto" if "cripto" in kind or "crypto" in kind else "accion"

        if not row.name:
            row.name = row.ticker or row.isin or ""

        if row.error is None:
            if not row.date:
                row.error = "fecha no reconocida"
            elif not row.name:
                row.error = "sin nombre ni ticker"
            elif not row.quantity or row.quantity <= 0:
                row.error = "cantidad no reconocida"
            elif row.unit_price is None or row.unit_price < 0:
                row.error = "precio no reconocido"
        result.rows.append(row)

    if not result.rows and not result.skipped:
        result.error = "Ninguna fila reconocida: revisa que las cabeceras sigan la plantilla"
    return result
