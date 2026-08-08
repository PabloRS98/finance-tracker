"""Importación de operaciones desde CSV de brokers: subida, preview y confirmación.

Flujo sin estado en servidor: el preview serializa cada fila parseada como JSON
en un campo oculto del formulario; al confirmar se re-validan las filas y los
duplicados (import_hash) antes de crear nada."""
import json
from datetime import UTC, datetime
from datetime import date as date_cls

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import get_db
from ..flash import redirect_flash
from ..forms import OptInt
from ..models import CURRENCY_CODES, Account, Asset, AssetType, Currency, Operation, OperationType
from ..services import market_data
from ..services.importers import IMPORTERS, ParsedRow
from ..templating import templates
from ..uploads import MAX_CSV_BYTES, MAX_PDF_BYTES, leer_limitado

router = APIRouter(prefix="/operaciones/importar", tags=["importar"], dependencies=[Depends(verify_auth)])

INVERTIBLE = (AssetType.ACCION, AssetType.CRIPTO)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _price_asset(asset: Asset) -> str:
    """Da precio de mercado a un activo recién importado, resolviendo el ticker por
    ISIN si hace falta. Solo acepta cotizaciones en la MISMA divisa que las
    operaciones, para no corromper el coste base (Trade Republic registra en EUR;
    aceptar un ticker USD fliparía la divisa del activo y desvirtuaría el P&L).
    Devuelve 'live' si consiguió precio de mercado, 'cost' si queda valorado a coste,
    '' si no cuenta (sin operaciones ni precio)."""
    if asset.asset_type not in INVERTIBLE:
        return ""
    want = asset.currency.value
    if asset.ticker:
        candidates = [asset.ticker]
    elif asset.isin and asset.asset_type == AssetType.ACCION:
        candidates = market_data.resolve_ticker_by_isin(asset.isin)
    else:
        candidates = []

    for sym in candidates[:4]:
        if asset.asset_type == AssetType.CRIPTO:
            res = market_data.get_crypto_price(sym, want.lower())
            if res is not None:
                asset.ticker = sym
                asset.current_price, asset.previous_close = res
                asset.last_price_update = _utcnow()
                return "live"
        else:
            res = market_data.get_stock_price(sym)
            if res and res["currency"] == want:
                asset.ticker = sym
                asset.current_price = res["price"]
                asset.previous_close = res["previous_close"]
                if asset.region is None and res.get("instrument_type") == "EQUITY":
                    asset.region = market_data.region_for_exchange(res["exchange"])
                if res.get("name") and market_data.name_is_placeholder(asset):
                    asset.name = res["name"]
                asset.last_price_update = _utcnow()
                return "live"
    return "cost" if asset.effective_price() is not None else ""


def _existing_hashes(db: Session) -> set[str]:
    return {h for (h,) in db.query(Operation.import_hash).filter(Operation.import_hash.isnot(None)).all()}


import re

# Patrones que se eliminan al normalizar nombres para fuzzy matching
_RE_SUFFIX = re.compile(
    r'\b(Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|'
    r'SA\.?|S\.A\.|AG|GmbH|SE|NV|PLC|LLC|LP|'
    r'\([A-Z]\)|Class [A-Z])\b', re.IGNORECASE
)
_RE_JUNK = re.compile(r'[,.()]+')

def _normalize_name(name: str) -> str:
    """Normaliza un nombre de activo para comparación fuzzy:
    quita sufijos legales, clases de acción y puntuación."""
    name = _RE_SUFFIX.sub('', name)
    name = _RE_JUNK.sub(' ', name)
    return ' '.join(name.upper().split())


def _match_asset(row: ParsedRow, assets: list[Asset]) -> Asset | None:
    """Casa una fila con un activo existente: ISIN → ticker → nombre exacto → nombre fuzzy."""
    # 1. ISIN exacto
    if row.isin:
        for a in assets:
            if a.isin and a.isin.upper() == row.isin.upper():
                return a
    # 2. Ticker exacto
    if row.ticker:
        for a in assets:
            if a.ticker and a.ticker.upper() == row.ticker.upper():
                return a
    # 3. Nombre exacto
    if row.name:
        rn = row.name.upper().strip()
        for a in assets:
            if a.name.upper().strip() == rn:
                return a
    # 4. Fuzzy: nombres normalizados
    if row.name:
        rn_norm = _normalize_name(row.name)
        if rn_norm:
            for a in assets:
                if _normalize_name(a.name) == rn_norm:
                    return a
    return None


def _conflicto_de_divisa(row: ParsedRow, asset: Asset | None) -> str | None:
    """Motivo por el que la fila NO puede colgarse del activo con el que casa.

    Las operaciones no guardan divisa: heredan la del activo. Así que meter una
    fila en euros en un activo que cotiza en dólares no convierte nada, solo
    reinterpreta el precio, y el coste medio pasa a mezclar dos divisas sin
    dejar rastro. Es fácil de provocar sin querer: el extracto de Trade Republic
    va siempre en euros y el mismo valor puede existir ya, comprado en dólares
    desde otro bróker. Se rechaza la fila y se explica el motivo, en vez de
    corromper el coste base en silencio."""
    if asset is None or asset.currency.value == row.currency:
        return None
    return 'el activo "%s" está en %s y la fila viene en %s' % (
        asset.name, asset.currency.value, row.currency,
    )


def _row_payload(row: ParsedRow) -> str:
    return json.dumps({
        "d": row.date.isoformat() if row.date else None,
        "t": row.type, "n": row.name, "tk": row.ticker, "i": row.isin,
        "q": row.quantity, "p": row.unit_price, "f": row.fee,
        "c": row.currency, "k": row.kind,
    })


def _row_from_payload(payload: str) -> ParsedRow | None:
    try:
        data = json.loads(payload)
        row = ParsedRow(
            type=data["t"], name=data.get("n") or "", ticker=data.get("tk"),
            isin=data.get("i"), quantity=float(data["q"]), unit_price=float(data["p"]),
            fee=float(data.get("f") or 0.0), currency=data.get("c") or "EUR",
            kind=data.get("k") or "accion",
        )
        row.date = date_cls.fromisoformat(data["d"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if row.type not in ("compra", "venta") or row.quantity <= 0 or row.unit_price < 0:
        return None
    if row.currency not in CURRENCY_CODES:
        return None
    return row


@router.get("")
def import_page(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(request, "import_ops.html", {
        "importers": IMPORTERS,
        "accounts": db.query(Account).order_by(Account.name).all(),
        "preview": None,
    })


@router.post("/preview")
async def import_preview(
    request: Request,
    archivo: UploadFile = File(...),
    formato: str = Form(...),
    account_id: OptInt = None,
    db: Session = Depends(get_db),
):
    importer = IMPORTERS.get(formato)
    if not importer:
        return redirect_flash("/operaciones/importar", "Formato de importación desconocido", "error")

    # Lectura acotada: sin tope, arrastrar el fichero equivocado tumba el
    # proceso por memoria en vez de fallar de forma controlada. Se lee con el
    # límite de CSV y después, si resulta ser un PDF, se aplica el suyo, que es
    # más bajo: estos bytes van a fitz.open(), que con un PDF malformado o con
    # bombas de descompresión consume mucha más memoria que el fichero original.
    raw_bytes = await leer_limitado(archivo, MAX_CSV_BYTES)
    filename = (archivo.filename or "").lower()

    es_pdf = filename.endswith(".pdf") or raw_bytes[:4] == b"%PDF"
    if es_pdf and len(raw_bytes) > MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail="El PDF supera el límite de %d MB" % (MAX_PDF_BYTES // (1024 * 1024)),
        )

    # Si es PDF, extraer texto con pymupdf
    if filename.endswith(".pdf") or raw_bytes[:4] == b"%PDF":
        try:
            import fitz
            doc = fitz.open(stream=raw_bytes, filetype="pdf")
            text = ""
            for page in doc:
                text += page.get_text() + "\n"
            doc.close()
        except Exception:
            return redirect_flash("/operaciones/importar", "No se pudo leer el PDF. ¿Está corrupto?", "error")
    else:
        text = raw_bytes.decode("utf-8-sig", errors="ignore")

    result = importer["parse"](text)
    if result.error:
        return redirect_flash("/operaciones/importar", result.error, "error")

    assets = db.query(Asset).filter(Asset.asset_type.in_(INVERTIBLE)).all()
    existing = _existing_hashes(db)
    seen_in_file: set[str] = set()

    items = []
    for row in result.rows:
        status, status_kind = "nueva", "ok"
        matched = None
        if row.error:
            status, status_kind = row.error, "error"
        else:
            matched = _match_asset(row, assets)
            conflicto = _conflicto_de_divisa(row, matched)
            h = row.import_hash()
            if h in existing or h in seen_in_file:
                status, status_kind = "ya importada", "dup"
            elif conflicto:
                status, status_kind = conflicto, "error"
            seen_in_file.add(h)
        items.append({
            "row": row,
            # Sin payload no hay campo oculto que enviar: las filas en error no
            # se pueden confirmar ni manipulando el formulario.
            "payload": _row_payload(row) if status_kind != "error" else None,
            "asset": matched,
            "status": status,
            "status_kind": status_kind,  # ok | dup | error
        })

    return templates.TemplateResponse(request, "import_ops.html", {
        "importers": IMPORTERS,
        "accounts": db.query(Account).order_by(Account.name).all(),
        "preview": {
            "filas": items,  # no llamarlo "items": colisiona con dict.items() en Jinja
            "skipped": result.skipped,
            "formato": formato,
            "formato_label": importer["label"],
            "account_id": account_id,
            "importables": sum(1 for it in items if it["status_kind"] == "ok"),
        },
    })


@router.post("/confirmar")
def import_confirm(
    account_id: OptInt = None,
    rows: list[str] = Form([]),
    db: Session = Depends(get_db),
):
    if not rows:
        return redirect_flash("/operaciones/importar", "No se seleccionó ninguna fila", "error")

    assets = db.query(Asset).filter(Asset.asset_type.in_(INVERTIBLE)).all()
    existing = _existing_hashes(db)

    creadas = duplicadas = invalidas = conflictivas = 0
    activos_nuevos = []
    touched: list[Asset] = []
    for payload in rows:
        row = _row_from_payload(payload)
        if row is None:
            invalidas += 1
            continue
        h = row.import_hash()
        if h in existing:
            duplicadas += 1
            continue

        asset = _match_asset(row, assets)
        # Se revalida aquí, no solo en el preview: el payload viaja en un campo
        # oculto del formulario y la cartera puede haber cambiado entre medias.
        if _conflicto_de_divisa(row, asset) is not None:
            conflictivas += 1
            continue
        existing.add(h)

        if asset is None:
            asset = Asset(
                name=row.name or row.ticker or row.isin or "Activo importado",
                asset_type=AssetType.CRIPTO if row.kind == "cripto" else AssetType.ACCION,
                currency=Currency(row.currency),
                ticker=row.ticker,
                isin=row.isin,
            )
            db.add(asset)
            db.flush()  # asigna id para poder colgarle la operación
            assets.append(asset)
            activos_nuevos.append(asset.name)

        db.add(Operation(
            asset_id=asset.id,
            account_id=account_id,
            type=OperationType(row.type),
            date=row.date,
            quantity=row.quantity,
            unit_price=row.unit_price,
            fee=row.fee,
            source="csv",
            import_hash=h,
        ))
        creadas += 1
        touched.append(asset)
    db.flush()  # las operaciones deben existir para el fallback de valoración a coste

    # Precio al vuelo de los activos afectados que aún no lo tienen (resolviendo el
    # ticker por ISIN si hace falta): así el patrimonio refleja la importación ya,
    # sin esperar al job periódico de precios.
    priced_live = priced_cost = 0
    seen: set[int] = set()
    for asset in touched:
        if id(asset) in seen or asset.current_price is not None:
            continue
        seen.add(id(asset))
        outcome = _price_asset(asset)
        if outcome == "live":
            priced_live += 1
        elif outcome == "cost":
            priced_cost += 1
    db.commit()

    if creadas == 0:
        aviso = "No se importó ninguna operación nueva"
        if conflictivas:
            aviso += ": %d con divisa distinta a la del activo con el que casan" % conflictivas
        return redirect_flash("/operaciones/importar", aviso, "error")
    msg = "%d operaciones importadas" % creadas
    if duplicadas:
        msg += ", %d duplicadas omitidas" % duplicadas
    if conflictivas:
        msg += ", %d omitidas por divisa distinta a la del activo" % conflictivas
    if invalidas:
        msg += ", %d filas inválidas" % invalidas
    if activos_nuevos:
        msg += ". Activos creados: %s" % ", ".join(activos_nuevos[:5])
    if priced_live:
        msg += ". %d con precio de mercado" % priced_live
    if priced_cost:
        msg += ". %d valorados a coste (asígnales ticker en Activos para su precio real)" % priced_cost
    return redirect_flash("/operaciones", msg)
