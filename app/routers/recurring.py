"""Gestión de gastos/ingresos recurrentes mensuales (alquiler, suscripciones, nómina...)."""
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..database import get_db
from ..flash import redirect_flash
from ..forms import OptInt
from ..models import Category, Currency, RecurringTransaction, TransactionType
from ..services.recurring import FREQUENCIES, generate_due_transactions, next_due_date
from ..templating import templates

router = APIRouter(prefix="/recurrentes", tags=["recurrentes"], dependencies=[Depends(verify_auth)])


@router.get("")
def list_recurring(request: Request, db: Session = Depends(get_db)):
    rules = db.query(RecurringTransaction).order_by(RecurringTransaction.name).all()
    today = date.today()
    rows = [{"rule": r, "next_due": next_due_date(r, today) if r.active else None} for r in rules]
    categories = db.query(Category).order_by(Category.name).all()
    return templates.TemplateResponse(request, "recurring.html", {
            "rows": rows,
            "categories": categories,
            "tx_types": list(TransactionType),
            "currencies": list(Currency),
            "frequencies": FREQUENCIES,
            "hoy": today.isoformat(),
        },
    )


@router.post("")
def create_recurring(
    name: str = Form(...),
    type: TransactionType = Form(...),
    amount: float = Form(...),
    currency: Currency = Form(Currency.EUR),
    interval_months: int = Form(1),
    category_id: OptInt = None,
    day_of_month: int = Form(...),
    start_date: date = Form(...),
    db: Session = Depends(get_db),
):
    rule = RecurringTransaction(
        name=name.strip(),
        type=type,
        amount=amount,
        currency=currency,
        interval_months=interval_months if interval_months in FREQUENCIES else 1,
        category_id=category_id,
        day_of_month=max(1, min(31, day_of_month)),
        start_date=start_date,
    )
    db.add(rule)
    db.commit()
    created = generate_due_transactions(db)
    msg = 'Recurrente "%s" creada' % rule.name
    if created:
        msg += " (%d transacciones generadas)" % created
    return redirect_flash("/recurrentes", msg)


@router.post("/{rule_id:int}/editar")
def edit_recurring(
    rule_id: int,
    name: str = Form(...),
    type: TransactionType = Form(...),
    amount: float = Form(...),
    currency: Currency = Form(Currency.EUR),
    interval_months: int = Form(1),
    category_id: OptInt = None,
    day_of_month: int = Form(...),
    db: Session = Depends(get_db),
):
    rule = db.get(RecurringTransaction, rule_id)
    if not rule:
        return redirect_flash("/recurrentes", "La recurrente ya no existe", "error")
    rule.name = name.strip()
    rule.type = type
    rule.amount = amount
    rule.currency = currency
    rule.interval_months = interval_months if interval_months in FREQUENCIES else 1
    rule.category_id = category_id
    rule.day_of_month = max(1, min(31, day_of_month))
    db.commit()
    return redirect_flash("/recurrentes", 'Recurrente "%s" actualizada' % rule.name)


@router.post("/{rule_id:int}/toggle")
def toggle_recurring(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(RecurringTransaction, rule_id)
    if not rule:
        return redirect_flash("/recurrentes", "La recurrente ya no existe", "error")
    rule.active = not rule.active
    if rule.active:
        # Al reactivar, no generar retroactivamente todo el periodo pausado
        rule.last_generated = date.today()
        msg = '"%s" activada (desde hoy)' % rule.name
    else:
        msg = '"%s" pausada' % rule.name
    db.commit()
    return redirect_flash("/recurrentes", msg)


@router.post("/{rule_id:int}/eliminar")
def delete_recurring(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(RecurringTransaction, rule_id)
    if rule:
        db.delete(rule)
        db.commit()
    return redirect_flash("/recurrentes", "Recurrente eliminada (sus transacciones ya generadas se conservan)", "info")


@router.post("/generar")
def force_generate(db: Session = Depends(get_db)):
    created = generate_due_transactions(db)
    if created:
        return redirect_flash("/recurrentes", "%d transacciones generadas" % created)
    return redirect_flash("/recurrentes", "No había nada pendiente de generar", "info")
