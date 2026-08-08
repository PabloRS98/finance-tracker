"""Transacciones: alta manual, edición, paginación, exportación CSV,
importación CSV del banco, entrada por voz y gestión de pendientes."""
import csv
import io
from datetime import datetime, date as date_cls
from math import ceil

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..config import settings
from ..database import get_db
from ..flash import redirect_flash
from ..forms import OptInt
from ..models import Operation, OperationType, Transaction, TransactionType, TransactionStatus, Category
from ..services import market_data
from ..services.voice_parser import guess_category, parse_voice_operation, parse_voice_text
from ..templating import dinero, templates
from ..uploads import leer_texto_limitado

router = APIRouter(prefix="/transacciones", tags=["transacciones"], dependencies=[Depends(verify_auth)])

PER_PAGE = 50


def _month_bounds(mes: str) -> tuple[date_cls, date_cls]:
    year, month = map(int, mes.split("-"))
    start = date_cls(year, month, 1)
    end = date_cls(year + 1, 1, 1) if month == 12 else date_cls(year, month + 1, 1)
    return start, end


def _filtered_query(db: Session, mes: str | None):
    query = db.query(Transaction).order_by(Transaction.date.desc(), Transaction.id.desc())
    if mes:
        try:
            start, end = _month_bounds(mes)
        except ValueError:
            return query
        query = query.filter(Transaction.date >= start, Transaction.date < end)
    return query


@router.get("")
def list_transactions(
    request: Request,
    mes: str | None = None,
    pagina: int = 1,
    db: Session = Depends(get_db),
):
    query = _filtered_query(db, mes)
    total = query.count()
    total_paginas = max(1, ceil(total / PER_PAGE))
    pagina = min(max(1, pagina), total_paginas)
    transactions = query.offset((pagina - 1) * PER_PAGE).limit(PER_PAGE).all()

    pending = (
        db.query(Transaction)
        .filter(Transaction.status == TransactionStatus.PENDIENTE)
        .order_by(Transaction.date.desc())
        .all()
    )
    categories = db.query(Category).order_by(Category.name).all()
    return templates.TemplateResponse(request, "transactions.html", {
            "transactions": transactions,
            "pending": pending,
            "categories": categories,
            "tx_types": list(TransactionType),
            "mes": mes,
            "pagina": pagina,
            "total_paginas": total_paginas,
            "total": total,
            "hoy": date_cls.today().isoformat(),
        },
    )


@router.post("")
def create_transaction(
    date: date_cls = Form(...),
    type: TransactionType = Form(...),
    category_id: OptInt = None,
    amount: float = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    tx = Transaction(
        date=date,
        type=type,
        category_id=category_id,
        amount=amount,
        description=description,
        status=TransactionStatus.CONFIRMADO,
        source="manual",
    )
    db.add(tx)
    db.commit()
    return redirect_flash("/transacciones", "Transacción añadida")


@router.post("/{tx_id}/editar")
def edit_transaction(
    tx_id: int,
    date: date_cls = Form(...),
    type: TransactionType = Form(...),
    category_id: OptInt = None,
    amount: float = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    tx = db.get(Transaction, tx_id)
    if not tx:
        return redirect_flash("/transacciones", "La transacción ya no existe", "error")
    tx.date = date
    tx.type = type
    tx.category_id = category_id
    tx.amount = amount
    tx.description = description
    db.commit()
    return redirect_flash("/transacciones", "Transacción actualizada")


@router.post("/{tx_id}/eliminar")
def delete_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if tx:
        db.delete(tx)
        db.commit()
    return redirect_flash("/transacciones", "Transacción eliminada", "info")


@router.post("/{tx_id}/confirmar")
def confirm_transaction(
    tx_id: int,
    amount: float = Form(...),
    type: TransactionType = Form(...),
    category_id: OptInt = None,
    description: str = Form(""),
    db: Session = Depends(get_db),
):
    """Confirma (y opcionalmente corrige) una transacción pendiente creada por voz."""
    tx = db.get(Transaction, tx_id)
    if tx:
        tx.amount = amount
        tx.type = type
        tx.category_id = category_id
        tx.description = description
        tx.status = TransactionStatus.CONFIRMADO
        db.commit()
    return redirect_flash("/transacciones", "Transacción confirmada")


@router.post("/{tx_id}/rechazar")
def reject_transaction(tx_id: int, db: Session = Depends(get_db)):
    tx = db.get(Transaction, tx_id)
    if tx:
        db.delete(tx)
        db.commit()
    return redirect_flash("/transacciones", "Pendiente rechazada", "info")


@router.get("/exportar")
def export_csv(mes: str | None = None, db: Session = Depends(get_db)):
    """Exporta a CSV las transacciones (todas o las del mes filtrado)."""
    rows = _filtered_query(db, mes).all()

    buffer = io.StringIO()
    buffer.write("\ufeff")  # BOM para que Excel detecte UTF-8
    writer = csv.writer(buffer)
    writer.writerow(["fecha", "tipo", "categoria", "importe", "descripcion", "origen", "estado"])
    for tx in rows:
        writer.writerow([
            tx.date.isoformat(),
            tx.type.value,
            tx.category.name if tx.category else "",
            "%.2f" % tx.amount,
            tx.description,
            tx.source,
            tx.status.value,
        ])
    buffer.seek(0)

    filename = ("transacciones_%s.csv" % mes) if mes else "transacciones.csv"
    disposition = 'attachment; filename="%s"' % filename
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.post("/importar-csv")
async def import_csv(
    archivo: UploadFile = File(...),
    columna_fecha: str = Form(...),
    columna_importe: str = Form(...),
    columna_descripcion: str = Form(...),
    formato_fecha: str = Form("%Y-%m-%d"),
    db: Session = Depends(get_db),
):
    """Importa movimientos desde un CSV exportado del banco. El usuario indica qué
    columnas corresponden a fecha/importe/descripción, ya que cada banco exporta
    con nombres de columna distintos."""
    # Acotado: `archivo.read()` a secas carga el fichero entero y el decode hace
    # una segunda copia, así que arrastrar el fichero equivocado mataba el
    # proceso por memoria en vez de dar un error.
    contenido = await leer_texto_limitado(archivo)
    reader = csv.DictReader(io.StringIO(contenido))
    creados = 0
    ignorados = 0
    for row in reader:
        try:
            raw_date = row[columna_fecha].strip()
            raw_amount = row[columna_importe].strip().replace(",", ".")
            tx_date = datetime.strptime(raw_date, formato_fecha).date()
            amount = float(raw_amount)
        except (KeyError, ValueError, AttributeError):
            ignorados += 1
            continue  # fila mal formada o columna inexistente: se ignora

        description = (row.get(columna_descripcion) or "").strip()
        category = guess_category(description, db)
        db.add(Transaction(
            date=tx_date,
            type=TransactionType.INGRESO if amount > 0 else TransactionType.GASTO,
            category_id=category.id if category else None,
            amount=abs(amount),
            description=description,
            status=TransactionStatus.CONFIRMADO,
            source="csv",
        ))
        creados += 1
    db.commit()

    if creados == 0:
        return redirect_flash(
            "/transacciones",
            "No se importó nada: revisa los nombres de columna y el formato de fecha",
            "error",
        )
    msg = "%d transacciones importadas" % creados
    if ignorados:
        msg += " (%d filas ignoradas)" % ignorados
    return redirect_flash("/transacciones", msg)


@router.post("/voz")
async def voice_transaction(request: Request, db: Session = Depends(get_db)):
    """Recibe texto ya transcrito por el navegador (Web Speech API) y crea
    una transacción PENDIENTE de aprobar. Si no se entiende el importe,
    no se crea nada y se devuelve un aviso claro."""
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "No se ha recibido ningún texto."}

    # ¿Es una operación de inversión? ("compré 0,1 ethereum a 2.800")
    op = parse_voice_operation(text, db)
    if op is not None:
        if op["error"]:
            return {"ok": False, "error": op["error"]}
        operation = Operation(
            asset_id=op["asset"].id,
            type=OperationType(op["type"]),
            date=op["date"],
            quantity=op["quantity"],
            unit_price=op["unit_price"],
            status=TransactionStatus.PENDIENTE,
            source="voz",
        )
        db.add(operation)
        db.commit()
        resumen = "%s de %s %s a %.2f %s · %s" % (
            "Compra" if op["type"] == "compra" else "Venta",
            op["quantity"], op["asset"].name, op["unit_price"],
            op["asset"].currency.value, op["date"].strftime("%d/%m/%Y"),
        )
        return {"ok": True, "operation_id": operation.id, "summary": resumen}

    parsed = parse_voice_text(text, db)
    if parsed["amount"] is None or parsed["amount"] <= 0:
        return {
            "ok": False,
            "error": 'No he entendido el importe en "%s". Repite indicando la cantidad, p. ej. "15 euros".' % text,
        }

    # Transaction.amount va SIEMPRE en la moneda base: si se dictó en otra divisa
    # ("gasté 20 dólares") hay que convertirlo, no apuntar el número tal cual.
    amount = market_data.to_base(parsed["amount"], parsed["currency"], settings.base_currency)
    if amount is None:
        return {
            "ok": False,
            "error": "No hay tipo de cambio %s→%s ahora mismo; inténtalo en unos minutos."
                     % (parsed["currency"], settings.base_currency),
        }

    tx = Transaction(
        date=parsed["date"],
        type=TransactionType(parsed["type"]),
        category_id=parsed["category_id"],
        amount=amount,
        description=parsed["description"],
        status=TransactionStatus.PENDIENTE,
        source="voz",
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)

    resumen = "%s de %s %s" % (
        "Ingreso" if tx.type == TransactionType.INGRESO else "Gasto",
        dinero(tx.amount), settings.base_currency,
    )
    if parsed["currency"] != settings.base_currency:
        resumen += " (%.2f %s)" % (parsed["amount"], parsed["currency"])
    if parsed["category_name"]:
        resumen += " · %s" % parsed["category_name"]
    resumen += " · %s" % tx.date.strftime("%d/%m/%Y")
    return {"ok": True, "transaction_id": tx.id, "summary": resumen}
