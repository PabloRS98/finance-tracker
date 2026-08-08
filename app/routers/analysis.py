"""Página de análisis: allocations (divisa/región/sector), comisiones y X-Ray de riesgos."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session, joinedload

from ..auth import verify_auth
from ..config import settings
from ..database import get_db
from ..flash import redirect_flash
from ..models import Asset, AssetType, Benchmark, Operation, PesoObjetivo, PriceHistory, TransactionStatus
from ..services import market_data, rebalanceo
from ..services.history import (
    benchmark_series,
    benchmarks_configurados,
    cagr_from_evolution,
    clave_de_simbolo,
    portfolio_evolution,
)
from ..services.rendimiento import rendimiento_por_ano, xirr_de_la_cartera
from ..services.xray import allocations, xray_checks
from ..templating import templates

router = APIRouter(prefix="/analisis", tags=["analisis"], dependencies=[Depends(verify_auth)])


def _fees_summary(db: Session) -> dict:
    """Comisiones pagadas en operaciones confirmadas, convertidas a la moneda base
    con el tipo de cambio actual (aproximación suficiente para un agregado)."""
    ops = (
        db.query(Operation)
        .options(joinedload(Operation.asset))  # evita N+1: abajo se lee op.asset.currency
        .filter(Operation.fee > 0, Operation.status == TransactionStatus.CONFIRMADO)
        .all()
    )
    total = 0.0
    by_year: dict[int, float] = {}
    counted = 0
    for op in ops:
        rate = market_data.get_exchange_rate(op.asset.currency.value, settings.base_currency)
        if rate is None:
            continue  # comisión no convertible: fuera del total en vez de contarla 1:1
        amount = (op.fee or 0.0) * rate
        total += amount
        by_year[op.date.year] = by_year.get(op.date.year, 0.0) + amount
        counted += 1
    return {
        "total": round(total, 2),
        "count": counted,
        "by_year": sorted(by_year.items()),
    }


@router.get("")
def analysis(request: Request, db: Session = Depends(get_db)):
    alloc = allocations(db)
    checks = xray_checks(db, alloc["rows"])
    warns = sum(1 for c in checks if c["level"] == "warn")

    # La serie de evolución alimenta las tres métricas: el TWR ya venía en ella,
    # y de las aportaciones acumuladas salen los flujos del XIRR.
    evolution = portfolio_evolution(db)
    return templates.TemplateResponse(request, "analysis.html", {
        "alloc": alloc,
        "checks": checks,
        "warns": warns,
        "fees": _fees_summary(db),
        "twr": evolution[-1]["twr"] if evolution else None,
        "cagr": cagr_from_evolution(evolution),
        "xirr": xirr_de_la_cartera(evolution),
        "anual": rendimiento_por_ano(evolution, benchmark_series(db)),
        "benchmarks": benchmarks_configurados(db),
        "base_currency": settings.base_currency,
        "umbrales": {
            "activo": settings.xray_max_asset_pct,
            "divisa": settings.xray_max_currency_pct,
            "precio_dias": settings.xray_stale_price_days,
            "manual_dias": settings.xray_stale_manual_days,
        },
    })


@router.post("/benchmarks")
def create_benchmark(
    symbol: str = Form(...),
    label: str = Form(""),
    db: Session = Depends(get_db),
):
    """Da de alta un índice de referencia por su símbolo de Yahoo.

    Se comprueba contra Yahoo antes de guardarlo: un símbolo que no existe se
    quedaría sin serie para siempre y aparecería como una columna vacía en la
    tabla anual, sin explicar por qué."""
    symbol = symbol.strip().upper()
    if not symbol:
        return redirect_flash("/analisis", "Indica el símbolo del índice", "error")
    if db.query(Benchmark).filter(Benchmark.symbol == symbol).first():
        return redirect_flash("/analisis", 'Ya sigues "%s"' % symbol, "error")

    cotizacion = market_data.get_stock_price(symbol)
    if not cotizacion:
        return redirect_flash(
            "/analisis",
            'Yahoo no reconoce "%s". Usa el símbolo exacto (^GSPC, IWDA.AS, ^IBEX...)' % symbol,
            "error",
        )

    nombre = label.strip() or cotizacion.get("name") or symbol
    db.add(Benchmark(clave=clave_de_simbolo(symbol), label=nombre[:60], symbol=symbol))
    db.commit()
    return redirect_flash(
        "/analisis",
        '"%s" añadido. Su histórico se descarga en el próximo repaso diario.' % nombre,
    )


@router.post("/benchmarks/{benchmark_id}/eliminar")
def delete_benchmark(benchmark_id: int, db: Session = Depends(get_db)):
    bench = db.get(Benchmark, benchmark_id)
    if not bench:
        return redirect_flash("/analisis", "Ese índice ya no existe", "error")
    # Los cierres descargados se borran con él: si no, quedarían ocupando sitio
    # sin que nada los lea, y al volver a añadirlo traerían datos rancios.
    db.query(PriceHistory).filter(PriceHistory.symbol == bench.symbol).delete()
    db.delete(bench)
    db.commit()
    return redirect_flash("/analisis", '"%s" ya no se sigue' % bench.label, "info")


# ---------- Rebalanceo ----------

@router.get("/rebalanceo")
def rebalance(request: Request, aportacion: float = 0.0, db: Session = Depends(get_db)):
    """Desviación frente a los pesos objetivo y qué comprar para corregirla."""
    # El plan se calcula UNA vez y se pasa al reparto: antes cada uno recorría
    # la cartera entera por su cuenta, calculando lo mismo dos veces.
    detalle = rebalanceo.plan(db, aportacion)
    return templates.TemplateResponse(request, "rebalanceo.html", {
        "detalle": detalle,
        "reparto": rebalanceo.reparto_de_aportacion(detalle, aportacion),
        "aportacion": aportacion,
        "invertibles": (
            db.query(Asset)
            .filter(Asset.asset_type.in_([AssetType.ACCION, AssetType.CRIPTO]))
            .order_by(Asset.name)
            .all()
        ),
        "base_currency": settings.base_currency,
    })


@router.post("/rebalanceo/objetivos")
def set_target(
    asset_id: int = Form(...),
    porcentaje: float = Form(...),
    db: Session = Depends(get_db),
):
    if not 0 < porcentaje <= 100:
        return redirect_flash("/analisis/rebalanceo", "El peso tiene que estar entre 0 y 100", "error")
    if not db.get(Asset, asset_id):
        return redirect_flash("/analisis/rebalanceo", "Ese activo ya no existe", "error")

    # Uno por activo: volver a fijarlo lo actualiza en vez de duplicarlo
    objetivo = db.query(PesoObjetivo).filter(PesoObjetivo.asset_id == asset_id).first()
    if objetivo:
        objetivo.porcentaje = porcentaje
    else:
        db.add(PesoObjetivo(asset_id=asset_id, porcentaje=porcentaje))
    db.commit()
    return redirect_flash("/analisis/rebalanceo", "Peso objetivo guardado")


@router.post("/rebalanceo/objetivos/{objetivo_id}/eliminar")
def delete_target(objetivo_id: int, db: Session = Depends(get_db)):
    objetivo = db.get(PesoObjetivo, objetivo_id)
    if objetivo:
        db.delete(objetivo)
        db.commit()
    return redirect_flash("/analisis/rebalanceo", "Peso objetivo eliminado", "info")
