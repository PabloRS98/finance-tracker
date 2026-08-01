"""Importadores de operaciones desde CSV de brokers/exchanges.

Cada parser convierte el CSV a una lista de ParsedRow homogénea; el router se
encarga de casarlas con activos existentes, deduplicar (import_hash) y crear
las operaciones. Los parsers son tolerantes con cabeceras (varios idiomas,
;, o tab como separador) porque cada broker exporta distinto según el locale.

Filas de dividendos, depósitos, intereses, etc. se omiten a propósito y se
cuentan en `skipped` para informar al usuario.
"""
from __future__ import annotations  # el campo ParsedRow.date eclipsa a datetime.date

import csv
import hashlib
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class ParsedRow:
    date: date | None = None
    type: str = ""                 # "compra" | "venta"
    name: str = ""                 # nombre legible del activo
    ticker: str | None = None      # símbolo Yahoo / id CoinGecko si se conoce
    isin: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    fee: float = 0.0
    currency: str = "EUR"          # EUR | USD
    kind: str = "accion"           # "accion" | "cripto"
    error: str | None = None       # fila reconocida pero inválida (se muestra, no se importa)

    def import_hash(self) -> str:
        key = "%s|%s|%s|%.8f|%.8f" % (
            self.date.isoformat() if self.date else "",
            (self.isin or self.ticker or self.name).upper(),
            self.type,
            self.quantity or 0.0,
            self.unit_price or 0.0,
        )
        return hashlib.sha1(key.encode()).hexdigest()


@dataclass
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)  # motivo -> nº de filas omitidas
    error: str | None = None  # error global (CSV ilegible, cabeceras no reconocidas)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")


def norm_header(header: str) -> str:
    """Normaliza una cabecera para compararla: minúsculas, sin acentos ni símbolos."""
    return re.sub(r"[^a-z0-9 ]", "", _strip_accents(header or "").lower()).strip()


ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def looks_like_isin(value: str) -> bool:
    """True si `value` tiene forma de ISIN (2 letras de país + 9 alfanum + dígito).
    Sirve para distinguir un ISIN de un ticker cuando llegan en la misma columna."""
    return bool(ISIN_RE.match((value or "").strip().upper()))


def read_csv(text: str) -> list[dict[str, str]]:
    """Lee el CSV detectando el separador (, ; o tab) y devuelve filas como dicts
    con las cabeceras ya normalizadas."""
    text = text.lstrip("﻿")
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    rows = []
    for raw in reader:
        rows.append({norm_header(k): (v or "").strip() for k, v in raw.items() if k})
    return rows


def pick(row: dict[str, str], *candidates: str) -> str:
    """Primer valor no vacío de la fila cuya cabecera normalizada esté en `candidates`."""
    for cand in candidates:
        value = row.get(cand)
        if value:
            return value
    return ""


def to_float(raw: str) -> float | None:
    """Convierte importes con formato es/en ('1.234,56', '1,234.56', '-12.5 €')."""
    if not raw:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", raw)
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        # El último símbolo es el separador decimal
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


DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%m/%d/%Y",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S",
)


def to_date(raw: str) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    # ISO con zona horaria o microsegundos ("2026-03-01T10:20:30.123Z")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


# Símbolo cripto -> id de CoinGecko (los más habituales; el resto queda sin ticker
# y el usuario lo completa en la ficha del activo)
CRYPTO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "ripple",
    "ADA": "cardano", "DOGE": "dogecoin", "DOT": "polkadot", "LINK": "chainlink",
    "AVAX": "avalanche-2", "LTC": "litecoin", "BNB": "binancecoin", "TRX": "tron",
    "TON": "the-open-network", "PEPE": "pepe", "SHIB": "shiba-inu", "UNI": "uniswap",
    "ATOM": "cosmos", "NEAR": "near", "ARB": "arbitrum", "OP": "optimism",
    "MATIC": "matic-network", "POL": "polygon-ecosystem-token", "XLM": "stellar",
}

# Divisas cotizadas aceptadas; las stablecoins USD se tratan como USD
QUOTE_CURRENCIES = {"EUR": "EUR", "USD": "USD", "USDT": "USD", "USDC": "USD"}


from . import generic, okx, revolut, revolut_pdf, trade_republic  # noqa: E402

IMPORTERS = {
    "trade_republic": {"label": "Trade Republic", "parse": trade_republic.parse},
    "revolut": {"label": "Revolut (CSV)", "parse": revolut.parse},
    "revolut_pdf": {"label": "Revolut (PDF)", "parse": revolut_pdf.parse},
    "okx": {"label": "OKX", "parse": okx.parse},
    "generic": {"label": "CSV genérico", "parse": generic.parse},
}
