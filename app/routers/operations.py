"""Operaciones de inversión (compras/ventas): alta, edición, paginación y filtro por activo."""
from datetime import date as date_cls
from math import ceil

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session, joinedload

from ..auth import verify_auth
from ..database import get_db
from ..flash import redirect_flash
from ..forms import OptFloat, OptInt
from ..models import Account, Asset, AssetType, Operation, OperationType, TransactionStatus
from ..services.portfolio import asset_summary
from ..templating import templates

router = APIRouter(prefix="/operaciones", tags=["operaciones"], dependencies=[Depends(verify_auth)])

PER_PAGE = 50

INVERTIBLE = (AssetType.ACCION, AssetType.CRIPTO)


@router.get("")
def list_operations(
    request: Request,
    activo: int | None = None,
    tipo: str | None = None,
    cuenta: int | None = None,
    pagina: int = 1,
    db: Session = Depends(get_db),
):
    query = (
        db.query(Operation)
        .options(joinedload(Operation.asset), joinedload(Operation.account))
        .filter(Operation.status == TransactionStatus.CONFIRMADO)
        .order_by(Operation.date.desc(), Operation.id.desc())
    )
    if activo:
        query = query.filter(Operation.asset_id == activo)
    # Los filtros de tipo y cuenta van como chips: son pocos valores y fijos,
    # así que se ven de un vistazo sin desplegar nada. El de activo sigue en un
    # select porque una cartera normal tiene decenas y no caben como chips.
    if tipo in ("compra", "venta"):
        query = query.filter(Operation.type == OperationType(tipo))
    if cuenta:
        query = query.filter(Operation.account_id == cuenta)

    pending = (
        db.query(Operation)
        .options(joinedload(Operation.asset))
        .filter(Operation.status == TransactionStatus.PENDIENTE)
        .order_by(Operation.date.desc())
        .all()
    )

    total = query.count()
    total_paginas = max(1, ceil(total / PER_PAGE))
    pagina = min(max(1, pagina), total_paginas)
    operations = query.offset((pagina - 1) * PER_PAGE).limit(PER_PAGE).all()

    assets = (
        db.query(Asset)
        .filter(Asset.asset_type.in_(INVERTIBLE))
        .order_by(Asset.name)
        .all()
    )
    accounts = db.query(Account).order_by(Account.name).all()

    # Resumen de la posición cuando se filtra por un activo concreto
    filtered_asset = db.get(Asset, activo) if activo else None
    summary = asset_summary(filtered_asset) if filtered_asset else None

    return templates.TemplateResponse(request, "operations.html", {
            "operations": operations,
            "pending": pending,
            "assets": assets,
            "accounts": accounts,
            "activo": activo,
            "tipo": tipo if tipo in ("compra", "venta") else None,
            "cuenta": cuenta,
            "filtered_asset": filtered_asset,
            "summary": summary,
            "op_types": list(OperationType),
            "pagina": pagina,
            "total_paginas": total_paginas,
            "total": total,
            "hoy": date_cls.today().isoformat(),
        },
    )


def _validate(db: Session, asset_id: int, quantity: float, unit_price: float) -> str | None:
    asset = db.get(Asset, asset_id)
    if not asset:
        return "El activo no existe"
    if asset.asset_type not in INVERTIBLE:
        return "Las operaciones solo aplican a acciones/ETFs/fondos y cripto"
    if quantity <= 0:
        return "La cantidad debe ser mayor que 0"
    if unit_price < 0:
        return "El precio no puede ser negativo"
    return None


@router.post("")
def create_operation(
    asset_id: int = Form(...),
    account_id: OptInt = None,
    type: OperationType = Form(...),
    date: date_cls = Form(...),
    quantity: float = Form(...),
    unit_price: float = Form(...),
    fee: OptFloat = None,
    db: Session = Depends(get_db),
):
    error = _validate(db, asset_id, quantity, unit_price)
    if error:
        return redirect_flash("/operaciones", error, "error")

    db.add(Operation(
        asset_id=asset_id,
        account_id=account_id,
        type=type,
        date=date,
        quantity=quantity,
        unit_price=unit_price,
        fee=fee or 0.0,
        source="manual",
    ))
    db.commit()
    asset = db.get(Asset, asset_id)
    verbo = "Compra" if type == OperationType.COMPRA else "Venta"
    return redirect_flash("/operaciones", '%s de "%s" registrada' % (verbo, asset.name))


@router.post("/{op_id:int}/editar")
def edit_operation(
    op_id: int,
    asset_id: int = Form(...),
    account_id: OptInt = None,
    type: OperationType = Form(...),
    date: date_cls = Form(...),
    quantity: float = Form(...),
    unit_price: float = Form(...),
    fee: OptFloat = None,
    db: Session = Depends(get_db),
):
    op = db.get(Operation, op_id)
    if not op:
        return redirect_flash("/operaciones", "La operación ya no existe", "error")
    error = _validate(db, asset_id, quantity, unit_price)
    if error:
        return redirect_flash("/operaciones", error, "error")

    op.asset_id = asset_id
    op.account_id = account_id
    op.type = type
    op.date = date
    op.quantity = quantity
    op.unit_price = unit_price
    op.fee = fee or 0.0
    db.commit()
    return redirect_flash("/operaciones", "Operación actualizada")


@router.post("/{op_id:int}/eliminar")
def delete_operation(op_id: int, db: Session = Depends(get_db)):
    op = db.get(Operation, op_id)
    if op:
        db.delete(op)
        db.commit()
    return redirect_flash("/operaciones", "Operación eliminada", "info")


@router.post("/{op_id:int}/confirmar")
def confirm_operation(
    op_id: int,
    quantity: float = Form(...),
    unit_price: float = Form(...),
    fee: OptFloat = None,
    account_id: OptInt = None,
    db: Session = Depends(get_db),
):
    """Confirma (y opcionalmente corrige) una operación pendiente creada por voz."""
    op = db.get(Operation, op_id)
    if not op:
        return redirect_flash("/operaciones", "La operación ya no existe", "error")
    if quantity <= 0 or unit_price < 0:
        return redirect_flash("/operaciones", "Cantidad o precio inválidos", "error")
    op.quantity = quantity
    op.unit_price = unit_price
    op.fee = fee or 0.0
    op.account_id = account_id
    op.status = TransactionStatus.CONFIRMADO
    db.commit()
    return redirect_flash("/operaciones", "Operación confirmada")


@router.post("/{op_id:int}/rechazar")
def reject_operation(op_id: int, db: Session = Depends(get_db)):
    op = db.get(Operation, op_id)
    if op:
        db.delete(op)
        db.commit()
    return redirect_flash("/operaciones", "Operación pendiente rechazada", "info")
