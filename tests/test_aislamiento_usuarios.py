"""Aislamiento entre usuarios: lo de uno no se ve desde el otro.

Es el test que decide si el multiusuario vale para algo. En una app de
patrimonio, un filtro que falte no da un error: enseña el dinero de otra
persona, y nadie se entera hasta que alguien lo mira.

Se prueba contra el mecanismo real —los dos enganches del ORM de
`app/multiusuario.py`— y no contra consultas escritas a mano en el test, porque
lo que hay que demostrar es justamente que el filtrado ocurre **sin que la
consulta lo pida**.
"""
from datetime import date

import pytest

from app.models import (
    Account,
    Alerta,
    Asset,
    AssetType,
    Category,
    Currency,
    NetWorthSnapshot,
    Operation,
    OperationType,
    TipoAlerta,
    Transaction,
    TransactionType,
    Usuario,
)
from app.multiusuario import fijar_usuario


@pytest.fixture
def dos_usuarios(db):
    """Ana y Beto, cada uno con un activo, un gasto y una cuenta."""
    ana, beto = Usuario(nombre="Ana"), Usuario(nombre="Beto")
    db.add_all([ana, beto])
    db.commit()

    for usuario, etiqueta in ((ana, "ANA"), (beto, "BETO")):
        activo = Asset(usuario_id=usuario.id, name="ACTIVO DE %s" % etiqueta,
                       asset_type=AssetType.ACCION, ticker="TCK%s" % etiqueta,
                       currency=Currency.EUR, current_price=100.0)
        db.add(activo)
        db.flush()
        db.add(Operation(asset_id=activo.id, type=OperationType.COMPRA,
                         quantity=1.0, unit_price=100.0, date=date(2026, 1, 5)))
        db.add(Transaction(usuario_id=usuario.id, date=date(2026, 1, 10), amount=50,
                           type=TransactionType.GASTO, description="gasto de %s" % etiqueta))
        db.add(Account(usuario_id=usuario.id, name="Revolut"))
        db.add(Category(usuario_id=usuario.id, name="Comida"))
        db.add(NetWorthSnapshot(usuario_id=usuario.id, date=date(2026, 1, 10),
                                total_value=1000.0, manual_value=0.0))
    db.commit()
    return ana, beto


# ---------- Lectura ----------

@pytest.mark.parametrize("modelo", [Asset, Transaction, Account, Category, NetWorthSnapshot])
def test_cada_usuario_solo_ve_lo_suyo(dos_usuarios, db, modelo):
    ana, beto = dos_usuarios

    fijar_usuario(db, ana.id)
    db.expire_all()
    de_ana = db.query(modelo).all()

    assert len(de_ana) == 1
    assert all(fila.usuario_id == ana.id for fila in de_ana)

    fijar_usuario(db, beto.id)
    db.expire_all()
    de_beto = db.query(modelo).all()

    assert len(de_beto) == 1
    assert all(fila.usuario_id == beto.id for fila in de_beto)


def test_las_operaciones_se_filtran_por_el_dueno_del_activo(dos_usuarios, db):
    """`Operation` no tiene `usuario_id`: cuelga de su activo. Duplicar el dueño
    serían dos fuentes de verdad, así que se filtra por el del activo."""
    ana, _ = dos_usuarios

    fijar_usuario(db, ana.id)
    db.expire_all()
    operaciones = db.query(Operation).all()

    assert len(operaciones) == 1
    assert operaciones[0].asset.name == "ACTIVO DE ANA"


def test_no_se_puede_leer_por_id_lo_de_otro(dos_usuarios, db):
    """El caso que un filtro por lista no cubre: pedir un id concreto ajeno.

    Es lo que pasaría con una URL copiada, o probando números a mano."""
    ana, beto = dos_usuarios
    fijar_usuario(db, beto.id)
    db.expire_all()
    ajeno = db.query(Asset).one()  # el de Beto

    fijar_usuario(db, ana.id)
    db.expire_all()

    assert db.query(Asset).filter(Asset.id == ajeno.id).first() is None


def test_las_relaciones_cargadas_tambien_se_filtran(dos_usuarios, db):
    """`selectinload` emite su propia consulta: si el filtro no se propagara,
    traería las operaciones del activo de otro."""
    from sqlalchemy.orm import selectinload

    ana, _ = dos_usuarios
    fijar_usuario(db, ana.id)
    db.expire_all()

    activos = db.query(Asset).options(selectinload(Asset.operations)).all()

    assert len(activos) == 1
    assert all(op.asset_id == activos[0].id for op in activos[0].operations)


# ---------- Escritura ----------

def test_lo_que_se_crea_hereda_el_usuario_de_la_sesion(dos_usuarios, db):
    """Ningún alta tiene que acordarse de poner el dueño, que es lo mismo que
    decir que ninguna puede olvidarse."""
    ana, _ = dos_usuarios
    fijar_usuario(db, ana.id)

    db.add(Transaction(date=date(2026, 2, 1), amount=10,
                       type=TransactionType.GASTO, description="sin dueño explícito"))
    db.commit()

    creada = db.query(Transaction).filter_by(description="sin dueño explícito").one()
    assert creada.usuario_id == ana.id


def test_una_alerta_creada_es_del_usuario_activo(dos_usuarios, db):
    ana, _ = dos_usuarios
    fijar_usuario(db, ana.id)
    db.expire_all()
    activo = db.query(Asset).one()

    db.add(Alerta(asset_id=activo.id, tipo=TipoAlerta.POR_ENCIMA, valor=150.0))
    db.commit()

    assert db.query(Alerta).one().usuario_id == ana.id


def test_los_nombres_repetidos_entre_usuarios_estan_permitidos(dos_usuarios, db):
    """Dos personas pueden tener una cuenta "Revolut" y una categoría "Comida".
    Antes eran unique globales y la segunda reventaba con IntegrityError."""
    fijar_usuario(db, None)
    db.expire_all()

    assert db.query(Account).filter_by(name="Revolut").count() == 2
    assert db.query(Category).filter_by(name="Comida").count() == 2


# ---------- Sesión sin dueño: los jobs ----------

def test_una_sesion_sin_usuario_lo_ve_todo(dos_usuarios, db):
    """Es deliberado: los jobs de fondo recorren a todo el mundo. Que esté
    probado evita que alguien "arregle" el filtro y rompa el refresco de
    precios."""
    fijar_usuario(db, None)
    db.expire_all()

    assert db.query(Asset).count() == 2


def test_recorrer_usuario_a_usuario_acota_cada_pasada(dos_usuarios, db):
    """Lo que hacen los jobs que sí producen un dato por persona: el snapshot de
    patrimonio, la muestra intradía, las recurrentes y las alertas."""
    vistos = []
    for usuario in dos_usuarios:
        fijar_usuario(db, usuario.id)
        db.expire_all()
        vistos.append((usuario.nombre, [a.name for a in db.query(Asset).all()]))

    assert vistos == [("Ana", ["ACTIVO DE ANA"]), ("Beto", ["ACTIVO DE BETO"])]
