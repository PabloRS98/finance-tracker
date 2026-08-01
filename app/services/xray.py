"""Allocations de la cartera invertida y análisis X-Ray (avisos de riesgo).

Reglas (umbrales configurables en .env):
- Concentración en un activo: un activo supera el % máximo de lo invertido.
- Concentración de divisa: la exposición fuera de la moneda base supera el % máximo.
- Precios estancados: activos con posición cuyo precio de mercado no se actualiza
  hace días (ticker roto, API caída) o valores manuales sin revisar hace meses.
"""
from datetime import timedelta

from sqlalchemy.orm import Session, selectinload

from ..config import settings
from ..models import Asset, AssetType, utcnow
from . import market_data
from .portfolio import asset_summary

INVERTIBLE = (AssetType.ACCION, AssetType.CRIPTO)
SIN_CLASIFICAR = "Sin clasificar"


def invested_rows(db: Session) -> list[dict]:
    """Activos invertibles con posición viva, valorados en la moneda base."""
    rows = []
    # selectinload: asset_summary recorre asset.operations de cada activo; sin esto
    # son N+1 consultas (una por activo) en cada carga de /analisis
    query = db.query(Asset).options(selectinload(Asset.operations)).filter(Asset.asset_type.in_(INVERTIBLE))
    for asset in query.all():
        summary = asset_summary(asset)
        value = summary["value"]
        if not value or value <= 0:
            continue
        rate = market_data.get_exchange_rate(asset.currency.value, settings.base_currency)
        if rate is None:
            continue  # sin tipo de cambio no entra en las allocations (falsearía los %)
        rows.append({"asset": asset, "summary": summary, "value_base": value * rate})
    return rows


def _breakdown(rows: list[dict], key_fn) -> list[tuple[str, float]]:
    grouped: dict[str, float] = {}
    for row in rows:
        label = key_fn(row["asset"]) or SIN_CLASIFICAR
        grouped[label] = grouped.get(label, 0.0) + row["value_base"]
    return sorted(grouped.items(), key=lambda kv: kv[1], reverse=True)


def allocations(db: Session) -> dict:
    rows = invested_rows(db)
    return {
        "total": sum(r["value_base"] for r in rows),
        "divisa": _breakdown(rows, lambda a: a.currency.value),
        "region": _breakdown(rows, lambda a: a.region),
        "sector": _breakdown(rows, lambda a: a.sector),
        "rows": rows,
    }


def xray_checks(db: Session, rows: list[dict] | None = None) -> list[dict]:
    """Lista de avisos [{level: warn|info|ok, titulo, detalle}]."""
    if rows is None:
        rows = invested_rows(db)
    total = sum(r["value_base"] for r in rows)
    checks: list[dict] = []
    now = utcnow()

    if total > 0:
        # 1. Concentración por activo
        for row in rows:
            pct = 100.0 * row["value_base"] / total
            if pct > settings.xray_max_asset_pct:
                checks.append({
                    "level": "warn",
                    "titulo": "Concentración en %s" % row["asset"].name,
                    "detalle": "Supone el %.1f%% de la cartera invertida (límite %.0f%%)."
                               % (pct, settings.xray_max_asset_pct),
                })

        # 2. Concentración de divisa (fuera de la moneda base)
        foreign = sum(r["value_base"] for r in rows if r["asset"].currency.value != settings.base_currency)
        foreign_pct = 100.0 * foreign / total
        if foreign_pct > settings.xray_max_currency_pct:
            checks.append({
                "level": "warn",
                "titulo": "Exposición a divisa extranjera",
                "detalle": "El %.1f%% de lo invertido no está en %s (límite %.0f%%)."
                           % (foreign_pct, settings.base_currency, settings.xray_max_currency_pct),
            })

    # 3. Precios estancados
    stale_market = timedelta(days=settings.xray_stale_price_days)
    for row in rows:
        asset = row["asset"]
        ref = asset.last_price_update
        if ref is None or now - ref > stale_market:
            desde = ref.strftime("%d/%m/%Y") if ref else "nunca"
            checks.append({
                "level": "warn",
                "titulo": "Precio estancado: %s" % asset.name,
                "detalle": "Sin actualización de precio desde %s. Revisa el ticker (%s)."
                           % (desde, asset.ticker or "sin ticker"),
            })

    stale_manual = timedelta(days=settings.xray_stale_manual_days)
    manual_types = (AssetType.CUENTA, AssetType.OTRO)
    for asset in db.query(Asset).filter(Asset.asset_type.in_(manual_types)).all():
        if not asset.manual_value:
            continue
        ref = asset.last_price_update or asset.created_at
        if ref is None or now - ref > stale_manual:
            checks.append({
                "level": "info",
                "titulo": "Valor manual sin revisar: %s" % asset.name,
                "detalle": "Última revisión: %s. Edita el activo para refrescar la marca."
                           % (ref.strftime("%d/%m/%Y") if ref else "desconocida"),
            })

    if not checks:
        checks.append({
            "level": "ok",
            "titulo": "Sin avisos",
            "detalle": "Ninguna regla de riesgo activada con los umbrales actuales.",
        })
    return checks
