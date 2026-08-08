"""[FT-M12] Listas mutables como valor por defecto en dos endpoints.

    def merge_assets(..., origen_ids: list[int] = Form([]), ...)
    def import_confirm(..., rows: list[str] = Form([]), ...)

FastAPI trata `Form([])` como una declaración de parámetro y construye una lista
nueva en cada petición, así que **hoy no hay bug**. Estos tests lo fijan: lo que
se prueba no es que la lista compartida falle, sino que **no se comparte**, para
que el día que alguien copie la firma a una función normal —donde sí se
compartiría— el cambio de comportamiento salte aquí.

Es la clase de defecto que no se ve en revisión y que se diagnostica fatal: los
datos de una petición aparecen en la siguiente.
"""
from app.models import Asset, AssetType, Currency

RUTA_FUSION = "/activos/duplicados/fusionar"
RUTA_CONFIRMAR = "/operaciones/importar/confirmar"


def _activo(client, nombre):
    a = Asset(name=nombre, asset_type=AssetType.ACCION, ticker=nombre, currency=Currency.EUR)
    client.db.add(a)
    client.db.commit()
    return a


def test_fusionar_sin_origenes_no_arrastra_los_de_la_peticion_anterior(client):
    """Si la lista por defecto se compartiera entre peticiones, la segunda
    llegaría con el id de la primera dentro y se llevaría por delante un activo
    que nadie pidió fusionar."""
    # Los tres se crean antes de tocar la app: intercalar escrituras de esta
    # sesión con las del endpoint hace saltar un aviso del mapa de identidad de
    # SQLAlchemy que no tiene que ver con lo que se está probando.
    destino = _activo(client, "DESTINO")
    origen = _activo(client, "ORIGEN")
    _activo(client, "INTACTO")

    client.post_form(RUTA_FUSION,
                     data={"destino_id": destino.id, "origen_ids": [origen.id]},
                     follow_redirects=False)

    # Segunda petición sin el campo. Lo que se comprueba es que no reaparezca
    # nada de la anterior, no que la fusión en sí funcione (eso lo cubre
    # test_fusion.py, con sus propias guardas de divisa y de posición cerrada).
    client.post_form(RUTA_FUSION,
                     data={"destino_id": destino.id},
                     follow_redirects=False)

    client.db.expire_all()
    assert client.db.query(Asset).filter_by(name="INTACTO").count() == 1


def test_confirmar_importacion_sin_filas_no_arrastra_las_anteriores(client):
    fila = (
        '{"date": "2026-01-01", "op_type": "compra", "name": "MUESTRA", '
        '"ticker": "MSTR", "quantity": 1.0, "unit_price": 10.0, "currency": "EUR", '
        '"fee": 0.0, "asset_type": "accion"}'
    )

    client.post_form(RUTA_CONFIRMAR,
                     data={"rows": [fila]}, follow_redirects=False)
    creadas_primera = client.db.query(Asset).count()

    client.post_form(RUTA_CONFIRMAR,
                     data={}, follow_redirects=False)

    client.db.expire_all()
    assert client.db.query(Asset).count() == creadas_primera, "la segunda no debe crear nada"


def test_las_firmas_no_usan_una_lista_literal_como_default():
    """Lo que ruff marcaría con B006 si reconociera `Form(...)` como default.

    No lo hace: entiende que es una declaración de parámetro de FastAPI. Así que
    el patrón que el informe daba por cubierto por el linter se fija aquí."""
    from pathlib import Path

    raiz = Path(__file__).resolve().parent.parent
    for fichero in ("app/routers/assets.py", "app/routers/imports.py"):
        texto = (raiz / fichero).read_text(encoding="utf-8")

        assert "Form([])" not in texto, "%s sigue usando una lista literal" % fichero
        assert "Form({})" not in texto
