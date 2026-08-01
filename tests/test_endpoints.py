"""Tests HTTP de la app real: que cada página responda, que la autenticación
cubra TODAS las rutas y que ningún dato guardado pueda romper el HTML.

Complementan a los tests de servicios: aquí se ejerce el cableado (routers,
plantillas, dependencias), que es justo donde se habían colado los fallos que
los tests unitarios no podían ver.
"""
from datetime import date

import pytest

from app.config import settings
from app.models import (
    Account, AccountKind, Asset, AssetType, Category, Currency, Operation, OperationType,
    RecurringTransaction, Transaction, TransactionStatus, TransactionType,
)

# Todas las páginas HTML de la app. /cuentas no está: es POST-only a propósito
# (las cuentas se gestionan desde /operaciones), y se cubre aparte.
PAGINAS = [
    "/",
    "/activos",
    "/operaciones",
    "/operaciones/importar",
    "/transacciones",
    "/categorias",
    "/analisis",
    "/recurrentes",
]


@pytest.fixture
def con_datos(client):
    """Una cartera mínima pero representativa: activo con operaciones, gasto,
    categoría con presupuesto, cuenta y regla recurrente."""
    db = client.db
    cuenta = Account(name="Trade Republic", kind=AccountKind.BROKER)
    categoria = Category(name="Comida", keywords="supermercado", budget_limit=300.0)
    db.add_all([cuenta, categoria])
    db.flush()

    activo = Asset(
        name="Apple", asset_type=AssetType.ACCION, currency=Currency.USD,
        ticker="AAPL", current_price=200.0, previous_close=190.0,
    )
    db.add(activo)
    db.flush()
    db.add(Operation(
        asset_id=activo.id, account_id=cuenta.id, type=OperationType.COMPRA,
        date=date(2026, 1, 15), quantity=10.0, unit_price=150.0, fee=1.0,
        status=TransactionStatus.CONFIRMADO,
    ))
    db.add(Transaction(
        date=date.today(), type=TransactionType.GASTO, category_id=categoria.id,
        amount=42.5, description="supermercado", status=TransactionStatus.CONFIRMADO,
    ))
    db.add(RecurringTransaction(
        name="Alquiler", type=TransactionType.GASTO, amount=800.0,
        currency=Currency.EUR, day_of_month=1, start_date=date(2026, 1, 1),
    ))
    db.commit()
    return client


# ---------- Las páginas responden ----------

@pytest.mark.parametrize("ruta", PAGINAS)
def test_paginas_responden_vacias(client, ruta):
    """Sin ningún dato: los estados vacíos no deben reventar."""
    assert client.get(ruta).status_code == 200


@pytest.mark.parametrize("ruta", PAGINAS)
def test_paginas_responden_con_datos(con_datos, ruta):
    assert con_datos.get(ruta).status_code == 200


def test_ficha_de_activo(con_datos):
    asset_id = con_datos.db.query(Asset).one().id
    assert con_datos.get("/activos/%d" % asset_id).status_code == 200


def test_activo_inexistente_redirige_sin_romper(client):
    resp = client.get("/activos/9999", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/activos"


def test_salud(client, monkeypatch):
    """Con el esquema al día y la base consultable, 200. El detalle de qué
    comprueba está en tests/test_salud_y_errores.py."""
    from app import main

    monkeypatch.setattr(main, "revision_pendiente", lambda bind=None: ("head", "head"))

    assert client.get("/salud").json() == {"status": "ok"}


def test_exportar_csv(con_datos):
    resp = con_datos.get("/transacciones/exportar")
    assert resp.status_code == 200
    assert "supermercado" in resp.text


# ---------- Autenticación ----------

@pytest.fixture
def con_auth(monkeypatch):
    monkeypatch.setattr(settings, "enable_auth", True)
    monkeypatch.setattr(settings, "auth_username", "pablo")
    monkeypatch.setattr(settings, "auth_password", "secreto")


@pytest.mark.parametrize("ruta", PAGINAS)
def test_paginas_piden_credenciales(client, con_auth, ruta):
    assert client.get(ruta).status_code == 401


def test_refresh_prices_pide_credenciales(client, con_auth, monkeypatch):
    """Regresión: este endpoint vivía en main.py fuera de los routers y era el
    único hueco sin auth de toda la app."""
    import app.main

    # No-op para que, si la auth fallara, el test dé un 401/200 limpio en vez de
    # reventar por dentro al intentar refrescar precios de verdad
    monkeypatch.setattr(app.main, "update_all_prices", lambda: None)

    # Con el token CSRF puesto, para que lo que falle sea la autenticación
    resp = client.post("/api/refresh-prices", headers={"X-CSRF-Token": client.csrf()})

    assert resp.status_code == 401


def test_cuentas_pide_credenciales(client, con_auth):
    """/cuentas es POST-only; su router también debe estar cubierto."""
    assert client.post_form("/cuentas", data={"name": "X"}).status_code == 401


def test_salud_no_pide_credenciales(client, con_auth):
    """El healthcheck de Docker no lleva credenciales: debe seguir abierto.

    Se comprueba que no sea 401, no que sea 200: /salud devuelve 503 cuando la
    app no puede servir páginas, y eso también es una respuesta sin auth."""
    assert client.get("/salud").status_code != 401


def test_credenciales_correctas_dan_acceso(client, con_auth):
    assert client.get("/", auth=("pablo", "secreto")).status_code == 200


def test_credenciales_incorrectas_rechazadas(client, con_auth):
    assert client.get("/", auth=("pablo", "otra")).status_code == 401


# ---------- Escapado: ningún dato guardado puede romper el HTML ----------

ROMPE_SCRIPT = '</script><script>alert(1)</script>'


def test_nombre_de_categoria_no_rompe_el_script_del_dashboard(client):
    """Regresión: las etiquetas de las gráficas se incrustan dentro de un
    bloque <script>. Un `tojson` sin escapar dejaba cerrar la etiqueta."""
    db = client.db
    categoria = Category(name=ROMPE_SCRIPT, keywords="")
    db.add(categoria)
    db.flush()
    db.add(Transaction(
        date=date.today(), type=TransactionType.GASTO, category_id=categoria.id,
        amount=10.0, description="x", status=TransactionStatus.CONFIRMADO,
    ))
    db.commit()

    html = client.get("/").text

    assert ROMPE_SCRIPT not in html
    assert "\\u003c/script\\u003e" in html


def test_region_de_activo_no_rompe_el_script_de_analisis(client):
    db = client.db
    activo = Asset(
        name="Raro", asset_type=AssetType.ACCION, currency=Currency.EUR,
        ticker="RARO", current_price=10.0, quantity=1.0, region=ROMPE_SCRIPT,
    )
    db.add(activo)
    db.commit()

    html = client.get("/analisis").text

    assert ROMPE_SCRIPT not in html


# ---------- Mutaciones ----------

def test_crear_categoria(client):
    resp = client.post_form(
        "/categorias", data={"name": "Ocio", "keywords": "cine", "budget_limit": "100"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert client.db.query(Category).filter(Category.name == "Ocio").one().budget_limit == 100.0


def test_crear_transaccion(client):
    resp = client.post_form(
        "/transacciones",
        data={"date": "2026-07-01", "type": "gasto", "amount": "25.5", "description": "test"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert client.db.query(Transaction).one().amount == pytest.approx(25.5)


def test_eliminar_transaccion(con_datos):
    tx_id = con_datos.db.query(Transaction).one().id
    con_datos.post_form("/transacciones/%d/eliminar" % tx_id, follow_redirects=False)
    assert con_datos.db.query(Transaction).count() == 0


def test_importe_vacio_no_es_error_500(client):
    """El OptFloat de forms.py convierte "" en None; un campo opcional vacío
    (lo que manda el navegador) no debe acabar en 422."""
    resp = client.post_form(
        "/categorias", data={"name": "Sin límite", "keywords": "", "budget_limit": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert client.db.query(Category).filter(Category.name == "Sin límite").one().budget_limit is None


# ---------- Voz: la divisa dictada se convierte ----------

def test_voz_en_dolares_convierte_a_la_base(client, monkeypatch):
    """Regresión: parse_voice_text detectaba la divisa pero el endpoint la
    ignoraba y apuntaba 20 USD como 20 EUR."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.9)

    resp = client.post_json("/transacciones/voz", json={"text": "gasté 20 dólares en comida"})

    assert resp.json()["ok"] is True
    assert client.db.query(Transaction).one().amount == pytest.approx(18.0)


def test_voz_sin_tipo_de_cambio_no_apunta_nada(client, monkeypatch):
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: None)

    resp = client.post_json("/transacciones/voz", json={"text": "gasté 20 dólares en comida"})

    assert resp.json()["ok"] is False
    assert client.db.query(Transaction).count() == 0


def test_categoria_duplicada_avisa_en_vez_de_500(client):
    """Category.name es UNIQUE: repetir nombre debe dar un aviso, no un error 500."""
    client.post_form("/categorias", data={"name": "Ocio", "keywords": ""}, follow_redirects=False)

    resp = client.post_form("/categorias", data={"name": "Ocio", "keywords": ""}, follow_redirects=False)

    assert resp.status_code == 303
    assert client.db.query(Category).filter(Category.name == "Ocio").count() == 1


def test_categoria_sin_nombre_avisa(client):
    resp = client.post_form("/categorias", data={"name": "   ", "keywords": ""}, follow_redirects=False)

    assert resp.status_code == 303
    assert client.db.query(Category).count() == 0


# ---------- CSRF ----------

def test_post_sin_token_es_rechazado(client):
    """Es el escenario del ataque: otra pestaña manda el POST y el navegador
    adjunta las credenciales Basic por su cuenta. Sin el token, no pasa."""
    client.get("/categorias")  # el navegador ya tiene la cookie csrftoken

    resp = client.post("/categorias", data={"name": "Inyectada", "keywords": ""})

    assert resp.status_code == 403
    assert client.db.query(Category).count() == 0


def test_post_con_token_falso_es_rechazado(client):
    client.get("/categorias")

    resp = client.post("/categorias", data={"name": "Inyectada", "_csrf": "inventado"})

    assert resp.status_code == 403
    assert client.db.query(Category).count() == 0


def test_borrado_sin_token_es_rechazado(con_datos):
    """El caso que más duele: un DELETE encubierto desde otro sitio."""
    tx_id = con_datos.db.query(Transaction).one().id

    resp = con_datos.post("/transacciones/%d/eliminar" % tx_id)

    assert resp.status_code == 403
    assert con_datos.db.query(Transaction).count() == 1


def test_json_sin_cabecera_es_rechazado(client):
    resp = client.post("/transacciones/voz", json={"text": "gasté 20 euros"})

    assert resp.status_code == 403
    assert client.db.query(Transaction).count() == 0


def test_get_no_necesita_token(client):
    """Los métodos seguros no se validan: navegar no debe pedir nada."""
    assert client.get("/categorias").status_code == 200


def test_la_cookie_del_token_es_httponly(client):
    """HttpOnly: un XSS futuro no podría leer el token del almacén de cookies.
    El JS propio lo saca del <meta>, no de la cookie."""
    resp = client.get("/")

    cookie = next(h for h in resp.headers.get_list("set-cookie") if h.startswith("csrftoken="))
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie


def test_las_plantillas_incluyen_el_token(client):
    """Si un formulario se quedara sin `csrf_input`, dejaría de funcionar."""
    html = client.get("/categorias").text

    assert 'name="_csrf"' in html
    assert 'name="csrf-token"' in html  # el <meta> para fetch
