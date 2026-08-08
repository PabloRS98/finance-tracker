"""Obtención de tipos de cambio y precios de mercado (acciones, ETFs, criptomonedas).

APIs gratuitas, sin necesidad de API key:
- Frankfurter para tipos de cambio (datos del Banco Central Europeo).
- API de gráficas de Yahoo Finance para acciones/ETFs/fondos (directa, sin yfinance:
  el flujo de cookies de la librería depende de fc.yahoo.com, que rechaza conexiones).
- CoinGecko API pública para criptomonedas.
"""
import logging
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote

import httpx

from . import errores_api

logger = logging.getLogger(__name__)

_CENT = Decimal("0.01")

FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
COINGECKO_MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/%s/market_chart"
COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"
COINGECKO_COIN_URL = "https://api.coingecko.com/api/v3/coins/%s"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%s"
YAHOO_SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
YAHOO_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _yahoo_search(query: str, count: int = 6) -> list[dict]:
    """Búsqueda pública de Yahoo (sin crumb ni key). Devuelve los quotes crudos de
    tipo acción/ETF/fondo; lista vacía si falla la llamada."""
    try:
        resp = httpx.get(
            YAHOO_SEARCH_URL,
            params={"q": query, "quotesCount": count, "newsCount": 0},
            headers=YAHOO_HEADERS, timeout=15, follow_redirects=True,
        )
        resp.raise_for_status()
        return [
            q for q in resp.json().get("quotes", [])
            if q.get("symbol") and q.get("quoteType") in ("EQUITY", "ETF", "MUTUALFUND")
        ]
    except Exception:
        logger.exception("Fallo en la búsqueda Yahoo de %r", query)
        return []


def resolve_ticker_by_isin(isin: str) -> list[str]:
    """Símbolos Yahoo candidatos para un ISIN, por orden de relevancia. Un mismo ISIN
    puede cotizar en varias plazas/divisas: quien llame elige según la divisa."""
    return [q["symbol"] for q in _yahoo_search(isin)]


def search_symbols(query: str, limit: int = 8) -> list[dict]:
    """Autocompletado de tickers para el alta de activos: [{value, name, extra}]."""
    return [
        {
            "value": q["symbol"],
            "name": q.get("longname") or q.get("shortname") or q["symbol"],
            "extra": " · ".join(x for x in (q.get("exchDisp"), q.get("typeDisp")) if x),
        }
        for q in _yahoo_search(query, limit)
    ]


def search_crypto(query: str, limit: int = 8) -> list[dict]:
    """Autocompletado de ids de CoinGecko: [{value, name, extra}]."""
    try:
        resp = httpx.get(COINGECKO_SEARCH_URL, params={"query": query}, timeout=10)
        resp.raise_for_status()
        return [
            {"value": c["id"], "name": c.get("name") or c["id"], "extra": (c.get("symbol") or "").upper()}
            for c in resp.json().get("coins", [])[:limit]
        ]
    except Exception:
        logger.exception("Fallo en la búsqueda CoinGecko de %r", query)
        return []


def get_crypto_name(coingecko_id: str) -> str | None:
    """Nombre legible de una cripto ('bitcoin' -> 'Bitcoin'). Solo se llama cuando
    hace falta renombrar (no en cada refresco de precios)."""
    try:
        resp = httpx.get(
            COINGECKO_COIN_URL % coingecko_id,
            params={"localization": "false", "tickers": "false", "market_data": "false",
                    "community_data": "false", "developer_data": "false"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get("name") or None
    except Exception:
        logger.exception("Fallo al obtener nombre de cripto %s", coingecko_id)
        return None


def name_is_placeholder(asset) -> bool:
    """True si el nombre del activo es el propio ticker (o está vacío): el caso de
    los CSV de Revolut, que solo traen el símbolo (MSFT · MSFT)."""
    name = (asset.name or "").strip().upper()
    return not name or name == (asset.ticker or "").strip().upper()


def normalize_quote_currency(price: float | None, currency: str | None) -> tuple[float | None, str]:
    """Normaliza la divisa de una cotización de Yahoo. Londres cotiza en peniques
    ("GBp"/"GBX", 1/100 de libra): se escala el precio y se devuelve "GBP"."""
    code = (currency or "USD").strip()
    if code in ("GBp", "GBX", "gbx"):  # peniques; "GBP" a secas ya son libras
        return (price / 100.0 if price is not None else None), "GBP"
    return price, code.upper()


def yahoo_chart(ticker: str, params: dict) -> dict:
    """Llama al endpoint de gráficas de Yahoo y devuelve el primer resultado."""
    resp = httpx.get(
        YAHOO_CHART_URL % quote(ticker), params=params,
        headers=YAHOO_HEADERS, timeout=15, follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.json()["chart"]["result"][0]

def parse_yahoo_intraday(result: dict) -> list[tuple[datetime, float]]:
    """(timestamp UTC, precio) del chart intradía de Yahoo, sin huecos None y
    con los peniques de Londres escalados a libras. Separada para poder testearla."""
    timestamps = result.get("timestamp") or []
    closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
    quote_currency = (result.get("meta") or {}).get("currency")
    _, norm = normalize_quote_currency(1.0, quote_currency)
    scale = 0.01 if (quote_currency and norm == "GBP" and quote_currency != "GBP") else 1.0
    return [
        (datetime.fromtimestamp(ts, tz=UTC), float(close) * scale)
        # strict=False: Yahoo devuelve a veces un cierre menos que marcas de
        # tiempo, y lo que sobra es la vela en curso. Cortar por la más corta es
        # lo correcto aquí; con strict=True esto lanzaría.
        for ts, close in zip(timestamps, closes, strict=False)
        if close is not None
    ]


def get_stock_intraday(ticker: str) -> list[tuple[datetime, float]]:
    """Curva intradía (5 min) del día de mercado actual; [] si falla o no cotiza intradía."""
    try:
        return parse_yahoo_intraday(yahoo_chart(ticker, {"range": "1d", "interval": "5m"}))
    except Exception:
        logger.exception("Fallo al obtener intradía de %s", ticker)
        return []


def get_crypto_intraday(coingecko_id: str, vs_currency: str) -> list[tuple[datetime, float]]:
    """Curva de las últimas 24 h desde CoinGecko (granularidad ~5 min); [] si falla."""
    try:
        resp = httpx.get(
            COINGECKO_MARKET_CHART_URL % quote(coingecko_id),
            params={"vs_currency": vs_currency.lower(), "days": 1},
            timeout=20,
        )
        resp.raise_for_status()
        return [
            (datetime.fromtimestamp(ts_ms / 1000, tz=UTC), float(price))
            for ts_ms, price in resp.json().get("prices", [])
        ]
    except Exception:
        logger.exception("Fallo al obtener intradía de cripto %s", coingecko_id)
        return []


_FX_CACHE_TTL_SECONDS = 3600
_fx_cache: dict[tuple[str, str], tuple[float, datetime]] = {}


def get_exchange_rate(from_currency: str, to_currency: str) -> float | None:
    """Tipo de cambio from_currency -> to_currency, con caché de 1 hora.

    Devuelve None si la API falla y no hay ningún valor cacheado al que caer.
    NO devuelve 1.0 como último recurso: en un arranque en frío sin red eso
    valoraría 1 USD = 1 EUR, y el snapshot diario llegaría a persistir ese
    número en el histórico. Quien llama debe decidir explícitamente qué hacer
    con la ausencia de tipo (excluir el activo, no escribir el snapshot...)."""
    if from_currency == to_currency:
        return 1.0

    key = (from_currency, to_currency)
    cached = _fx_cache.get(key)
    if cached and (datetime.now(UTC) - cached[1]).total_seconds() < _FX_CACHE_TTL_SECONDS:
        return cached[0]

    try:
        resp = httpx.get(
            FRANKFURTER_URL, params={"from": from_currency, "to": to_currency},
            timeout=10, follow_redirects=True,
        )
        resp.raise_for_status()
        rate = float(resp.json()["rates"][to_currency])
        _fx_cache[key] = (rate, datetime.now(UTC))
        return rate
    except Exception:
        logger.exception("Fallo al obtener tipo de cambio %s->%s", from_currency, to_currency)
        # Caché caducada pero utilizable: mejor un tipo de ayer que ninguno
        return cached[0] if cached else None


def to_base(amount: Decimal | float, currency: str, base: str) -> Decimal | None:
    """Convierte `amount` de `currency` a `base`, cuadrado a céntimos.

    Devuelve Decimal porque el resultado acaba en un importe del libro. El tipo
    de cambio llega como float (viene de una API), así que se pasa a Decimal por
    su representación decimal —Decimal(str(x)), no Decimal(x)— para no arrastrar
    el ruido binario del float al importe final.

    None si no hay tipo de cambio: quien llama debe negarse a guardar el importe
    en vez de apuntarlo sin convertir (un gasto de 20 USD no son 20 EUR)."""
    value = Decimal(str(amount))
    if currency == base:
        return value.quantize(_CENT, rounding=ROUND_HALF_UP)
    rate = get_exchange_rate(currency, base)
    if rate is None:
        return None
    return (value * Decimal(str(rate))).quantize(_CENT, rounding=ROUND_HALF_UP)


# Región aproximada según el exchange donde cotiza (para acciones individuales;
# en ETFs el exchange no dice nada de la exposición real, ahí clasifica el usuario)
EXCHANGE_REGIONS = {
    "EE. UU.": {"NMS", "NYQ", "NGM", "NCM", "NAS", "PCX", "ASE", "BTS"},
    "Europa": {"GER", "FRA", "BER", "AMS", "PAR", "MIL", "MCE", "EBS", "LSE", "IOB",
               "LIS", "BRU", "VIE", "STO", "CPH", "HEL", "OSL", "ISE", "WSE", "ZRH", "VTX"},
    "Japón": {"TYO", "JPX", "OSA"},
    "China": {"HKG", "SHH", "SHZ"},
    "Asia": {"KSC", "KOE", "TAI", "BSE", "NSI", "SES"},
}


def region_for_exchange(exchange: str | None) -> str | None:
    for region, exchanges in EXCHANGE_REGIONS.items():
        if exchange in exchanges:
            return region
    return None


def get_stock_price(ticker: str) -> dict | None:
    """Precio actual de una acción/ETF/fondo vía Yahoo Finance. Devuelve
    {price, currency, previous_close, exchange, instrument_type} o None si falla."""
    try:
        meta = yahoo_chart(ticker, {"range": "1d", "interval": "1d"})["meta"]
        price = meta.get("regularMarketPrice")
        if price is None:
            return None
        prev_close = meta.get("regularMarketPreviousClose") or meta.get("chartPreviousClose")
        price, currency = normalize_quote_currency(float(price), meta.get("currency"))
        prev_close, _ = normalize_quote_currency(
            float(prev_close) if prev_close else None, meta.get("currency")
        )
        return {
            "price": price,
            "currency": currency,
            "previous_close": prev_close,
            "exchange": meta.get("exchangeName"),
            "instrument_type": meta.get("instrumentType"),
            # Nombre legible ("Microsoft Corporation"): para renombrar activos cuyo
            # nombre es el propio ticker (importaciones de Revolut)
            "name": meta.get("longName") or meta.get("shortName"),
        }
    except Exception as exc:
        # Con el motivo concreto: un ticker inexistente, una cuota agotada y un
        # corte de red exigen acciones distintas y antes se veían igual.
        logger.warning("Precio de %s: %s", ticker, errores_api.registrar(exc, "Yahoo", ticker))
        return None


def get_crypto_price(coingecko_id: str, vs_currency: str = "eur") -> tuple[float, float | None] | None:
    """Precio actual de una criptomoneda vía CoinGecko, con el precio de hace 24h
    derivado del cambio porcentual. Devuelve (precio, precio_24h_antes) o None.
    `coingecko_id` es el id de CoinGecko (ej. 'bitcoin', 'ethereum'), no el símbolo/ticker."""
    try:
        resp = httpx.get(
            COINGECKO_URL,
            params={"ids": coingecko_id, "vs_currencies": vs_currency, "include_24hr_change": "true"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()[coingecko_id]
        price = float(data[vs_currency])
        change_pct = data.get(f"{vs_currency}_24h_change")
        prev = price / (1 + change_pct / 100.0) if change_pct is not None else None
        return price, prev
    except Exception as exc:
        # CoinGecko corta con 429 en el plan gratuito: hay que poder distinguirlo
        # de un id mal escrito, porque uno se arregla esperando y el otro no.
        logger.warning("Precio de %s: %s", coingecko_id,
                       errores_api.registrar(exc, "CoinGecko", coingecko_id))
        return None
