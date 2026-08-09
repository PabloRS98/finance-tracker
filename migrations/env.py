"""Entorno de Alembic.

La URL no se lee de alembic.ini: sale de la misma configuración que usa la app
(app.config.settings.db_path), para que migraciones y runtime no puedan apuntar
a bases de datos distintas.

`render_as_batch=True` es obligatorio con SQLite: no soporta ALTER COLUMN, así
que Alembic recrea la tabla y copia los datos (modo "batch").
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app import models  # noqa: F401  registra todos los modelos en Base.metadata
from app.config import settings
from app.database import Base

config = context.config
# Por defecto, la BD de la app. Si quien invoca ya fijó una URL (los tests, o un
# `alembic -x` puntual sobre otra copia), se respeta.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", "sqlite:///%s" % settings.db_path)

# El logging de `alembic.ini` es para cuando Alembic manda: la línea de órdenes
# y el entrypoint del contenedor. Cuando quien migra es la app —`init_db()`, o
# la suite— el logging ya está configurado y esto lo pisaba entero: `fileConfig`
# apaga por defecto todos los loggers existentes, y además el `[logger_root]` de
# ese fichero está en WARN, así que a partir de ahí los `logger.info` de `app.*`
# desaparecían. El daño no es que algo se rompa: es que deja de avisarse de lo
# que se rompe.
#
# `configure_logger` es el interruptor que Alembic prevé para esto; lo apaga
# `app/database.py` al construir su Config.
if config.attributes.get("configure_logger", True) and config.config_file_name is not None:
    # Ni siquiera desde la línea de órdenes tiene sentido silenciar lo que ya
    # hubiera: env.py importa `app.config`, que registra sus propios loggers.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
