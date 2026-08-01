"""Cuentas/plataformas (broker, exchange, banco). Se gestionan desde la página de operaciones."""
from fastapi import APIRouter, Depends, Form
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import get_db
from ..flash import redirect_flash
from ..models import Account, AccountKind, Asset, Operation

router = APIRouter(prefix="/cuentas", tags=["cuentas"], dependencies=[Depends(verify_auth)])


@router.post("")
def create_account(
    name: str = Form(...),
    kind: AccountKind = Form(AccountKind.BROKER),
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return redirect_flash("/operaciones", "El nombre no puede estar vacío", "error")
    if db.query(Account).filter(Account.name == name).first():
        return redirect_flash("/operaciones", 'Ya existe una cuenta "%s"' % name, "error")
    db.add(Account(name=name, kind=kind))
    db.commit()
    return redirect_flash("/operaciones", 'Cuenta "%s" creada' % name)


@router.post("/{account_id}/editar")
def edit_account(
    account_id: int,
    name: str = Form(...),
    kind: AccountKind = Form(AccountKind.BROKER),
    db: Session = Depends(get_db),
):
    account = db.get(Account, account_id)
    if not account:
        return redirect_flash("/operaciones", "La cuenta ya no existe", "error")
    account.name = name.strip() or account.name
    account.kind = kind
    db.commit()
    return redirect_flash("/operaciones", 'Cuenta "%s" actualizada' % account.name)


@router.post("/{account_id}/eliminar")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if account:
        # Las operaciones y activos asociados quedan sin cuenta, no se borran
        db.query(Operation).filter(Operation.account_id == account_id).update({"account_id": None})
        db.query(Asset).filter(Asset.account_id == account_id).update({"account_id": None})
        db.delete(account)
        db.commit()
    return redirect_flash("/operaciones", "Cuenta eliminada", "info")
