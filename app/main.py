"""Punto de entrada: Tracker de Patrimonio e Ingresos/Gastos."""
import logging
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import verify_auth
from .config import settings
from .csrf import issue_token, set_cookie, verify_csrf
from .database import SessionLocal, get_db, revision_pendiente
from .models import Asset, Category
from .routers import (
    accounts,
    analysis,
    assets,
    categories,
    dashboard,
    imports,
    operations,
    recurring,
    transactions,
)
from .services.recurring import generate_due_transactions
from .services.scheduler import snapshot_net_worth, start_scheduler, update_all_prices
from .services.telegram_bot import start_bot, stop_bot

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


def avisar_si_no_hay_autenticacion() -> None:
    """Deja constancia en el log cuando la app queda sin pedir credenciales.

    Sin autenticación no hay nada en la interfaz que lo indique: la app se ve
    exactamente igual, así que el estado inseguro es invisible salvo que uno
    vaya a mirar el `.env`. El log del arranque es el único sitio donde se
    mira cuando algo va mal, y es donde tiene que constar.
    """
    if settings.enable_auth:
        return
    logger.warning(
        "ENABLE_AUTH está desactivado: cualquiera que alcance el puerto ve y edita "
        "el patrimonio sin credenciales. Es lo correcto solo si el puerto no sale "
        "de esta máquina (FINANCE_BIND=127.0.0.1, el valor por defecto)."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("lifespan: arrancando")
    avisar_si_no_hay_autenticacion()
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


# 'unsafe-inline' es un compromiso consciente de esta primera iteración: hay
# bloques <script> y atributos style="" en las plantillas, y quitarlos es un
# trabajo aparte (anotado como seguimiento para migrar a nonces). Aun así la
# CSP ya impide cargar scripts de otro origen y exfiltrar por img-src o
# connect-src externos, que es la mitad del valor.
#
# frame-ancestors 'none' no es decorativo: con las credenciales Basic
# cacheadas por el navegador, embeber la app en un iframe permite clickjacking
# sobre "Eliminar activo". El token CSRF protege el POST, pero no protege de
# que el clic lo dé el propio usuario engañado sobre la página real.
CSP = (
    "default-src 'self'; img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


if settings.debug_sql:
    # Se registra solo si está activado: con DEBUG_SQL apagado no hay listener
    # y el coste es exactamente cero.
    from .database import engine
    from .medicion import contar_consultas, instrumentar

    instrumentar(engine)

    @app.middleware("http")
    async def contar_sql(request: Request, call_next):
        """Publica cuántas sentencias SQL costó la petición."""
        with contar_consultas(engine) as consultas:
            response = await call_next(request)
        response.headers["X-Consultas-SQL"] = str(consultas.total)
        return response


@app.middleware("http")
async def cabeceras_de_seguridad(request: Request, call_next):
    """Cabeceras de seguridad en todas las respuestas, estáticos y errores incluidos.

    Se declara a nivel de app por el mismo motivo que el CSRF: cubrir también
    lo que se añada después. `setdefault` para que una ruta pueda relajar una
    cabecera concreta si algún día hace falta, sin tocar el middleware.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    # Redundante con frame-ancestors para navegadores al día, pero es la única
    # protección contra encuadre en los que no implementan esa directiva.
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", CSP)
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

# Absoluta, por el mismo motivo que las plantillas: con la ruta relativa,
# StaticFiles comprueba que el directorio existe y lanza RuntimeError al montar
# si el proceso no arrancó desde la raíz del repo. La app no llegaba a levantar.
STATIC_DIR = Path(__file__).resolve().parent / "static"

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _pagina_error(request: Request, codigo: int, titulo: str, detalle: str) -> HTMLResponse:
    """Página de error autocontenida: sin plantilla base, sin base de datos.

    Es deliberado. Si el error viene de que la base no responde, una página que
    extienda `base.html` —con su navegación y sus totales— fallaría al pintarse
    y volveríamos al texto plano. Esta solo necesita la hoja de estilos, y si
    tampoco carga sigue siendo legible.
    """
    return HTMLResponse(
        status_code=codigo,
        content=(
            '<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>%(codigo)d · Patrimonio</title>"
            '<link rel="stylesheet" href="/static/css/style.css">'
            "<style>body{display:grid;place-items:center;min-height:100vh;margin:0;"
            "background:var(--bg,#0b0d12);color:var(--text,#e9ecf2);"
            "font-family:Inter,system-ui,sans-serif;text-align:center;padding:1.5rem}"
            ".err{max-width:32rem}.err b{display:block;font-size:3.5rem;line-height:1;"
            "color:var(--text-3,#707a8c)}.err h1{font-size:1.35rem;margin:.8rem 0 .4rem}"
            ".err p{color:var(--text-2,#a9b1c0);margin:0 0 1.4rem}"
            ".err a{display:inline-block;padding:.6rem 1.1rem;border-radius:8px;"
            "background:var(--accent,#4f8ef7);color:#fff;text-decoration:none}</style>"
            "</head><body><div class=err><b>%(codigo)d</b><h1>%(titulo)s</h1>"
            "<p>%(detalle)s</p><a href=/>Volver al inicio</a></div></body></html>"
        ) % {
            "codigo": codigo,
            "titulo": escape(titulo),
            "detalle": escape(detalle),
        },
    )


def _problemas_para_servir(db: Session) -> list[str]:
    """Lo que impediría servir una página. Lista vacía = la app funciona.

    No basta con un `SELECT 1`. El fallo real que tuvo esta app durante semanas
    fue una columna que estaba en el modelo y no en la base: la conexión iba
    bien, las tablas existían y todas las páginas devolvían 500. Por eso aquí se
    consulta un `Asset` con el ORM, que emite un SELECT con todas las columnas
    mapeadas y revienta igual que reventaban las páginas.
    """
    problemas = []
    try:
        actual, head = revision_pendiente(db.get_bind())
        if actual != head:
            problemas.append("esquema desactualizado")
    except Exception:
        logger.exception("Healthcheck: no se pudo leer la revisión del esquema")
        problemas.append("esquema ilegible")

    try:
        db.query(Asset).limit(1).all()
    except Exception:
        logger.exception("Healthcheck: la consulta de prueba falló")
        problemas.append("consulta de prueba fallida")
    return problemas


@app.get("/salud")
def health(response: Response, db: Session = Depends(get_db)):
    """Healthcheck del contenedor: sin auth a propósito (Docker no lleva credenciales).

    Devuelve 503 si la app no puede servir páginas, para que Docker marque el
    contenedor como `unhealthy` en vez de dar por bueno un proceso vivo que
    responde 500 a todo. El detalle concreto va al log, no a la respuesta: esta
    ruta no pide credenciales.
    """
    problemas = _problemas_para_servir(db)
    if problemas:
        response.status_code = 503
        return {"status": "degradado", "problemas": problemas}
    return {"status": "ok"}


@app.exception_handler(StarletteHTTPException)
async def error_http(request: Request, exc: StarletteHTTPException):
    """404 y demás errores HTTP con la cara de la app, no el texto plano de Starlette."""
    if exc.status_code == 404:
        return _pagina_error(request, 404, "Esta página no existe",
                             "El enlace es antiguo o la dirección está mal escrita.")
    return _pagina_error(request, exc.status_code, "Algo ha fallado", exc.detail or "")


@app.exception_handler(Exception)
async def error_no_controlado(request: Request, exc: Exception):
    """Último recinto: cualquier excepción sin capturar.

    Antes salía la página en texto plano de Starlette, sin forma de volver atrás
    y sin pista de qué mirar. La traza va al log; al navegador solo el aviso.
    """
    logger.exception("Error no controlado en %s %s", request.method, request.url.path)
    return _pagina_error(
        request, 500, "Algo ha fallado",
        "El detalle está en el log del contenedor: docker compose logs -f.",
    )


@app.post("/api/refresh-prices", dependencies=[Depends(verify_auth)])
async def refresh_prices():
    await run_in_threadpool(update_all_prices)
    return {"status": "ok", "message": "Precios actualizados"}
