"""Healthcheck que no miente y páginas de error con la cara de la app.

El healthcheck antiguo devolvía {"status": "ok"} sin tocar la base de datos. Con
una columna presente en el modelo y ausente en la base, Docker daba el
contenedor por sano mientras todas las páginas devolvían 500. Duró semanas.

La comprobación tiene que ser la misma que hace una página: consultar por el
ORM, que emite un SELECT con todas las columnas mapeadas.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import _problemas_para_servir, app


@pytest.fixture
def esquema_al_dia(monkeypatch):
    """La base de los tests se crea con `create_all`, no migrando, así que no
    tiene `alembic_version` y el healthcheck la vería atrasada con razón. Aquí
    se prueba la otra mitad: la consulta real contra el modelo."""
    from app import main

    monkeypatch.setattr(main, "revision_pendiente", lambda bind=None: ("head", "head"))


# ---------- Healthcheck ----------

def test_con_todo_bien_responde_ok(client, esquema_al_dia):
    respuesta = client.get("/salud")

    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_no_pide_autenticacion(client, esquema_al_dia, monkeypatch):
    """Docker no lleva credenciales: si /salud exigiera auth, el contenedor
    quedaría siempre unhealthy."""
    from app import auth

    monkeypatch.setattr(auth.settings, "enable_auth", True)

    assert client.get("/salud").status_code == 200


def test_una_columna_que_falta_deja_el_contenedor_unhealthy(client, esquema_al_dia):
    """El fallo real que estuvo semanas sin detectarse.

    La conexión va bien y la tabla existe; lo que falta es una columna que el
    modelo sí declara. Un `SELECT 1` habría dicho que todo está en orden.
    """
    client.db.execute(text("ALTER TABLE assets DROP COLUMN avg_cost_override"))
    client.db.commit()

    respuesta = client.get("/salud")

    assert respuesta.status_code == 503
    assert respuesta.json()["status"] == "degradado"
    assert "consulta de prueba fallida" in respuesta.json()["problemas"]


def test_sin_tablas_tambien_avisa(client, esquema_al_dia):
    client.db.execute(text("DROP TABLE assets"))
    client.db.commit()

    assert client.get("/salud").status_code == 503


def test_el_detalle_del_fallo_no_sale_en_la_respuesta(client, esquema_al_dia):
    """La ruta no pide credenciales: el porqué va al log, no al navegador."""
    client.db.execute(text("DROP TABLE assets"))
    client.db.commit()

    cuerpo = client.get("/salud").text

    assert "Traceback" not in cuerpo and "sqlite3" not in cuerpo.lower()


def test_un_esquema_atrasado_cuenta_como_problema(client, monkeypatch):
    from app import main

    monkeypatch.setattr(main, "revision_pendiente", lambda bind=None: ("abc123", "def456"))

    assert "esquema desactualizado" in _problemas_para_servir(client.db)


def test_una_base_sin_migrar_sale_atrasada(client):
    """Sin patch: la base de los tests no tiene `alembic_version`, que es justo
    el estado de una base anterior a Alembic. Debe contarse como problema."""
    assert "esquema desactualizado" in _problemas_para_servir(client.db)


def test_si_no_se_puede_leer_la_revision_tambien(client, monkeypatch):
    from app import main

    def revienta(bind=None):
        raise RuntimeError("sin alembic_version")

    monkeypatch.setattr(main, "revision_pendiente", revienta)

    assert "esquema ilegible" in _problemas_para_servir(client.db)


# ---------- Páginas de error ----------

def test_una_ruta_que_no_existe_devuelve_la_pagina_de_la_app(client):
    respuesta = client.get("/esto-no-existe")

    assert respuesta.status_code == 404
    assert "Esta página no existe" in respuesta.text
    assert 'href="/static/css/style.css"' in respuesta.text
    assert "Volver al inicio" in respuesta.text


def test_la_pagina_de_error_no_depende_de_la_plantilla_base(client):
    """Si el error viene de la base de datos, una página que extienda base.html
    fallaría al pintarse y volveríamos al texto plano de Starlette."""
    client.db.execute(text("DROP TABLE assets"))
    client.db.commit()

    respuesta = client.get("/no-existe-nada")

    assert respuesta.status_code == 404
    assert "Esta página no existe" in respuesta.text


@pytest.fixture
def cliente_que_no_relanza(client):
    """TestClient deja escapar las excepciones por defecto, así que sin esto no
    se llega a ver la respuesta que recibiría un navegador."""
    sin_relanzar = TestClient(app, raise_server_exceptions=False)
    sin_relanzar.cookies = client.cookies
    yield sin_relanzar


def test_una_excepcion_sin_capturar_da_500_con_la_pagina(cliente_que_no_relanza, monkeypatch):
    from app.routers import dashboard

    def revienta(*args, **kwargs):
        raise RuntimeError("fallo de prueba")

    monkeypatch.setattr(dashboard, "portfolio_totals", revienta)

    respuesta = cliente_que_no_relanza.get("/")

    assert respuesta.status_code == 500
    assert "Algo ha fallado" in respuesta.text
    assert "fallo de prueba" not in respuesta.text, "la traza va al log, no al navegador"
