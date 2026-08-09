"""[FT-X1] Migrar dentro del proceso no puede dejar mudos los logs de la app.

`fileConfig()` viene con `disable_existing_loggers=True` por defecto: apaga
TODOS los loggers que ya existieran al aplicarse. `migrations/env.py` lo llama
al importarse, así que cualquier `init_db()` en el mismo proceso —los tests, o
un arranque que aplique migraciones— apagaba de paso los loggers de la app.

El daño es que no se nota. La app sigue funcionando; lo que desaparece es el
aviso de que algo va mal: precios que no se actualizan, un envío de Telegram que
falla, una alerta que no sale. Y la suite queda a merced del orden de los tests.
"""
import logging

from app.database import init_db


def test_migrar_no_apaga_los_loggers_de_la_app(tmp_path):
    from sqlalchemy import create_engine

    registro = logging.getLogger("app.services.scheduler")
    registro.disabled = False

    motor = create_engine("sqlite:///%s" % (tmp_path / "prueba.db"))
    init_db(motor)

    assert registro.disabled is False, "migrar dejó mudo un logger de la app"


def test_migrar_no_baja_el_nivel_de_la_raiz(tmp_path):
    """La mitad más dañina: `alembic.ini` tiene la raíz en WARN, que es lo que
    quieres al migrar desde la línea de órdenes y lo contrario de lo que quieres
    dentro de la app, donde `main.py` la pone en INFO."""
    from sqlalchemy import create_engine

    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)

    init_db(create_engine("sqlite:///%s" % (tmp_path / "prueba.db")))

    assert raiz.level == logging.INFO
    assert raiz.isEnabledFor(logging.INFO)


def test_migrar_no_apaga_un_logger_creado_por_un_test(tmp_path, caplog):
    """El caso que rompe la suite: un test configura su logger, otro migra, y a
    partir de ahí el primero deja de capturar nada."""
    from sqlalchemy import create_engine

    registro = logging.getLogger("prueba.ft.x1")
    registro.setLevel(logging.INFO)

    init_db(create_engine("sqlite:///%s" % (tmp_path / "prueba.db")))

    with caplog.at_level(logging.INFO, logger="prueba.ft.x1"):
        registro.info("sigo hablando")

    assert "sigo hablando" in caplog.text
