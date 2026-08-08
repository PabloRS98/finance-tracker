"""Cálculo de posiciones a partir de operaciones: coste medio, P&L realizado y
no realizado, variación del día y efecto divisa.

Método de coste medio: cada compra suma su importe (más comisión) al coste de la
posición; cada venta retira coste al precio medio del momento y cristaliza P&L
realizado. Todos los importes en la divisa del activo.

En paralelo se lleva el mismo coste en la moneda base, convertido al tipo de
cambio del día de cada compra: comparando el rendimiento en base (coste
histórico, valor al FX actual) con el rendimiento local sale el efecto divisa,
siempre como porcentaje aparte del P&L de precio."""
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Asset, AssetType, Operation, OperationType, PriceHistory, TransactionStatus
from . import market_data

# Tipo de cambio divisa-del-activo -> base para una fecha dada
FxLookup = Callable[[date], float]

# Por debajo de esta cantidad se considera que no queda posición. Las ventas a
# coste medio arrastran error de coma flotante y vender una posición entera puede
# dejarla en 1e-16 en vez de en 0 clavado.
CANTIDAD_MINIMA = 1e-9


@dataclass
class Position:
    quantity: float = 0.0
    cost_open: float = 0.0      # coste de la posición abierta (compras - ventas a coste medio)
    cost_open_base: float = 0.0  # el mismo coste en moneda base, al FX del día de cada compra
    invested: float = 0.0       # total aportado en compras, informativo
    realized_pnl: float = 0.0   # P&L cristalizado por ventas (neto de comisiones)
    has_operations: bool = False

    @property
    def avg_cost(self) -> float | None:
        """Precio medio de compra de la posición abierta."""
        if self.quantity > 0 and self.cost_open > 0:
            return self.cost_open / self.quantity
        return None


def compute_position(operations: list[Operation], fx_on: FxLookup | None = None) -> Position:
    """Posición resultante de una lista de operaciones (se ordenan por fecha).
    Si se vende más cantidad de la que hay, el exceso se trata como coste cero
    (la posición nunca queda con coste negativo).

    `fx_on(fecha)` convierte la divisa del activo a la base en esa fecha; sin él
    (activos en la divisa base) el coste en base coincide con el local."""
    pos = Position()
    confirmed = [o for o in operations if o.status == TransactionStatus.CONFIRMADO]
    for op in sorted(confirmed, key=lambda o: (o.date, o.id or 0)):
        pos.has_operations = True
        if op.type == OperationType.COMPRA:
            pos.quantity += op.quantity
            cost = op.quantity * op.unit_price + (op.fee or 0.0)
            pos.cost_open += cost
            pos.cost_open_base += cost * (fx_on(op.date) if fx_on else 1.0)
            pos.invested += cost
        else:  # VENTA
            avg = pos.cost_open / pos.quantity if pos.quantity > 0 else 0.0
            avg_base = pos.cost_open_base / pos.quantity if pos.quantity > 0 else 0.0
            sold_qty = min(op.quantity, pos.quantity)
            cost_removed = avg * sold_qty
            proceeds = op.quantity * op.unit_price - (op.fee or 0.0)
            pos.realized_pnl += proceeds - cost_removed
            pos.cost_open -= cost_removed
            pos.cost_open_base -= avg_base * sold_qty
            pos.quantity -= op.quantity
            if pos.quantity <= 0:
                pos.quantity = max(pos.quantity, 0.0)
                pos.cost_open = 0.0
                pos.cost_open_base = 0.0
    return pos


def posicion_cerrada(pos: Position) -> bool:
    """El activo tuvo operaciones y ya no queda nada: se vendió entero.

    No es lo mismo que "sin posición". Un activo dado de alta y aún sin comprar
    tampoco tiene cantidad, pero no está cerrado: no hay historial detrás. La
    distinción importa porque una posición cerrada guarda operaciones reales y
    P&L realizado que sostienen la rentabilidad histórica —hay que conservarla,
    solo que sin mezclarla con lo que sigue vivo en la cartera.
    """
    return pos.has_operations and pos.quantity <= CANTIDAD_MINIMA


def fx_lookup(db: Session, currency: str) -> FxLookup | None:
    """Tipo de cambio `currency`->base por fecha, para valorar cada compra al FX
    de su día. Usa la serie diaria cacheada en `price_history` (último cierre
    conocido para festivos); para hoy —o sin serie— usa el tipo actual.
    Devuelve None si la divisa ya es la base."""
    if currency == settings.base_currency:
        return None
    current = market_data.get_exchange_rate(currency, settings.base_currency)
    rows = (
        db.query(PriceHistory.date, PriceHistory.price)
        .filter(PriceHistory.symbol == "FX:%s:%s" % (currency, settings.base_currency))
        .order_by(PriceHistory.date)
        .all()
    )
    if not rows:
        # Sin tipo actual ni serie histórica no hay nada con qué convertir: se
        # devuelve None y quien llame se queda sin descomposición de divisa
        # (mejor omitir el análisis que calcularlo con un tipo inventado).
        return (lambda day: current) if current is not None else None
    dates = [r[0] for r in rows]
    rates = [r[1] for r in rows]

    def rate_for(day: date) -> float:
        if day >= date.today() and current is not None:
            return current
        i = bisect_right(dates, day)
        return rates[i - 1] if i > 0 else rates[0]

    return rate_for


def fx_effect(pnl_pct_local: float, pnl_pct_base: float) -> float | None:
    """Efecto divisa (%): la parte del rendimiento en base que no explica el precio.
    Descomposición multiplicativa: (1+total) = (1+local) × (1+divisa)."""
    local_growth = 1 + pnl_pct_local / 100.0
    if local_growth <= 0:
        return None
    return 100.0 * ((1 + pnl_pct_base / 100.0) / local_growth - 1)


def resumen_completo(db: Session, asset: Asset) -> dict:
    """`asset_summary` con los dos lookups de divisa puestos.

    Existe para que no haya una tercera forma de montar esta llamada. La ficha
    del activo los pasaba y la lista de operaciones no, así que al filtrar las
    operaciones de un activo en dólares el panel de resumen mostraba el efecto
    divisa vacío mientras la ficha del mismo activo lo enseñaba: dos cifras
    distintas para lo mismo según por dónde se entrara.
    """
    return asset_summary(asset, fx_lookup(db, asset.currency.value), exposure_fx_lookup(db, asset, {}))


def exposure_fx_lookup(db: Session, asset: Asset, cache: dict[str, FxLookup | None]) -> FxLookup | None:
    """Lookup FX para la divisa de EXPOSICIÓN del activo (subyacente en otra
    divisa aunque cotice en la base, ej. clase USD de un fondo comprada en EUR).
    None si el activo no tiene exposición aparte o no cotiza en la base."""
    exp = asset.exposure_currency
    if not exp or exp == settings.base_currency or asset.currency.value != settings.base_currency:
        return None
    if exp not in cache:
        cache[exp] = fx_lookup(db, exp)
    return cache[exp]


def asset_summary(asset: Asset, fx_on: FxLookup | None = None, exposure_fx: FxLookup | None = None) -> dict:
    """Resumen de la posición de un activo para las vistas: cantidad, coste medio,
    valor, P&L no realizado (€ y %) y variación del día. Divisa: la del activo.

    Con `fx_on` (activos en divisa distinta de la base) añade la descomposición:
    P&L en base con coste al FX histórico (`unrealized_base`, `pnl_pct_base`) y
    el efecto divisa como % aparte (`fx_effect_pct`).

    Con `exposure_fx` (activos que cotizan en la base con subyacente en otra
    divisa) la descomposición es la inversa: el retorno en base ya es el total y
    el "local" se reconstruye en la divisa de exposición con el FX del día de
    cada compra (las operaciones van en la base, así que su coste ya está
    implícitamente al FX histórico)."""
    pos = compute_position(asset.operations, fx_on)
    quantity = pos.quantity if pos.has_operations else asset.quantity
    price = asset.current_price

    # Valor: a precio de mercado si lo hay; si no, a coste medio (para que el activo
    # cuente en el patrimonio aunque aún no tenga cotización). El P&L no realizado
    # solo se calcula con precio de mercado real (a coste sería siempre 0 y engañoso).
    eff_price = price if price is not None else (pos.avg_cost if pos.has_operations else None)
    value = (quantity * eff_price) if (quantity is not None and eff_price is not None) else None

    # avg_cost y cost_open: se toman del avg_cost_override si está definido (criptos
    # sin operaciones, activos con coste manual), si no del Position calculado
    avg_cost = asset.avg_cost_override if asset.avg_cost_override is not None else pos.avg_cost
    cost_open = (avg_cost * quantity) if (
        avg_cost is not None and quantity is not None and quantity > 0
    ) else pos.cost_open

    unrealized = pnl_pct = None
    if price is not None and value is not None and cost_open > 0:
        unrealized = value - cost_open
        pnl_pct = 100.0 * unrealized / cost_open

    unrealized_base = pnl_pct_base = fx_effect_pct = None
    exposure_local_pct = exposure_cost_open = exposure_value = None
    if fx_on is not None and pnl_pct is not None and pos.cost_open_base > 0:
        value_base = value * fx_on(date.today())
        unrealized_base = value_base - pos.cost_open_base
        pnl_pct_base = 100.0 * unrealized_base / pos.cost_open_base
        fx_effect_pct = fx_effect(pnl_pct, pnl_pct_base)
    elif exposure_fx is not None and pnl_pct is not None:
        # Coste en la divisa de exposición, al FX del día de cada compra
        # (reutiliza cost_open_base de compute_position con el lookup invertido)
        pos_exp = compute_position(asset.operations, lambda d: 1.0 / exposure_fx(d))
        if pos_exp.cost_open_base > 0:
            exposure_cost_open = pos_exp.cost_open_base
            exposure_value = value / exposure_fx(date.today())
            exposure_local_pct = 100.0 * (exposure_value - exposure_cost_open) / exposure_cost_open
            unrealized_base = unrealized
            pnl_pct_base = pnl_pct
            fx_effect_pct = fx_effect(exposure_local_pct, pnl_pct_base)

    day_change = day_change_pct = None
    if quantity and price is not None and asset.previous_close:
        day_change = quantity * (price - asset.previous_close)
        day_change_pct = 100.0 * (price - asset.previous_close) / asset.previous_close

    return {
        "position": pos,
        "quantity": quantity,
        "avg_cost": avg_cost,
        "cost_open": cost_open,
        "value": value,
        "unrealized": unrealized,
        "pnl_pct": pnl_pct,
        "unrealized_base": unrealized_base,
        "pnl_pct_base": pnl_pct_base,
        "fx_effect_pct": fx_effect_pct,
        "exposure_currency": asset.exposure_currency if exposure_local_pct is not None else None,
        "exposure_local_pct": exposure_local_pct,
        "exposure_cost_open": exposure_cost_open,
        "exposure_value": exposure_value,
        "realized": pos.realized_pnl if pos.has_operations else None,
        "day_change": day_change,
        "day_change_pct": day_change_pct,
    }


def portfolio_totals(db: Session) -> dict:
    """Totales de la parte invertida (acciones/ETFs/cripto) convertidos a la moneda
    base: valor, coste abierto, P&L no realizado (€ y %), variación del día y
    efecto divisa agregado (% aparte del P&L de precio)."""
    total_value = total_cost = total_unrealized = total_day = 0.0
    pnl_value = cost_hist = 0.0  # solo activos con P&L calculable, para la descomposición
    any_ops = any_day = any_fx = False
    lookups: dict[str, FxLookup | None] = {}
    missing_fx: set[str] = set()  # divisas sin tipo de cambio: sus activos quedan fuera

    assets = (
        db.query(Asset)
        .options(selectinload(Asset.operations))  # evita N+1: asset_summary recorre las operaciones
        .filter(Asset.asset_type.in_([AssetType.ACCION, AssetType.CRIPTO]))
        .all()
    )
    for asset in assets:
        currency = asset.currency.value
        if currency not in lookups:
            lookups[currency] = fx_lookup(db, currency)
        fx_on = lookups[currency]
        rate = market_data.get_exchange_rate(currency, settings.base_currency)
        if rate is None:
            # Sin tipo de cambio no se puede sumar a la base: fuera del agregado
            # (contarlo 1:1 falsearía el patrimonio y el P&L de toda la cartera)
            missing_fx.add(currency)
            continue
        s = asset_summary(asset, fx_on, exposure_fx_lookup(db, asset, lookups))
        if s["value"] is not None:
            total_value += s["value"] * rate
        if s["unrealized"] is not None:
            any_ops = True
            exp_rate = (
                market_data.get_exchange_rate(s["exposure_currency"], settings.base_currency)
                if s["exposure_local_pct"] is not None else None
            )
            if exp_rate is not None:
                # Cotiza en base con subyacente en otra divisa: la pata "local" va
                # en la divisa de exposición (coste al FX de hoy, que la cancela) y
                # el coste en base ya está al FX histórico (las ops son en base)
                total_cost += s["exposure_cost_open"] * exp_rate
                total_unrealized += s["value"] - s["exposure_cost_open"] * exp_rate
                pnl_value += s["value"]
                cost_hist += s["position"].cost_open
                any_fx = True
            else:
                total_cost += s["cost_open"] * rate
                total_unrealized += s["unrealized"] * rate
                pnl_value += s["value"] * rate
                cost_hist += (
                    s["position"].cost_open_base if s["position"].cost_open_base > 0
                    else (s["cost_open"] * rate)
                )
                if fx_on is not None:
                    any_fx = True
        if s["day_change"] is not None:
            any_day = True
            total_day += s["day_change"] * rate

    pnl_pct = 100.0 * total_unrealized / total_cost if total_cost > 0 else None

    # Efecto divisa agregado: P&L en base con coste al FX histórico frente al
    # P&L "local" (coste al FX de hoy, que cancela la divisa). Solo tiene
    # sentido si hay algún activo en divisa distinta de la base.
    pnl_pct_base = fx_effect_pct = None
    if any_fx and cost_hist > 0 and pnl_pct is not None:
        pnl_pct_base = 100.0 * (pnl_value - cost_hist) / cost_hist
        fx_effect_pct = fx_effect(pnl_pct, pnl_pct_base)

    day_pct = None
    if any_day and total_value - total_day != 0:
        day_pct = 100.0 * total_day / (total_value - total_day)

    return {
        "invested_value": round(total_value, 2),
        "unrealized": round(total_unrealized, 2) if any_ops else None,
        "pnl_pct": round(pnl_pct, 2) if pnl_pct is not None else None,
        "pnl_pct_base": round(pnl_pct_base, 2) if pnl_pct_base is not None else None,
        "fx_effect_pct": round(fx_effect_pct, 2) if fx_effect_pct is not None else None,
        "day_change": round(total_day, 2) if any_day else None,
        "day_change_pct": round(day_pct, 2) if day_pct is not None else None,
        # Divisas sin tipo de cambio: sus activos NO están en las cifras de arriba
        "missing_fx": sorted(missing_fx),
    }
