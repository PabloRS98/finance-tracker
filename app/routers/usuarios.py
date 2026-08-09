"""Elegir quién eres, y gestionar las cuentas de casa.

Estas rutas son las únicas que NO exigen usuario: son las que sirven para
tenerlo. Y por eso son también las únicas que abren la sesión sin filtrar —
necesitan ver la lista entera de personas, que es lo único compartido.
"""
from fastapi import APIRouter, Depends, Form, Request, Response
from sqlalchemy.orm import Session

from .. import sesion
from ..database import get_db
from ..flash import redirect_flash
from ..models import Usuario
from ..templating import templates

router = APIRouter(tags=["usuarios"])

MIN_PASSWORD = 4


def _lista(db: Session) -> list[Usuario]:
    return db.query(Usuario).order_by(Usuario.nombre).all()


@router.get("/entrar")
def elegir_usuario(request: Request, db: Session = Depends(get_db)):
    """Pantalla de elección. Sin usuarios todavía, ofrece crear el primero."""
    return templates.TemplateResponse(request, "entrar.html", {
        "usuarios": _lista(db),
        "min_password": MIN_PASSWORD,
    })


@router.post("/entrar")
def entrar(
    response: Response,
    usuario_id: int = Form(...),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        return redirect_flash("/entrar", "Esa cuenta ya no existe", "error")
    if not sesion.comprobar(password, usuario.password_hash):
        return redirect_flash("/entrar", "Contraseña incorrecta", "error")

    respuesta = redirect_flash("/", "Hola, %s" % usuario.nombre)
    sesion.abrir(respuesta, usuario.id)
    return respuesta


@router.post("/salir")
def salir():
    respuesta = redirect_flash("/entrar", "Sesión cerrada", "info")
    sesion.cerrar(respuesta)
    return respuesta


@router.post("/usuarios")
def crear_usuario(
    nombre: str = Form(...),
    password: str = Form(""),
    db: Session = Depends(get_db),
):
    """Alta de una persona. La contraseña es opcional a propósito.

    Se permite sin haber entrado: si no, la primera persona no podría crearse a
    sí misma. A partir de ahí no es un agujero — quien llega a esta pantalla ya
    ha pasado ENABLE_AUTH si está activado, y crear una cuenta nueva y vacía no
    da acceso a los datos de nadie."""
    nombre = nombre.strip()
    if not nombre:
        return redirect_flash("/entrar", "El nombre no puede estar vacío", "error")
    if db.query(Usuario).filter(Usuario.nombre == nombre).first():
        return redirect_flash("/entrar", 'Ya hay una cuenta llamada "%s"' % nombre, "error")
    if password and len(password) < MIN_PASSWORD:
        return redirect_flash(
            "/entrar", "La contraseña necesita al menos %d caracteres" % MIN_PASSWORD, "error",
        )

    usuario = Usuario(
        nombre=nombre,
        password_hash=sesion.hashear(password) if password else None,
    )
    db.add(usuario)
    db.commit()

    # Sus categorías por defecto, ya. Sin esto la primera pantalla de gastos
    # sale vacía y hay que inventarlas antes de poder apuntar nada.
    from ..main import DEFAULT_CATEGORIES
    from ..models import Category
    from ..multiusuario import fijar_usuario

    fijar_usuario(db, usuario.id)
    for nombre_cat, palabras in DEFAULT_CATEGORIES:
        db.add(Category(name=nombre_cat, keywords=palabras))
    db.commit()
    fijar_usuario(db, None)

    return redirect_flash("/entrar", 'Cuenta "%s" creada; ya puedes entrar' % nombre)


@router.post("/usuarios/password")
def cambiar_password(
    request: Request,
    actual: str = Form(""),
    nueva: str = Form(""),
    db: Session = Depends(get_db),
):
    """Poner, cambiar o quitar la contraseña de la cuenta con la que se entró.

    Dejar la nueva vacía la quita, que es una decisión legítima aquí: en casa,
    una contraseña que estorba acaba siendo de cuatro letras o la app abierta
    permanentemente."""
    usuario_id = sesion.leer(request)
    usuario = db.get(Usuario, usuario_id) if usuario_id is not None else None
    if usuario is None:
        return redirect_flash("/entrar", "Entra primero", "error")
    if not sesion.comprobar(actual, usuario.password_hash):
        return redirect_flash("/", "La contraseña actual no es correcta", "error")
    if nueva and len(nueva) < MIN_PASSWORD:
        return redirect_flash(
            "/", "La contraseña necesita al menos %d caracteres" % MIN_PASSWORD, "error",
        )

    usuario.password_hash = sesion.hashear(nueva) if nueva else None
    db.commit()
    aviso = "Contraseña actualizada" if nueva else "Contraseña quitada: entrarás sin ella"
    return redirect_flash("/", aviso)
