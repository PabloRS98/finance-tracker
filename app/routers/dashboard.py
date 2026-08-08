"""Vista principal: evolución de patrimonio, desglose por tipo de activo,
ingresos/gastos, presupuestos y pendientes de aprobar."""
import calendar
import os
import tempfile
from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse
from sqlalchemy import func
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from ..auth import verify_auth
from ..config import settings
from ..database import get_db
from ..flash import redirect_flash
from ..models import (
    AssetType,
    Category,
    NetWorthIntraday,
    NetWorthSnapshot,
    SnapshotSource,
    Transaction,
    TransactionStatus,
    TransactionType,
    utcnow,
)
from ..services import market_data
from ..services.history import benchmark_series, cagr_from_evolution, eur_usd_snapshot, portfolio_evolution
from ..services.portfolio import portfolio_totals
from ..services.recurring import sumar_meses
from ..services.scheduler import backup_database, compute_net_worth
from ..services.xray import invested_rows
from ..templating import templates

router = APIRouter(tags=["dashboard"], dependencies=[Depends(verify_auth)])

CERO = Decimal("0")

TYPE_LABELS = {
    AssetType.CUENTA: "Cuentas",
    AssetType.ACCION: "Inversión",
    AssetType.CRIPTO: "Cripto",
    AssetType.OTRO: "Inmuebles/Otro",
}


def series_mensuales(
    db: Session, meses: list[tuple[int, int]],
) -> tuple[list[Decimal], list[Decimal]]:
    """Ingresos y gastos confirmados de cada mes, en una sola consulta agrupada.

    Antes era una consulta por mes: seis viajes a la base para pintar una
    gráfica de seis puntos, y encima traían las filas enteras para acabar
    sumándolas en Python.

    Vive fuera de la vista para poder probarla: la suma de los meses es lo
    único de la portada que se puede comprobar contra un número exacto, y una
    optimización que cambia el resultado no es una optimización.
    """
    if not meses:
        return [], []

    primer_mes = date(meses[0][0], meses[0][1], 1)
    ultimo_y, ultimo_m = meses[-1]
    fin_rango = date(ultimo_y + 1, 1, 1) if ultimo_m == 12 else date(ultimo_y, ultimo_m + 1, 1)

    agregados = {
        (fila.mes, fila.tipo): fila.total
        for fila in db.query(
            func.strftime("%Y-%m", Transaction.date).label("mes"),
            Transaction.type.label("tipo"),
            func.sum(Transaction.amount).label("total"),
        )
        .filter(
            Transaction.date >= primer_mes, Transaction.date < fin_rango,
            # Una pendiente todavía no ha ocurrido: no puede entrar en la serie.
            Transaction.status == TransactionStatus.CONFIRMADO,
        )
        .group_by("mes", Transaction.type)
        .all()
    }

    ingresos, gastos = [], []
    for y, m in meses:
        clave = "%04d-%02d" % (y, m)
        ingresos.append(round(Decimal(agregados.get((clave, TransactionType.INGRESO), 0) or 0), 2))
        gastos.append(round(Decimal(agregados.get((clave, TransactionType.GASTO), 0) or 0), 2))
    return ingresos, gastos


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    valuation = compute_net_worth(db)
    invested = portfolio_totals(db)

    # Evolución híbrida: parte invertida reconstruida desde operaciones + precios
    # históricos; parte manual desde snapshots. Benchmarks para el modo comparación.
    evolution = portfolio_evolution(db)
    benchmarks = benchmark_series(db)
    cagr = cagr_from_evolution(evolution)

    # Muestras intradía (últimas 24 h) para el rango 1D; timestamps en UTC con
    # "Z" explícita para que el navegador las pase a hora local
    intraday = [
        {"ts": r.ts.isoformat() + "Z", "total": r.total_value, "invertido": r.invested_value}
        for r in db.query(NetWorthIntraday)
        .filter(NetWorthIntraday.ts >= utcnow() - timedelta(hours=24))
        .order_by(NetWorthIntraday.ts)
        .all()
    ]

    # Desglose por tipo de activo. Sale de la valoración que ya se hizo arriba:
    # antes esto recorría los activos por cuarta vez y volvía a pedir un tipo de
    # cambio por cada uno, calculando exactamente lo mismo.
    breakdown: dict[str, float] = {}
    for tipo, valor in valuation.por_tipo.items():
        label = TYPE_LABELS.get(tipo, "Otro")
        breakdown[label] = breakdown.get(label, 0.0) + valor
    breakdown_items = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)

    # Mapa de la cartera invertida: superficie = peso, color = variación del día.
    # Reutiliza invested_rows, que ya valora cada posición en la moneda base y
    # deja fuera las que no se pueden convertir.
    heatmap = sorted(
        (
            {
                # El id va para que cada pieza del mapa ampliado enlace a su
                # ficha: mirar el mapa y querer abrir lo que se está mirando es
                # el gesto siguiente.
                "id": r["asset"].id,
                "nombre": r["asset"].name,
                "ticker": r["asset"].ticker or "",
                "valor": round(r["value_base"], 2),
                "variacion": r["summary"]["day_change_pct"],
            }
            for r in invested_rows(db)
        ),
        key=lambda r: r["valor"], reverse=True,
    )

    today = date.today()
    first_of_month = today.replace(day=1)
    month_txs = (
        db.query(Transaction)
        .filter(Transaction.date >= first_of_month, Transaction.status == TransactionStatus.CONFIRMADO)
        .all()
    )
    # Los importes son Decimal: los acumuladores arrancan en Decimal("0"), no en
    # 0.0, para no mezclar tipos (y para que las sumas sean exactas al céntimo).
    gastos_mes = sum((t.amount for t in month_txs if t.type == TransactionType.GASTO), CERO)
    ingresos_mes = sum((t.amount for t in month_txs if t.type == TransactionType.INGRESO), CERO)

    gastos_por_categoria: dict[str, Decimal] = {}
    for t in month_txs:
        if t.type != TransactionType.GASTO:
            continue
        nombre = t.category.name if t.category else "Sin categoría"
        gastos_por_categoria[nombre] = gastos_por_categoria.get(nombre, CERO) + t.amount

    # Ingresos vs gastos de los últimos 6 meses
    # `sumar_meses` de services/recurring.py hace exactamente esta cuenta, y
    # tenerla dos veces es tenerla dos veces mal el día que alguien arregle una.
    meses_rango = [sumar_meses(today.year, today.month, -i) for i in range(5, -1, -1)]
    ingresos_serie, gastos_serie = series_mensuales(db, meses_rango)
    meses_labels = ["%s %s" % (calendar.month_abbr[m], y) for y, m in meses_rango]

    presupuestos = []
    if settings.budgets_enabled:
        for cat in db.query(Category).filter(Category.budget_limit.isnot(None)).all():
            gastado = gastos_por_categoria.get(cat.name, CERO)
            porcentaje = float(100 * gastado / cat.budget_limit) if cat.budget_limit else 0.0
            porcentaje = round(porcentaje, 1)
            presupuestos.append({
                "categoria": cat.name,
                "limite": cat.budget_limit,
                "gastado": gastado,
                # Dos valores porque son dos cosas: lo que mide la barra (que
                # no puede pasarse de su ancho) y lo que se ha gastado de
                # verdad (que sí puede pasar del 100 %, y es justo lo que hay
                # que ver). Antes se llamaban `porcentaje` y `porcentaje_real`,
                # y había que mirar la plantilla para saber cuál era cuál.
                "porcentaje_barra": min(100, porcentaje),
                "porcentaje": porcentaje,
            })

    pendientes = (
        db.query(Transaction)
        .filter(Transaction.status == TransactionStatus.PENDIENTE)
        .order_by(Transaction.date.desc())
        .all()
    )
    categories = db.query(Category).order_by(Category.name).all()

    return templates.TemplateResponse(request, "dashboard.html", {
            "net_worth_now": round(valuation.total, 2),
            # Divisas sin tipo de cambio: sus activos no están en el total, y la
            # plantilla lo avisa en vez de presentar una cifra parcial como firme
            "missing_fx": sorted(valuation.missing),
            "invested": invested,
            "cagr": cagr,
            "backup_keep": settings.backup_keep,
            "base_currency": settings.base_currency,
            "evolution": evolution,
            "intraday": intraday,
            "benchmarks": benchmarks,
            "fx_eurusd": eur_usd_snapshot(db),
            "breakdown_items": breakdown_items,
            "heatmap": heatmap,
            "gastos_mes": round(gastos_mes, 2),
            "ingresos_mes": round(ingresos_mes, 2),
            "balance_mes": round(ingresos_mes - gastos_mes, 2),
            "gastos_por_categoria": gastos_por_categoria,
            "meses_labels": meses_labels,
            "ingresos_serie": ingresos_serie,
            "gastos_serie": gastos_serie,
            "presupuestos": presupuestos,
            "pendientes": pendientes,
            "categories": categories,
            "hoy": today.isoformat(),
        },
    )


@router.get("/fx")
def fx_rate():
    """Tipo de cambio actual USD->base para el toggle de divisa del frontend."""
    return {
        "usd_to_base": market_data.get_exchange_rate("USD", settings.base_currency),
        "base": settings.base_currency,
    }


@router.get("/patrimonio/backup")
def download_backup():
    """Descarga un backup fresco de la BD (copia consistente vía API de SQLite).

    El destino es un temporal único y se borra en cuanto la respuesta se ha
    enviado entera. Antes era la ruta fija `/tmp/finance-backup.db`, con tres
    problemas: `/tmp` no existe fuera de Linux; dos descargas a la vez se
    pisaban el fichero y `sqlite3.backup()` sobre uno que se está leyendo da una
    copia corrupta sin ningún error —el peor fallo posible en un backup—; y la
    copia, que lleva el patrimonio entero, se quedaba ahí para siempre.

    `BackgroundTask` corre DESPUÉS de mandar la respuesta completa, que es justo
    lo que hace falta: borrarlo antes cortaría la descarga.

    La rotación sigue sin aplicarse aquí, y es lo correcto: `backup_database`
    solo rota dentro de un directorio llamado `backups`, y este es el temporal
    del sistema. Descargar una copia no puede llevarse por delante las diarias.
    """
    fd, path = tempfile.mkstemp(prefix="finance-backup-", suffix=".db")
    os.close(fd)
    backup_database(path)
    return FileResponse(
        path, media_type="application/octet-stream",
        filename="finance-backup-%s.db" % date.today().isoformat(),
        background=BackgroundTask(os.unlink, path),
    )


@router.post("/patrimonio/snapshot-manual")
def add_manual_snapshot(fecha: date = Form(...), valor: float = Form(...), db: Session = Depends(get_db)):
    """Permite añadir un punto histórico manual a la gráfica de evolución de patrimonio."""
    existing = db.query(NetWorthSnapshot).filter(NetWorthSnapshot.date == fecha).first()
    if existing:
        existing.total_value = valor
        existing.source = SnapshotSource.MANUAL
    else:
        db.add(NetWorthSnapshot(date=fecha, total_value=valor, source=SnapshotSource.MANUAL))
    db.commit()
    return redirect_flash("/", "Punto histórico guardado (%s)" % fecha.isoformat())
