"""Punto de entrada: Tracker de Patrimonio e Ingresos/Gastos."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.staticfiles import StaticFiles

from .auth import verify_auth
from .csrf import issue_token, set_cookie, verify_csrf
from .database import revision_pendiente, SessionLocal
from .models import Category
from .routers import (
    accounts, analysis, assets, categories, dashboard, imports, operations, recurring, transactions,
)
from .services.recurring import generate_due_transactions
from .services.scheduler import start_scheduler, snapshot_net_worth, update_all_prices
from .services.telegram_bot import start_bot, stop_bot
from fastapi.concurrency import run_in_threadpool

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = [
    ("Vivienda", "alquiler,hipoteca,luz,agua,gas,comunidad"),
    ("Comida", "supermercado,restaurante,mercadona,carrefour,comida"),
    ("Transporte", "gasolina,metro,bus,uber,cabify,parking"),
    ("Ocio", "cine,netflix,spotify,videojuegos,ocio"),
    ("Salud", "farmacia,medico,dentista,seguro salud"),
    ("Nómina/Ingresos", "nomina,nómina,salario,sueldo"),
    ("Otros", ""),
]


def seed_categories() -> None:
    db = SessionLocal()
    try:
        if db.query(Category).count() == 0:
            for name, keywords in DEFAULT_CATEGORIES:
                db.add(Category(name=name, keywords=keywords))
            db.commit()
    finally:
        db.close()


def comprobar_esquema() -> None:
    """Avisa si la base de datos no está en la última revisión.

    Las migraciones NO se aplican aquí: las corre el entrypoint antes de
    levantar uvicorn. Ejecutar `alembic upgrade` dentro del lifespan podía
    quedarse esperando un lock de SQLite y dejaba el arranque colgado sin decir
    por qué. Aquí solo se lee `alembic_version`, que no bloquea.
    """
    try:
        actual, head = revision_pendiente()
    except Exception:
        logger.exception("No se pudo comprobar la revisión del esquema")
        return
    if actual == head:
        return
    logger.error(
        "La base de datos está en la revisión %s y el código espera %s. "
        "Arranca con el entrypoint del contenedor o ejecuta 'alembic upgrade head'; "
        "hasta entonces habrá errores de columna inexistente.",
        actual or "sin marcar", head,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("lifespan: arrancando")
    comprobar_esquema()
    seed_categories()
    try:
        snapshot_net_worth()
    except Exception:
        logger.exception("No se pudo generar el snapshot inicial de patrimonio")
    # Catch-up de recurrentes: genera lo vencido mientras el servidor estuvo apagado
    db = SessionLocal()
    try:
        generate_due_transactions(db)
    except Exception:
        logger.exception("No se pudieron generar las transacciones recurrentes")
    finally:
        db.close()
    app.state.scheduler = start_scheduler()
    logger.info("lifespan: scheduler arrancado")
    app.state.telegram_bot = start_bot()
    logger.info("lifespan: listo")
    yield
    logger.info("lifespan: parando")
    stop_bot()
    app.state.scheduler.shutdown(wait=False)


# La protección CSRF se declara a nivel de app, no router a router: así cubre
# también cualquier ruta que se añada después (que es justo como se coló en su
# día el /api/refresh-prices sin autenticación).
app = FastAPI(title="Tracker de Patrimonio", lifespan=lifespan, dependencies=[Depends(verify_csrf)])


@app.middleware("http")
async def csrf_cookie(request: Request, call_next):
    """Emite el token de esta petición y lo persiste en la cookie si es nuevo."""
    token = issue_token(request)
    response = await call_next(request)
    if request.cookies.get("csrftoken") != token:
        set_cookie(response, token)
    return response


app.include_router(dashboard.router)
app.include_router(assets.router)
app.include_router(operations.router)
app.include_router(imports.router)
app.include_router(analysis.router)
app.include_router(accounts.router)
app.include_router(transactions.router)
app.include_router(recurring.router)
app.include_router(categories.router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/salud")
def health():
    """Healthcheck del contenedor: sin auth a propósito (Docker no lleva credenciales)."""
    return {"status": "ok"}


@app.post("/api/refresh-prices", dependencies=[Depends(verify_auth)])
async def refresh_prices():
    await run_in_threadpool(update_all_prices)
    return {"status": "ok", "message": "Precios actualizados"}
