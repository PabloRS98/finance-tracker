"""CRUD de activos (cuentas, inversiones, cripto, inmuebles), con edición y precios."""
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session, selectinload

from ..auth import verify_auth
from ..config import settings
from ..database import get_db
from ..flash import redirect_flash
from ..forms import OptFloat
from ..models import (
    CURRENCY_CODES,
    Alerta,
    Asset,
    AssetType,
    Currency,
    TipoAlerta,
    Watchlist,
    currency_from_code,
)
from ..services import classify, fusion, market_data, telegram
from ..services.history import _symbol_series
from ..services.portfolio import (
    FxLookup,
    asset_summary,
    exposure_fx_lookup,
    fx_lookup,
    posicion_cerrada,
)
from ..templating import templates

router = APIRouter(prefix="/activos", tags=["activos"], dependencies=[Depends(verify_auth)])

INVERTIBLE = (AssetType.ACCION, AssetType.CRIPTO)

# Orden y etiqueta de las secciones de la lista de activos
TYPE_SECTIONS = [
    (AssetType.ACCION, "Inversión", "trending-up"),
    (AssetType.CRIPTO, "Cripto", "coins"),
    (AssetType.CUENTA, "Cuentas", "banknote"),
    (AssetType.OTRO, "Inmuebles / Otro", "tag"),
]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _fetch_price(asset: Asset) -> bool:
    """Actualiza el precio de mercado del activo. Devuelve True si lo consiguió."""
    if asset.asset_type == AssetType.ACCION and asset.ticker:
        result = market_data.get_stock_price(asset.ticker)
        if result:
            asset.current_price = result["price"]
            asset.previous_close = result["previous_close"]
            detected = currency_from_code(result["currency"])
            if detected is not None:
                asset.currency = detected
            classify.autofill(asset, result)
            if result.get("name") and market_data.name_is_placeholder(asset):
                asset.name = result["name"]
            asset.last_price_update = _utcnow()
            return True
    elif asset.asset_type == AssetType.CRIPTO and asset.ticker:
        result = market_data.get_crypto_price(asset.ticker, asset.currency.value.lower())
        if result is not None:
            asset.current_price, asset.previous_close = result
            if market_data.name_is_placeholder(asset):
                asset.name = market_data.get_crypto_name(asset.ticker) or asset.name
            asset.last_price_update = _utcnow()
            return True
    return False


def _fx_for(db: Session, asset: Asset, cache: dict[str, FxLookup | None]) -> FxLookup | None:
    """Lookup FX divisa-del-activo->base (para el efecto divisa), uno por divisa."""
    currency = asset.currency.value
    if currency not in cache:
        cache[currency] = fx_lookup(db, currency)
    return cache[currency]


def _row_for(asset: Asset, fx_on: FxLookup | None = None, exposure_fx: FxLookup | None = None) -> dict:
    """Fila de un activo: valor en su divisa y en la base, más el resumen de la
    posición (cantidad/coste medio/P&L/efecto divisa) si es invertible."""
    rate = market_data.get_exchange_rate(asset.currency.value, settings.base_currency)
    summary = asset_summary(asset, fx_on, exposure_fx) if asset.asset_type in INVERTIBLE else None
    # El valor sale del resumen cuando lo hay: `asset_summary` ya valoró la
    # posición, y volver a pedírselo al modelo repetía `compute_position` —que
    # además ordena la lista de operaciones— una vez más por activo. Para los no
    # invertibles no hay resumen y el valor es el manual, sin cálculo detrás.
    value = (summary["value"] or 0.0) if summary is not None else asset.current_value()
    # value_base None = sin tipo de cambio; el filtro `dinero` lo pinta como "-"
    return {
        "asset": asset, "value": value, "summary": summary,
        "value_base": value * rate if rate is not None else None,
    }


@router.get("")
def list_assets(request: Request, db: Session = Depends(get_db)):
    # selectinload: _row_for -> asset_summary recorre asset.operations de cada activo
    assets = db.query(Asset).options(selectinload(Asset.operations)).order_by(Asset.name).all()

    # Agrupado por tipo (Inversión / Cripto / Cuentas / Inmuebles), cada sección
    # con su subtotal; dentro, los activos ordenados por valor descendente.
    #
    # Las posiciones cerradas salen aparte: valen 0 y no aportaban nada a los
    # subtotales, pero ocupaban sitio en medio de lo que sí tienes. Se conservan
    # enteras —su historial y su P&L realizado sostienen la rentabilidad— solo
    # que plegadas al final. Ninguna cifra cambia por moverlas.
    by_type: dict[AssetType, list[dict]] = {}
    cerradas: list[dict] = []
    fx_cache: dict[str, FxLookup | None] = {}
    total_base = 0.0
    for a in assets:
        row = _row_for(a, _fx_for(db, a, fx_cache), exposure_fx_lookup(db, a, fx_cache))
        if row["summary"] is not None and posicion_cerrada(row["summary"]["position"]):
            ops = [o.date for o in a.operations]
            row["cerrada_el"] = max(ops) if ops else None
            cerradas.append(row)
            continue
        total_base += row["value_base"] or 0.0
        by_type.setdefault(a.asset_type, []).append(row)

    # Las más recientes primero: es el orden en el que uno las busca.
    cerradas.sort(key=lambda r: r["cerrada_el"] or date.min, reverse=True)

    groups = []
    for atype, label, ic in TYPE_SECTIONS:
        rows = sorted(by_type.get(atype, []), key=lambda r: r["value_base"] or 0.0, reverse=True)
        if rows:
            groups.append({
                "label": label, "icon": ic, "rows": rows,
                "subtotal": sum(r["value_base"] or 0.0 for r in rows),
            })

    seguidos = db.query(Watchlist).order_by(Watchlist.name).all()

    return templates.TemplateResponse(request, "assets.html", {
            "groups": groups,
            "cerradas": cerradas,
            "total_base": total_base,
            "base_currency": settings.base_currency,
            "asset_types": list(AssetType),
            "currencies": list(Currency),
            "seguidos": seguidos,
        },
    )


@router.get("/buscar")
def search_ticker(q: str = "", tipo: str = "accion"):
    """Autocompletado del campo ticker en el alta de activos. `tipo` decide la
    fuente: Yahoo (acciones/ETFs/fondos) o CoinGecko (cripto).
    Definida antes de /{asset_id} para que "buscar" no se intente parsear como id."""
    q = q.strip()
    if len(q) < 2:
        return []
    if tipo == "criptomoneda":
        return market_data.search_crypto(q)
    return market_data.search_symbols(q)


@router.get("/duplicados")
def list_duplicates(request: Request, db: Session = Depends(get_db)):
    """Activos que parecen el mismo valor comprado en dos sitios.

    Definida antes de /{asset_id} para que "duplicados" no se intente parsear
    como id, igual que /buscar."""
    return templates.TemplateResponse(request, "duplicados.html", {
        "grupos": fusion.candidatos(db),
        "base_currency": settings.base_currency,
    })


@router.get("/{asset_id}/intradia")
def asset_intraday(asset_id: int, db: Session = Depends(get_db)):
    """Curva intradía del activo para el rango 1D de la ficha (JSON, on-demand,
    sin almacenar). Nunca 500: si la API falla o el activo no cotiza intradía,
    devuelve puntos vacíos y el frontend hace fallback."""
    asset = db.get(Asset, asset_id)
    points: list[tuple] = []
    if asset and asset.ticker:
        if asset.asset_type == AssetType.ACCION:
            points = market_data.get_stock_intraday(asset.ticker)
        elif asset.asset_type == AssetType.CRIPTO:
            points = market_data.get_crypto_intraday(asset.ticker, asset.currency.value)
    return {
        "currency": asset.currency.value if asset else None,
        "points": [{"ts": dt.isoformat(), "precio": round(p, 6)} for dt, p in points],
    }


@router.get("/{asset_id}")
def asset_detail(asset_id: int, request: Request, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        return redirect_flash("/activos", "El activo ya no existe", "error")

    rate = market_data.get_exchange_rate(asset.currency.value, settings.base_currency)
    summary = (
        asset_summary(asset, fx_lookup(db, asset.currency.value), exposure_fx_lookup(db, asset, {}))
        if asset.asset_type in INVERTIBLE else None
    )
    operations = sorted(asset.operations, key=lambda o: (o.date, o.id or 0), reverse=True)

    # Precio secundario: el actual convertido a la otra divisa (base si el activo
    # es extranjero; USD si ya cotiza en base). FX con caché de 1 h.
    secondary = None
    if asset.current_price is not None and asset.asset_type in INVERTIBLE:
        sec_cur = settings.base_currency if asset.currency.value != settings.base_currency else "USD"
        sec_rate = rate if sec_cur == settings.base_currency else market_data.get_exchange_rate(
            asset.currency.value, sec_cur,
        )
        if sec_rate is not None:
            secondary = {"currency": sec_cur, "price": asset.current_price * sec_rate}

    # Histórico de precios para la mini-gráfica (cierres cacheados, en la divisa
    # del activo). Solo hay serie si el activo tiene ticker.
    price_history = []
    if asset.ticker:
        series = _symbol_series(db, asset.ticker)
        price_history = [{"fecha": d.isoformat(), "precio": round(p, 4)} for d, p in sorted(series.items())]

    return templates.TemplateResponse(request, "asset_detail.html", {
            "asset": asset,
            "summary": summary,
            "value": asset.current_value(),
            "value_base": asset.current_value() * rate if rate is not None else None,
            "operations": operations,
            "por_cuenta": fusion.posicion_por_cuenta(asset),
            "alertas": db.query(Alerta).filter(Alerta.asset_id == asset.id).order_by(Alerta.id).all(),
            "tipos_alerta": list(TipoAlerta),
            "telegram_listo": telegram.is_configured(),
            "price_history": price_history,
            "secondary": secondary,
            "base_currency": settings.base_currency,
            "currencies": list(Currency),
        },
    )


def _classify_defaults(asset: Asset) -> None:
    """Clasificación automática al crear/editar (solo rellena huecos, ver classify.py)."""
    classify.autofill(asset)


@router.post("")
def create_asset(
    name: str = Form(...),
    asset_type: AssetType = Form(...),
    currency: Currency = Form(Currency.EUR),
    ticker: str = Form(""),
    quantity: OptFloat = None,
    manual_value: OptFloat = None,
    avg_cost_override: OptFloat = None,
    region: str = Form(""),
    sector: str = Form(""),
    db: Session = Depends(get_db),
):
    asset = Asset(
        name=name,
        asset_type=asset_type,
        currency=currency,
        ticker=ticker.strip() or None,
        quantity=quantity,
        manual_value=manual_value,
        avg_cost_override=avg_cost_override,
        region=region.strip() or None,
        sector=sector.strip() or None,
    )
    _classify_defaults(asset)
    if manual_value is not None:
        asset.last_price_update = _utcnow()
    db.add(asset)
    db.commit()
    db.refresh(asset)

    # Intenta obtener el precio inicial al vuelo si es una acción o cripto
    fetched = _fetch_price(asset)
    if fetched:
        db.commit()

    if asset.asset_type in (AssetType.ACCION, AssetType.CRIPTO) and asset.ticker and not fetched:
        return redirect_flash(
            "/activos",
            '"%s" añadido, pero no se pudo obtener su precio (revisa el ticker/ID)' % asset.name,
            "error",
        )
    return redirect_flash("/activos", 'Activo "%s" añadido' % asset.name)


@router.post("/{asset_id}/editar")
def edit_asset(
    asset_id: int,
    name: str = Form(...),
    asset_type: AssetType = Form(...),
    currency: Currency = Form(Currency.EUR),
    ticker: str = Form(""),
    quantity: OptFloat = None,
    manual_value: OptFloat = None,
    avg_cost_override: OptFloat = None,
    region: str = Form(""),
    sector: str = Form(""),
    exposure_currency: str | None = Form(None),
    db: Session = Depends(get_db),
):
    asset = db.get(Asset, asset_id)
    if not asset:
        return redirect_flash("/activos", "El activo ya no existe", "error")

    ticker_changed = (ticker.strip() or None) != asset.ticker or asset_type != asset.asset_type
    manual_changed = manual_value != asset.manual_value
    asset.name = name
    asset.asset_type = asset_type
    asset.currency = currency
    asset.ticker = ticker.strip() or None
    asset.quantity = quantity
    asset.manual_value = manual_value
    asset.avg_cost_override = avg_cost_override
    asset.region = region.strip() or None
    asset.sector = sector.strip() or None
    if exposure_currency is not None:  # ausente (form sin el campo) = conservar
        exp = exposure_currency.strip().upper()
        asset.exposure_currency = exp if exp in CURRENCY_CODES and exp != settings.base_currency else None
    _classify_defaults(asset)
    if ticker_changed:
        asset.current_price = None
        asset.last_price_update = None
        _fetch_price(asset)
    elif manual_changed and manual_value is not None:
        # Marca de revisión del valor manual (la usa la regla de estancados del X-Ray)
        asset.last_price_update = _utcnow()
    db.commit()
    return redirect_flash("/activos", 'Activo "%s" actualizado' % asset.name)


@router.post("/{asset_id}/eliminar")
def delete_asset(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if asset:
        db.delete(asset)
        db.commit()
    return redirect_flash("/activos", "Activo eliminado", "info")


@router.post("/{asset_id}/actualizar-precio")
def refresh_asset_price(asset_id: int, db: Session = Depends(get_db)):
    asset = db.get(Asset, asset_id)
    if not asset:
        return redirect_flash("/activos", "El activo ya no existe", "error")
    if _fetch_price(asset):
        db.commit()
        return redirect_flash(
            "/activos",
            'Precio de "%s" actualizado: %.2f %s' % (asset.name, asset.current_price, asset.currency.value),
        )
    return redirect_flash("/activos", 'No se pudo actualizar el precio de "%s"' % asset.name, "error")


# ---------- Valores en seguimiento (watchlist) ----------

@router.post("/seguimiento")
def add_to_watchlist(
    ticker: str = Form(...),
    asset_type: AssetType = Form(AssetType.ACCION),
    db: Session = Depends(get_db),
):
    """Empieza a seguir un valor sin tenerlo en cartera.

    Se resuelve contra la API antes de guardarlo, igual que los benchmarks: un
    ticker mal escrito se quedaría en la lista sin precio y sin explicación."""
    ticker = ticker.strip()
    if not ticker:
        return redirect_flash("/activos", "Indica el ticker a seguir", "error")
    if asset_type not in INVERTIBLE:
        return redirect_flash("/activos", "Solo se pueden seguir acciones/ETFs y cripto", "error")
    if db.query(Watchlist).filter(Watchlist.ticker == ticker).first():
        return redirect_flash("/activos", 'Ya sigues "%s"' % ticker, "error")

    item = Watchlist(ticker=ticker, name=ticker, asset_type=asset_type)
    if asset_type == AssetType.CRIPTO:
        precio = market_data.get_crypto_price(ticker, settings.base_currency.lower())
        if precio is None:
            return redirect_flash("/activos", 'No se reconoce la cripto "%s"' % ticker, "error")
        item.currency = Currency(settings.base_currency)
        item.current_price, item.previous_close = precio
        item.name = market_data.get_crypto_name(ticker) or ticker
    else:
        cotizacion = market_data.get_stock_price(ticker)
        if not cotizacion:
            return redirect_flash("/activos", 'No se reconoce el ticker "%s"' % ticker, "error")
        detectada = currency_from_code(cotizacion["currency"])
        if detectada is not None:
            item.currency = detectada
        item.current_price = cotizacion["price"]
        item.previous_close = cotizacion["previous_close"]
        item.name = cotizacion.get("name") or ticker
    item.last_price_update = _utcnow()

    db.add(item)
    db.commit()
    return redirect_flash("/activos", 'Siguiendo "%s"' % item.name)


@router.post("/seguimiento/{item_id}/eliminar")
def remove_from_watchlist(item_id: int, db: Session = Depends(get_db)):
    item = db.get(Watchlist, item_id)
    if item:
        db.delete(item)
        db.commit()
    return redirect_flash("/activos", "Ya no se sigue", "info")


@router.post("/seguimiento/{item_id}/comprar")
def watchlist_to_portfolio(item_id: int, db: Session = Depends(get_db)):
    """Pasa un valor seguido a la cartera y lleva al alta de la operación.

    El activo se crea sin cantidad: la posición sale de las operaciones, y la
    primera se registra en la pantalla a la que redirige, ya filtrada por él."""
    item = db.get(Watchlist, item_id)
    if not item:
        return redirect_flash("/activos", "Ese valor ya no está en seguimiento", "error")

    existente = db.query(Asset).filter(Asset.ticker == item.ticker).first()
    if existente is not None:
        db.delete(item)
        db.commit()
        return redirect_flash(
            "/operaciones?activo=%d" % existente.id,
            '"%s" ya estaba en cartera: registra la operación' % existente.name,
        )

    asset = Asset(
        name=item.name,
        asset_type=item.asset_type,
        currency=item.currency,
        ticker=item.ticker,
        current_price=item.current_price,
        previous_close=item.previous_close,
        last_price_update=item.last_price_update,
    )
    classify.autofill(asset)
    db.add(asset)
    db.delete(item)
    db.commit()
    db.refresh(asset)
    return redirect_flash(
        "/operaciones?activo=%d" % asset.id,
        '"%s" pasa a cartera: registra la compra' % asset.name,
    )


# ---------- Fusión de activos duplicados ----------

@router.post("/duplicados/fusionar")
def merge_assets(
    destino_id: int = Form(...),
    # default_factory, no una lista literal: hoy FastAPI construye una lista
    # nueva por petición, así que no hay bug, pero es el patrón que se convierte
    # en uno muy difícil de diagnosticar en cuanto alguien copia la firma a una
    # función normal —los datos de una petición aparecen en la siguiente—.
    origen_ids: list[int] = Form(default_factory=list),
    db: Session = Depends(get_db),
):
    destino = db.get(Asset, destino_id)
    if not destino:
        return redirect_flash("/activos/duplicados", "El activo destino ya no existe", "error")

    origenes = [a for a in (db.get(Asset, i) for i in origen_ids) if a is not None]
    motivo = fusion.puede_fusionar(destino, origenes)
    if motivo:
        return redirect_flash("/activos/duplicados", motivo, "error")

    resumen = fusion.fusionar(db, destino, origenes)
    return redirect_flash(
        "/activos/%d" % destino.id,
        "%d operaciones pasan a \"%s\" (absorbe %s)"
        % (resumen["movidas"], destino.name, ", ".join(resumen["absorbidos"])),
    )


# ---------- Alertas de precio ----------

@router.post("/{asset_id}/alertas")
def create_alert(
    asset_id: int,
    tipo: TipoAlerta = Form(...),
    valor: float = Form(...),
    db: Session = Depends(get_db),
):
    asset = db.get(Asset, asset_id)
    if not asset:
        return redirect_flash("/activos", "El activo ya no existe", "error")
    if valor <= 0:
        return redirect_flash(
            "/activos/%d" % asset_id,
            "El precio objetivo y el porcentaje de caída tienen que ser mayores que 0",
            "error",
        )

    db.add(Alerta(asset_id=asset_id, tipo=tipo, valor=valor))
    db.commit()
    return redirect_flash("/activos/%d" % asset_id, "Alerta creada")


@router.post("/alertas/{alerta_id}/eliminar")
def delete_alert(alerta_id: int, db: Session = Depends(get_db)):
    alerta = db.get(Alerta, alerta_id)
    if not alerta:
        return redirect_flash("/activos", "Esa alerta ya no existe", "error")
    destino = alerta.asset_id
    db.delete(alerta)
    db.commit()
    return redirect_flash("/activos/%d" % destino, "Alerta eliminada", "info")
