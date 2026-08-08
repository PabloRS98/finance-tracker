"""Histórico de precios y reconstrucción de la evolución del patrimonio.

- Cierres diarios cacheados en `price_history` (Yahoo para acciones/benchmarks,
  CoinGecko para cripto —la API gratuita solo da 365 días—, Frankfurter para FX).
- La parte invertida se reconstruye día a día desde las operaciones: cantidad
  acumulada × precio de cierre, con relleno hacia delante. Si un día no hay
  precio de mercado, se usa el precio de la última operación (así la curva
  existe incluso sin ticker).
- La parte manual (cuentas/inmuebles) sale de los snapshots diarios; para
  snapshots antiguos sin desglose se deriva restando la parte invertida.
"""
import logging
import re
from datetime import UTC, date, datetime, time, timedelta

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Asset,
    AssetType,
    Benchmark,
    NetWorthSnapshot,
    Operation,
    OperationType,
    PriceHistory,
    TransactionStatus,
)
from . import market_data

logger = logging.getLogger(__name__)

FRANKFURTER_SERIES_URL = "https://api.frankfurter.dev/v1"
COINGECKO_CHART_URL = "https://api.coingecko.com/api/v3/coins/%s/market_chart"
COINGECKO_MAX_DAYS = 365  # límite de histórico de la API gratuita

INVERTIBLE = (AssetType.ACCION, AssetType.CRIPTO)


def benchmarks_configurados(db: Session) -> list[Benchmark]:
    """Índices de referencia, en orden de alta. Antes eran dos constantes; ahora
    salen de la tabla para que se puedan añadir y quitar desde la interfaz."""
    return db.query(Benchmark).order_by(Benchmark.id).all()


def clave_de_simbolo(symbol: str) -> str:
    """Identificador estable a partir del símbolo ("^GSPC" -> "gspc").

    Lo usan el selector del dashboard y las columnas de la tabla anual, así que
    tiene que ser algo que sobreviva a renombrar la etiqueta."""
    return re.sub(r"[^a-z0-9]+", "_", symbol.lower()).strip("_") or "benchmark"


# ---------- Fetchers ----------

def fetch_stock_history(ticker: str, start: date) -> dict[date, float]:
    """Cierres diarios desde el endpoint de gráficas de Yahoo (sin yfinance)."""
    try:
        period1 = int(datetime.combine(start, time.min, tzinfo=UTC).timestamp())
        period2 = int(datetime.now(UTC).timestamp())
        result = market_data.yahoo_chart(ticker, {"period1": period1, "period2": period2, "interval": "1d"})
        timestamps = result.get("timestamp") or []
        closes = (result.get("indicators", {}).get("quote") or [{}])[0].get("close") or []
        # Londres cotiza en peniques (GBp): escalar todos los cierres a libras
        quote_currency = (result.get("meta") or {}).get("currency")
        _, norm_currency = market_data.normalize_quote_currency(1.0, quote_currency)
        scale = 0.01 if (quote_currency and norm_currency == "GBP" and quote_currency != "GBP") else 1.0
        out: dict[date, float] = {}
        # strict=False: Yahoo puede devolver un cierre menos que marcas de
        # tiempo (la vela en curso). Cortar por la más corta es lo que toca.
        for ts, close in zip(timestamps, closes, strict=False):
            if close is not None:
                # epoch es UTC: fijar la zona para no correr el día según la TZ del contenedor
                out[datetime.fromtimestamp(ts, tz=UTC).date()] = float(close) * scale
        return out
    except Exception:
        logger.exception("Fallo al obtener histórico de %s", ticker)
        return {}


def fetch_crypto_history(coingecko_id: str, vs_currency: str, start: date) -> dict[date, float]:
    days = min((date.today() - start).days + 1, COINGECKO_MAX_DAYS)
    if days <= 0:
        return {}
    try:
        resp = httpx.get(
            COINGECKO_CHART_URL % coingecko_id,
            params={"vs_currency": vs_currency.lower(), "days": days, "interval": "daily"},
            timeout=20,
        )
        resp.raise_for_status()
        out: dict[date, float] = {}
        for ts_ms, price in resp.json().get("prices", []):
            out[datetime.fromtimestamp(ts_ms / 1000, tz=UTC).date()] = float(price)
        return out
    except Exception:
        logger.exception("Fallo al obtener histórico de cripto %s", coingecko_id)
        return {}


def fetch_fx_history(from_currency: str, to_currency: str, start: date) -> dict[date, float]:
    try:
        resp = httpx.get(
            "%s/%s..%s" % (FRANKFURTER_SERIES_URL, start.isoformat(), date.today().isoformat()),
            params={"from": from_currency, "to": to_currency},
            timeout=20, follow_redirects=True,
        )
        resp.raise_for_status()
        return {
            date.fromisoformat(day): float(rates[to_currency])
            for day, rates in resp.json().get("rates", {}).items()
        }
    except Exception:
        logger.exception("Fallo al obtener histórico FX %s->%s", from_currency, to_currency)
        return {}


# ---------- Cache en price_history ----------

def _stored_symbol_dates(db: Session, symbol: str) -> set[date]:
    return {d for (d,) in db.query(PriceHistory.date).filter(PriceHistory.symbol == symbol).all()}


def _store(db: Session, symbol: str, series: dict[date, float]) -> int:
    existing = _stored_symbol_dates(db, symbol)
    added = 0
    for day, price in series.items():
        if day not in existing:
            db.add(PriceHistory(symbol=symbol, date=day, price=price))
            added += 1
    db.commit()
    return added


def _latest_stored(db: Session, symbol: str) -> date | None:
    row = (
        db.query(PriceHistory.date)
        .filter(PriceHistory.symbol == symbol)
        .order_by(PriceHistory.date.desc())
        .first()
    )
    return row[0] if row else None


def _earliest_stored(db: Session, symbol: str) -> date | None:
    row = (
        db.query(PriceHistory.date)
        .filter(PriceHistory.symbol == symbol)
        .order_by(PriceHistory.date)
        .first()
    )
    return row[0] if row else None


def refresh_price_history(db: Session) -> None:
    """Rellena el cache de cierres para activos con operaciones, FX y benchmarks.
    Solo pide a las APIs el tramo que falta desde el último cierre guardado."""
    first_op = db.query(Operation.date).order_by(Operation.date).first()
    if not first_op:
        return
    start = first_op[0]

    def missing_from(symbol: str) -> date:
        latest = _latest_stored(db, symbol)
        return max(start, latest + timedelta(days=1)) if latest else start

    asset_ids = {aid for (aid,) in db.query(Operation.asset_id).distinct().all()}
    # USD siempre: la tarjeta EUR/USD del dashboard (eur_usd_snapshot) y el
    # toggle €$ dependen de la serie FX:USD:EUR aunque no haya activos en USD
    fx_currencies = {"USD"}
    for asset in db.query(Asset).filter(Asset.id.in_(asset_ids)).all() if asset_ids else []:
        if asset.currency.value != settings.base_currency:
            fx_currencies.add(asset.currency.value)
        if asset.exposure_currency and asset.exposure_currency != settings.base_currency:
            fx_currencies.add(asset.exposure_currency)
        if not asset.ticker:
            continue
        since = missing_from(asset.ticker)
        if asset.asset_type == AssetType.CRIPTO:
            if since <= date.today():
                series = fetch_crypto_history(asset.ticker, asset.currency.value, since)
                if series:
                    _store(db, asset.ticker, series)
        else:
            # Acciones: además del tramo incremental, un backfill único hasta 5 años
            # atrás (para el rango 5A de la ficha); _store deduplica, es idempotente
            desired_start = min(since, date.today() - timedelta(days=5 * 365))
            earliest = _earliest_stored(db, asset.ticker)
            if earliest is None or earliest > desired_start + timedelta(days=7):
                since = desired_start
            if since <= date.today():
                series = fetch_stock_history(asset.ticker, since)
                if series:
                    _store(db, asset.ticker, series)

    for fx_cur in sorted(fx_currencies):
        fx_symbol = "FX:%s:%s" % (fx_cur, settings.base_currency)
        since = missing_from(fx_symbol)
        if since <= date.today():
            series = fetch_fx_history(fx_cur, settings.base_currency, since)
            if series:
                _store(db, fx_symbol, series)

    for bench in benchmarks_configurados(db):
        symbol = bench.symbol
        since = missing_from(symbol)
        # Si la serie del índice no llega hasta la primera operación, se pide
        # desde ahí. Los benchmarks no tenían el backfill que sí hacen los
        # activos, así que una vez guardado el primer cierre solo avanzaban
        # hacia delante: la comparación contra el índice quedaba en blanco para
        # todo el periodo anterior a esa primera descarga, que es casi siempre
        # el grueso del histórico.
        earliest = _earliest_stored(db, symbol)
        if earliest is not None and earliest > start:
            since = start
        if since <= date.today():
            series = fetch_stock_history(symbol, since)
            if series:
                _store(db, symbol, series)


# ---------- Reconstrucción de la evolución ----------

def _forward_filled(series: dict[date, float], timeline: list[date]) -> dict[date, float]:
    """Serie completa sobre la línea temporal, arrastrando el último valor conocido."""
    out: dict[date, float] = {}
    last = None
    for day in timeline:
        if day in series:
            last = series[day]
        if last is not None:
            out[day] = last
    return out


def _symbol_series(db: Session, symbol: str) -> dict[date, float]:
    return {
        row.date: row.price
        for row in db.query(PriceHistory).filter(PriceHistory.symbol == symbol).all()
    }


def portfolio_evolution(db: Session) -> list[dict]:
    """Serie diaria [{fecha, total, invertido}] en la moneda base.

    invertido(t) = Σ activos cantidad(t) × precio(t) × fx(t)
    total(t) = invertido(t) + parte manual (del snapshot más reciente ≤ t)
    """
    ops = (
        db.query(Operation)
        .filter(Operation.status == TransactionStatus.CONFIRMADO)
        .order_by(Operation.date, Operation.id)
        .all()
    )
    snapshots = db.query(NetWorthSnapshot).order_by(NetWorthSnapshot.date).all()

    if not ops:
        # Sin operaciones: la gráfica clásica de snapshots. Emite las MISMAS
        # claves que el camino normal aunque valgan cero. Cuando faltaban,
        # /analisis reventaba con KeyError: 'twr' en cualquier instalación
        # recién montada —el arranque crea un snapshot, así que la lista no
        # estaba vacía y el `if evolution` de la vista no protegía de nada—.
        return [
            {
                "fecha": s.date.isoformat(),
                "total": s.total_value,
                "invertido": 0.0,
                "aportado": 0.0,
                "twr": 0.0,
            }
            for s in snapshots
        ]

    start = ops[0].date
    if snapshots and snapshots[0].date < start:
        start = snapshots[0].date
    today = date.today()
    timeline = [start + timedelta(days=i) for i in range((today - start).days + 1)]

    # Cantidad acumulada por activo y día (pasos en las fechas de operación)
    assets = {a.id: a for a in db.query(Asset).filter(Asset.id.in_({o.asset_id for o in ops})).all()}
    qty_steps: dict[int, dict[date, float]] = {aid: {} for aid in assets}
    running: dict[int, float] = dict.fromkeys(assets, 0.0)
    op_price_steps: dict[int, dict[date, float]] = {aid: {} for aid in assets}
    for op in ops:
        delta = op.quantity if op.type == OperationType.COMPRA else -op.quantity
        running[op.asset_id] = running.get(op.asset_id, 0.0) + delta
        qty_steps[op.asset_id][op.date] = running[op.asset_id]
        op_price_steps[op.asset_id][op.date] = op.unit_price

    # Serie FX divisa->base por cada divisa presente en la cartera (forward-filled;
    # sin serie descargada aún se asume 1.0, como antes de la primera descarga)
    fx_by_currency: dict[str, dict[date, float]] = {}

    def fx_series_for(currency: str) -> dict[date, float]:
        if currency not in fx_by_currency:
            fx_by_currency[currency] = _forward_filled(
                _symbol_series(db, "FX:%s:%s" % (currency, settings.base_currency)), timeline
            )
        return fx_by_currency[currency]

    invested_by_day: dict[date, float] = dict.fromkeys(timeline, 0.0)
    for aid, asset in assets.items():
        quantities = _forward_filled(qty_steps[aid], timeline)
        # Precio: cierres de mercado; huecos (o falta de ticker) se cubren con el
        # precio de la última operación, y el precio actual para el último día
        market = _symbol_series(db, asset.ticker) if asset.ticker else {}
        merged = dict(op_price_steps[aid])
        merged.update(market)
        if asset.current_price is not None:
            merged[today] = asset.current_price
        prices = _forward_filled(merged, timeline)

        asset_fx = (
            fx_series_for(asset.currency.value)
            if asset.currency.value != settings.base_currency else None
        )
        for day in timeline:
            qty = quantities.get(day, 0.0)
            price = prices.get(day)
            if not qty or price is None:
                continue
            value = qty * price
            if asset_fx is not None:
                value *= asset_fx.get(day, 1.0)
            invested_by_day[day] += value

    # Parte manual: pasos con los snapshots (manual_value directo, o derivado
    # restando la parte invertida en snapshots antiguos sin desglose)
    manual_steps: dict[date, float] = {}
    for snap in snapshots:
        if snap.manual_value is not None:
            manual_steps[snap.date] = snap.manual_value
        else:
            manual_steps[snap.date] = max(0.0, snap.total_value - invested_by_day.get(snap.date, 0.0))
    manual_series = _forward_filled(manual_steps, timeline)

    # Flujos de caja diarios (aportaciones/retiradas de la parte invertida) para
    # el TWR: rendimiento encadenado que descuenta las aportaciones, comparable
    # con un índice (una compra no debe parecer que "sube" la cartera)
    flow_steps: dict[date, float] = {}
    for op in ops:
        asset = assets[op.asset_id]
        amount = op.quantity * op.unit_price
        fee = op.fee or 0.0
        signed = (amount + fee) if op.type == OperationType.COMPRA else -(amount - fee)
        if asset.currency.value != settings.base_currency:
            signed *= fx_series_for(asset.currency.value).get(op.date, 1.0)
        flow_steps[op.date] = flow_steps.get(op.date, 0.0) + signed

    result = []
    twr_index = 1.0
    prev_invested = 0.0
    aportado = 0.0
    for day in timeline:
        invested = invested_by_day[day]
        flow = flow_steps.get(day, 0.0)
        # Aportación neta acumulada: lo que has puesto de tu bolsillo, sin contar
        # revalorización. La distancia con "invertido" es la ganancia, y de sus
        # diferencias diarias sale el XIRR (ver services/rendimiento.py).
        aportado += flow
        if prev_invested > 0:
            twr_index *= 1 + (invested - prev_invested - flow) / prev_invested
        prev_invested = invested
        result.append({
            "fecha": day.isoformat(),
            "total": round(invested + manual_series.get(day, 0.0), 2),
            "invertido": round(invested, 2),
            "aportado": round(aportado, 2),
            "twr": round((twr_index - 1) * 100, 3),
        })
    return result


def cagr_from_evolution(evolution: list[dict]) -> float | None:
    """Rentabilidad anualizada (%) a partir de la serie TWR de `portfolio_evolution`.
    Anualiza el TWR acumulado sobre el periodo con exposición (desde el primer día
    con parte invertida > 0). None con menos de 90 días de histórico: anualizar
    periodos cortos dispara cifras absurdas (+2% en 2 semanas -> +68% anual)."""
    points = [p for p in evolution if p.get("invertido", 0) > 0]
    if len(points) < 2:
        return None
    days = (date.fromisoformat(points[-1]["fecha"]) - date.fromisoformat(points[0]["fecha"])).days
    if days < 90:
        return None
    total_growth = 1 + evolution[-1]["twr"] / 100
    if total_growth <= 0:
        return None
    return round(100 * (total_growth ** (365.25 / days) - 1), 2)


def eur_usd_snapshot(db: Session) -> dict:
    """Cotización EUR/USD (dólares que vale 1 euro): tipo actual, variación
    frente al último cierre BCE anterior a hoy, frase legible y serie histórica
    para la gráfica. La serie cacheada (`FX:USD:EUR`) es USD->base: se invierte."""
    rate = market_data.get_exchange_rate("EUR", "USD")
    if rate is None:
        # Sin tipo actual no hay tarjeta EUR/USD: el dashboard la oculta
        return {"rate": None, "change_pct": None, "change_pct_weekly": None, "phrase": None, "points": []}
    series = _symbol_series(db, "FX:USD:%s" % settings.base_currency)
    points = [
        {"fecha": day.isoformat(), "rate": round(1.0 / price, 4)}
        for day, price in sorted(series.items()) if price
    ]

    today = date.today().isoformat()
    closes = [p for p in points if p["fecha"] < today]
    prev = closes[-1]["rate"] if closes else None
    change_pct = 100.0 * (rate - prev) / prev if prev else None

    # Variación semanal: comparar con cierre de hace ~7 días naturales
    week_ago = (date.today() - timedelta(days=7)).isoformat()
    week_closes = [p for p in points if p["fecha"] < week_ago]
    prev_week = week_closes[-1]["rate"] if week_closes else None
    change_pct_weekly = 100.0 * (rate - prev_week) / prev_week if prev_week else None

    if change_pct is None:
        phrase = None
    elif abs(change_pct) < 0.005:  # por debajo del redondeo mostrado (+0,00%)
        phrase = "el euro se mantiene frente al dólar"
    elif change_pct > 0:
        phrase = "el euro se revaloriza frente al dólar"
    else:
        phrase = "el euro se deprecia frente al dólar"

    # Punto de hoy con el tipo en vivo, para que la curva llegue al presente
    if not points or points[-1]["fecha"] < today:
        points.append({"fecha": today, "rate": round(rate, 4)})
    return {
        "rate": rate, "change_pct": change_pct, "change_pct_weekly": change_pct_weekly,
        "phrase": phrase, "points": points,
    }


def benchmark_series(db: Session) -> dict[str, dict]:
    """Series de cierre de los benchmarks para la gráfica (modo comparación %)."""
    out: dict[str, dict] = {}
    for bench in benchmarks_configurados(db):
        series = _symbol_series(db, bench.symbol)
        out[bench.clave] = {
            "label": bench.label,
            "points": [
                {"fecha": day.isoformat(), "close": price}
                for day, price in sorted(series.items())
            ],
        }
    return out
