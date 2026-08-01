"""Parser del historial de trading de OKX (export CSV de "Trade history" / "Order history").

El instrumento viene como par (BTC-EUR, ETH/USDT...): la parte base identifica la
cripto (se mapea a id de CoinGecko si es conocida) y la cotizada debe ser EUR o
USD (USDT/USDC se tratan como USD). La comisión se asume en la divisa cotizada."""
import re

from . import CRYPTO_IDS, QUOTE_CURRENCIES, ParsedRow, ParseResult, pick, read_csv, to_date, to_float


def _split_instrument(raw: str) -> tuple[str, str] | None:
    parts = re.split(r"[-/_]", raw.strip().upper())
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


def parse(text: str) -> ParseResult:
    result = ParseResult()
    rows = read_csv(text)
    if not rows:
        result.error = "El CSV está vacío o no tiene cabeceras reconocibles"
        return result

    for raw in rows:
        side = pick(raw, "side", "direction", "lado", "tipo", "order type").lower()
        if not side:
            result.skip("sin lado (buy/sell)")
            continue
        if "buy" in side or "compra" in side:
            op_type = "compra"
        elif "sell" in side or "venta" in side:
            op_type = "venta"
        else:
            result.skip('lado no soportado ("%s")' % side)
            continue

        instrument = pick(raw, "instrument", "instrumento", "symbol", "simbolo", "pair", "par",
                          "market", "mercado", "underlying asset", "instrument id")
        split = _split_instrument(instrument)
        if not split:
            result.skip("sin par de trading")
            continue
        base, quote = split

        row = ParsedRow(type=op_type, kind="cripto")
        row.name = base
        row.ticker = CRYPTO_IDS.get(base)
        row.date = to_date(pick(raw, "time", "order time", "filled time", "fill time", "date",
                                "fecha", "hora", "created time", "trade time"))
        qty = to_float(pick(raw, "amount", "filled amount", "size", "quantity", "cantidad",
                             "filled qty", "exec qty", "volume"))
        row.quantity = abs(qty) if qty is not None else None  # el signo lo da el lado (buy/sell), no la cantidad
        row.unit_price = to_float(pick(raw, "avg price", "filled price", "price", "precio",
                                       "avg fill price", "exec price"))
        fee = to_float(pick(raw, "fee", "fees", "comision", "trading fee"))
        row.fee = abs(fee) if fee else 0.0

        currency = QUOTE_CURRENCIES.get(quote)
        if currency is None:
            row.error = 'par cotizado en "%s" no soportado (solo EUR/USD/USDT/USDC)' % quote
        else:
            row.currency = currency

        if row.error is None:
            if not row.date:
                row.error = "fecha no reconocida"
            elif not row.quantity or row.quantity <= 0:
                row.error = "cantidad no reconocida"
            elif row.unit_price is None or row.unit_price < 0:
                row.error = "precio no reconocido"
        result.rows.append(row)

    if not result.rows and not result.skipped:
        result.error = "Ninguna fila reconocida: ¿seguro que es el historial de trading de OKX?"
    return result
