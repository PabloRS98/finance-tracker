"""Clasificación automática de región y sector de activos.

Tres fuentes, de más a menos específica:
1. Heurística por NOMBRE para fondos/ETFs indexados ("MSCI World" → Global,
   "S&P 500" → EE. UU., "EuroStoxx" → Europa...).
2. Búsqueda de Yahoo para acciones sueltas (v1/finance/search devuelve
   sector/industry sin necesidad de crumb, a diferencia de quoteSummary).
3. Región aproximada por exchange (market_data.region_for_exchange).

NUNCA pisa valores puestos por el usuario: solo rellena region/sector vacíos.
Se invoca al refrescar precios (scheduler y router de activos), así los
activos existentes sin clasificar se rellenan solos en la primera pasada.
"""
import logging
import re

from ..config import settings
from ..models import Asset, AssetType
from . import market_data

logger = logging.getLogger(__name__)

# Orden: del patrón más específico al más genérico (el primero que casa gana).
# "MSCI Emerging Markets Asia" debe caer en Emergentes antes que en Asia, y
# cualquier cosa con "World"/"Global" solo al final.
REGION_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"emerging|emergente", re.I), "Emergentes"),
    (re.compile(r"s\s*&\s*p\s*500|sp\s*500|nasdaq|russell|dow\s*jones|\busa?\b|u\.s\.|estados unidos|north america", re.I), "EE. UU."),
    (re.compile(r"jap[oó]n|(?<!ex-)(?<!ex )japan|nikkei|topix", re.I), "Japón"),
    (re.compile(r"\bchina\b|csi\s*300|hang\s*seng|\bhong\s*kong\b", re.I), "China"),
    (re.compile(r"\basia\b|pacific", re.I), "Asia"),
    (re.compile(r"euro\s*stoxx|stoxx\s*600|\beurope\b|europa|\bdax\b|\bibex\b|\bcac\b|ftse\s*100|\bmib\b", re.I), "Europa"),
    (re.compile(r"\bworld\b|acwi|all[- ]?country|all[- ]?world|global|developed", re.I), "Global"),
]

# Sectores de Yahoo (inglés) → etiquetas cortas en español (las de /analisis)
SECTOR_ES = {
    "Technology": "Tecnología",
    "Communication Services": "Comunicación",
    "Healthcare": "Salud",
    "Financial Services": "Finanzas",
    "Energy": "Energía",
    "Basic Materials": "Materiales",
    "Consumer Cyclical": "Consumo",
    "Consumer Defensive": "Consumo",
    "Industrials": "Industria",
    "Real Estate": "Inmobiliario",
    "Utilities": "Utilities",
}

# Nombre con pinta de índice/fondo amplio → sector "Diversificado" (no hay un
# sector concreto); los índices tecnológicos sí tienen sector claro.
_TECH_INDEX = re.compile(r"nasdaq|tecnolog|technology", re.I)
_BROAD_INDEX = re.compile(
    r"msci|s\s*&\s*p\s*500|sp\s*500|stoxx|\bftse\b|russell|nikkei|topix|csi\s*300|"
    r"hang\s*seng|acwi|all[- ]?country|all[- ]?world|\bworld\b|\bibex\b|\bdax\b|\bcac\b|"
    r"\bindex\b|índice|\betf\b|ucits",
    re.I,
)


# Divisa de exposición en el nombre del fondo: la clase de divisa ("USD (Acc)",
# "GBP Hedged"...). EUR no cuenta: es la base, sin exposición aparte.
_EXPOSURE_TOKEN = re.compile(r"\b(USD|GBP|CHF|JPY|HKD|CNY|SEK|NOK|DKK|CAD|AUD)\b")


def exposure_from_name(name: str | None) -> str | None:
    if not name:
        return None
    m = _EXPOSURE_TOKEN.search(name)
    return m.group(1) if m else None


def region_from_name(name: str | None) -> str | None:
    if not name:
        return None
    for pattern, region in REGION_PATTERNS:
        if pattern.search(name):
            return region
    return None


def sector_from_name(name: str | None) -> str | None:
    if not name:
        return None
    if _TECH_INDEX.search(name):
        return "Tecnología"
    if _BROAD_INDEX.search(name):
        return "Diversificado"
    return None


def yahoo_sector(ticker: str) -> str | None:
    """Sector vía el endpoint de búsqueda de Yahoo. Si algún día deja de traer
    el campo, devuelve None y queda el resto de heurísticas."""
    try:
        for q in market_data._yahoo_search(ticker, 3):
            if (q.get("symbol") or "").upper() == ticker.upper() and q.get("sector"):
                return SECTOR_ES.get(q["sector"], q["sector"])
    except Exception:
        logger.exception("Fallo buscando el sector de %s", ticker)
    return None


def autofill(asset: Asset, stock_meta: dict | None = None) -> bool:
    """Rellena region/sector SOLO si están vacíos (nunca pisa al usuario).
    `stock_meta` es el dict de get_stock_price (exchange/instrument_type), si
    se tiene a mano. Devuelve True si cambió algo."""
    changed = False

    if asset.asset_type == AssetType.CRIPTO:
        if asset.region is None:
            asset.region = "Global"
            changed = True
        if asset.sector is None:
            asset.sector = "Cripto"
            changed = True
        return changed

    if asset.asset_type != AssetType.ACCION:
        return changed

    if asset.region is None:
        region = region_from_name(asset.name)
        if region is None and stock_meta and stock_meta.get("instrument_type") == "EQUITY":
            region = market_data.region_for_exchange(stock_meta.get("exchange"))
        if region:
            asset.region = region
            changed = True

    if asset.sector is None:
        sector = sector_from_name(asset.name)
        if sector is None and asset.ticker and (stock_meta is None or stock_meta.get("instrument_type") == "EQUITY"):
            sector = yahoo_sector(asset.ticker)
        if sector:
            asset.sector = sector
            changed = True

    # Divisa de exposición: solo para activos que cotizan en la base (en los
    # demás la divisa de cotización YA da el efecto divisa)
    if asset.exposure_currency is None and asset.currency.value == settings.base_currency:
        exposure = exposure_from_name(asset.name)
        if (exposure is None and stock_meta and stock_meta.get("instrument_type") == "EQUITY"
                and asset.region == "EE. UU."):
            # Acción individual USA cotizada en EUR (cross-listing de Frankfurt)
            exposure = "USD"
        if exposure and exposure != settings.base_currency:
            asset.exposure_currency = exposure
            changed = True

    return changed
