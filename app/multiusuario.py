"""Aislamiento entre usuarios, en un solo sitio.

La alternativa era añadir `.filter(usuario_id == actual)` a los **106 puntos de
consulta** repartidos por routers y servicios. No se ha hecho, y no por pereza:
en un aislamiento hecho a mano, el fallo no es que algo se rompa —es que un
filtro que falte enseña el dinero de otra persona sin que nada avise. Y el
número 107 lo escribe alguien dentro de seis meses sin saber que había que
filtrarlo.

Aquí el filtro se aplica solo, en dos enganches del ORM:

- `do_orm_execute` añade `usuario_id == actual` a toda consulta de una entidad
  con dueño, con `with_loader_criteria`, que además se propaga a las relaciones
  cargadas por `selectinload` o `joinedload`.
- `before_flush` rellena `usuario_id` en lo que se crea, para que ningún alta
  tenga que acordarse.

**El usuario vive en la sesión (`session.info`), no en una variable global ni en
un contextvar.** Los endpoints síncronos de FastAPI corren en un hilo del pool,
así que lo que se guarda por hilo no llega donde hace falta —ya pasó al montar
el contador de consultas SQL—. Colgarlo de la sesión lo hace explícito: una
sesión sabe de quién es, y si no lo sabe es porque quien la abrió no lo dijo.

Una sesión SIN usuario no se filtra. Es deliberado y lo necesitan los jobs de
fondo, que recorren a todo el mundo: el refresco de precios o el backup no son
de nadie en concreto. La contrapartida es que abrir `SessionLocal()` a secas en
un camino web saltaría el aislamiento, y por eso `get_db` —la única vía por la
que las vistas obtienen sesión— siempre lo fija.

`Operation` no lleva `usuario_id`: cuelga de su activo, que sí lo tiene, y
duplicarlo serían dos fuentes de verdad para lo mismo. Se filtra por el activo.
`PriceHistory` tampoco: son cotizaciones públicas, y que dos personas con el
mismo ETF compartan la serie cacheada es lo correcto.
"""
from sqlalchemy import event, orm

from .models import (
    Account,
    Alerta,
    Asset,
    Benchmark,
    Category,
    NetWorthIntraday,
    NetWorthSnapshot,
    Operation,
    PesoObjetivo,
    RecurringTransaction,
    Transaction,
    Watchlist,
)

CLAVE = "usuario_id"

# Entidades con dueño directo.
CON_DUENO = (
    Account, Asset, Category, Transaction, RecurringTransaction,
    NetWorthSnapshot, NetWorthIntraday, Benchmark, Watchlist, Alerta, PesoObjetivo,
)


def usuario_de(session) -> int | None:
    return session.info.get(CLAVE)


def fijar_usuario(session, usuario_id: int | None) -> None:
    session.info[CLAVE] = usuario_id


@event.listens_for(orm.Session, "do_orm_execute")
def _filtrar_por_usuario(estado) -> None:
    if not estado.is_select or estado.is_column_load or estado.is_relationship_load:
        return
    usuario_id = usuario_de(estado.session)
    if usuario_id is None:
        return  # sesión sin dueño: jobs de fondo, que trabajan para todos

    for entidad in CON_DUENO:
        estado.statement = estado.statement.options(
            orm.with_loader_criteria(
                entidad,
                entidad.usuario_id == usuario_id,
                # Se aplica también a las relaciones cargadas en la misma
                # consulta: sin esto, un `selectinload(Asset.operations)` traería
                # las de otro dueño si alguien colara un id ajeno.
                include_aliases=True,
            )
        )
    # Las operaciones no tienen dueño propio: se filtran por el de su activo.
    estado.statement = estado.statement.options(
        orm.with_loader_criteria(
            Operation,
            Operation.asset.has(Asset.usuario_id == usuario_id),
            include_aliases=True,
        )
    )


def por_cada_usuario():
    """Una sesión ya acotada por cada persona, para los jobs de fondo.

        for usuario, db in por_cada_usuario():
            hacer_algo(db)

    Los jobs que producen un dato POR PERSONA —el snapshot de patrimonio, la
    muestra intradía, las recurrentes, las alertas— tienen que pasar por aquí.
    Si abrieran una sesión sin dueño verían la cartera de todos sumada y
    escribirían un único snapshot con el patrimonio de la casa entera, que no es
    el de nadie.

    Los que no producen datos de nadie —el backup, el refresco de cotizaciones—
    se quedan con una sesión sin dueño a propósito: una sola pasada sirve para
    todos y pedir el mismo precio una vez por persona sería tirar peticiones.

    Cada usuario lleva su propia sesión: una compartida arrastraría en su mapa
    de identidad objetos del anterior, que es exactamente la fuga que esto viene
    a evitar.
    """
    from .database import SessionLocal
    from .models import Usuario

    inventario = SessionLocal()
    try:
        ids = [fila[0] for fila in inventario.query(Usuario.id).order_by(Usuario.id).all()]
    finally:
        inventario.close()

    for usuario_id in ids:
        db = SessionLocal()
        fijar_usuario(db, usuario_id)
        try:
            yield usuario_id, db
        finally:
            db.close()


@event.listens_for(orm.Session, "before_flush")
def _asignar_usuario(session, contexto, instancias) -> None:
    """Rellena `usuario_id` en lo que se acaba de crear.

    Así ningún alta tiene que acordarse, que es lo mismo que decir que ninguna
    puede olvidarse."""
    usuario_id = usuario_de(session)
    if usuario_id is None:
        return

    for objeto in session.new:
        if isinstance(objeto, CON_DUENO) and getattr(objeto, "usuario_id", None) is None:
            objeto.usuario_id = usuario_id
