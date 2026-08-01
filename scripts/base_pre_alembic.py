"""Crea en /data una base como las de antes de Alembic, para probar el arranque.

Reproduce el estado que rompió el despliegue real: el esquema de la v3, sin
tabla `alembic_version` y sin la columna que se añadió al modelo después. Un
`alembic upgrade` sobre una base así la trata como vacía e intenta crear tablas
que ya existen; `init_db()` tiene que detectarlo y ponerla al día sola.

No forma parte de la aplicación: lo usa el job de despliegue del CI, que lo
monta dentro del contenedor. Por eso no se copia en la imagen.

Hace falta `PYTHONPATH`: al vivir fuera del paquete, Python pone en `sys.path`
la carpeta del script y no la raíz del proyecto, así que `app` no se encuentra.

Uso: PYTHONPATH=/app DB_PATH=/data/finance.db python /scripts/base_pre_alembic.py
"""
import sys

from alembic import command
from sqlalchemy import text

from app import models  # noqa: F401  registra los modelos en Base
from app.database import REVISION_INICIAL, _config_alembic, engine


def main() -> int:
    command.upgrade(_config_alembic(engine), REVISION_INICIAL)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE alembic_version"))
        conn.execute(text("ALTER TABLE assets DROP COLUMN avg_cost_override"))
        # Una fila para que el arranque tenga algo que leer y la migración algo
        # que conservar: una base vacía pasaría comprobaciones que esta no.
        # created_at lo pone el modelo con un default de Python, que aquí no
        # interviene porque se inserta con SQL a pelo.
        conn.execute(text(
            "INSERT INTO assets (name, asset_type, currency, quantity, current_price, created_at) "
            "VALUES ('Activo de prueba', 'accion_etf_fondo', 'EUR', 1, 100, CURRENT_TIMESTAMP)"
        ))
    print("base anterior a Alembic creada en", engine.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
