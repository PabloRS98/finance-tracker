#!/bin/sh
# Ajusta la propiedad de /data y baja de privilegios antes de arrancar la app.
#
# El chown del Dockerfile solo afecta a la imagen: cuando Docker monta encima un
# volumen que ya existe, sus ficheros conservan la propiedad que tuvieran. Los
# despliegues anteriores a que la app corriera sin privilegios dejaron /data en
# manos de root, así que el usuario "finance" no podía ni abrir la base de datos
# y el contenedor entraba en bucle de reinicio con "attempt to write a readonly
# database". Por eso el ajuste se hace aquí, en cada arranque, y no en el build.
#
# setpriv viene en la imagen base (util-linux), así que no hace falta gosu.
#
# Aquí se aplican también las migraciones, antes de arrancar uvicorn. Hacerlo
# dentro del lifespan de FastAPI podía quedarse esperando un lock de SQLite y
# dejaba el arranque colgado sin explicar por qué; en este punto no hay servidor
# ni scheduler ni bot tocando la base, así que no hay con quién competir. Si una
# migración falla, `set -e` corta el arranque en vez de dejar la app sirviendo
# contra un esquema viejo.
set -e

# Se llama a init_db() y no a `alembic upgrade head` a secas: una base anterior
# a Alembic no tiene tabla alembic_version, así que upgrade la trata como vacía
# e intenta crear tablas que ya existen ("table accounts already exists").
# init_db() detecta ese caso, le completa las columnas que le falten y la marca
# antes de migrar.
MIGRAR='from app.database import init_db; init_db()'

if [ "$(id -u)" = "0" ]; then
    chown -R finance:finance /data
    setpriv --reuid=finance --regid=finance --init-groups python -c "$MIGRAR"
    exec setpriv --reuid=finance --regid=finance --init-groups "$@"
fi

# Ya se arrancó sin privilegios (por ejemplo con `docker run --user`): no hay
# nada que ajustar, y forzarlo fallaría.
python -c "$MIGRAR"
exec "$@"
