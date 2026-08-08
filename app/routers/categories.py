"""Gestión de categorías de gasto/ingreso, incluyendo límites de presupuesto opcionales."""
from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from ..auth import verify_auth
from ..config import settings
from ..database import get_db
from ..flash import redirect_flash
from ..forms import OptFloat
from ..models import Category, RecurringTransaction, Transaction
from ..templating import templates

router = APIRouter(prefix="/categorias", tags=["categorias"], dependencies=[Depends(verify_auth)])


@router.get("")
def list_categories(request: Request, db: Session = Depends(get_db)):
    categories = db.query(Category).order_by(Category.name).all()
    return templates.TemplateResponse(request, "categories.html", {
            "categories": categories, "budgets_enabled": settings.budgets_enabled},
    )


@router.get("/opciones")
def category_options(db: Session = Depends(get_db)):
    """Categorías en JSON para el formulario rápido del botón flotante.

    Ese formulario vive en base.html, que se renderiza en todas las páginas y no
    recibe la lista: pasarla en cada vista obligaría a tocar los nueve routers.
    Se pide una sola vez, al abrir el diálogo por primera vez."""
    return [
        {"id": c.id, "name": c.name}
        for c in db.query(Category).order_by(Category.name).all()
    ]


@router.post("")
def create_category(
    name: str = Form(...),
    keywords: str = Form(""),
    budget_limit: OptFloat = None,
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        return redirect_flash("/categorias", "El nombre no puede estar vacío", "error")
    # `name` es UNIQUE: sin esta comprobación, repetir un nombre revienta con un
    # 500 por IntegrityError en vez de avisar (mismo patrón que en cuentas).
    if db.query(Category).filter(Category.name == name).first():
        return redirect_flash("/categorias", 'Ya existe una categoría "%s"' % name, "error")
    db.add(Category(name=name, keywords=keywords, budget_limit=budget_limit))
    db.commit()
    return redirect_flash("/categorias", 'Categoría "%s" añadida' % name)


@router.post("/{cat_id}/editar")
def edit_category(
    cat_id: int,
    name: str = Form(...),
    keywords: str = Form(""),
    budget_limit: OptFloat = None,
    db: Session = Depends(get_db),
):
    cat = db.get(Category, cat_id)
    if not cat:
        return redirect_flash("/categorias", "La categoría ya no existe", "error")
    name = name.strip()
    if not name:
        return redirect_flash("/categorias", "El nombre no puede estar vacío", "error")
    duplicada = db.query(Category).filter(Category.name == name, Category.id != cat_id).first()
    if duplicada:
        return redirect_flash("/categorias", 'Ya existe una categoría "%s"' % name, "error")
    cat.name = name
    cat.keywords = keywords
    cat.budget_limit = budget_limit
    db.commit()
    return redirect_flash("/categorias", 'Categoría "%s" guardada' % cat.name)


@router.post("/{cat_id}/eliminar")
def delete_category(cat_id: int, db: Session = Depends(get_db)):
    """Borra la categoría y deja sin categoría lo que la usaba.

    Mismo patrón que `delete_account`: las referencias se ponen a None ANTES de
    borrar. Si no, quedan apuntando a un id inexistente —SQLite no aplica las
    claves foráneas—, y lo peligroso no es que se vean como "Sin categoría",
    sino que SQLite reutiliza los ids: la siguiente categoría que se cree puede
    recibir el de la borrada y adoptar transacciones que no le corresponden.
    """
    cat = db.get(Category, cat_id)
    if not cat:
        return redirect_flash("/categorias", "Categoría eliminada", "info")

    afectadas = db.query(Transaction).filter(Transaction.category_id == cat_id).update(
        {"category_id": None}
    )
    db.query(RecurringTransaction).filter(RecurringTransaction.category_id == cat_id).update(
        {"category_id": None}
    )
    db.delete(cat)
    db.commit()

    if afectadas:
        aviso = "Categoría eliminada; %d transacciones quedan sin categoría" % afectadas
    else:
        aviso = "Categoría eliminada"
    return redirect_flash("/categorias", aviso, "info")
