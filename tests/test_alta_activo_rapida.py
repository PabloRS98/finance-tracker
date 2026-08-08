"""[FT-M3] El alta de un activo esperaba al precio dentro de la petición.

`_fetch_price` hace una petición a Yahoo o CoinGecko con **15 s de timeout**, y
puede encadenar otra por la clasificación automática: hasta medio minuto con el
navegador colgado después de pulsar "Añadir".

Lo que **no** se mueve a segundo plano es la comprobación de que el ticker
existe. Avisar de un ticker mal escrito solo sirve en el momento de escribirlo,
así que esa se queda en la petición, con una llamada ligera — el mismo criterio
que ya seguían `add_to_watchlist` y `create_benchmark`.
"""
import time

import pytest

from app.models import Asset, AssetType
from app.routers import assets as router_activos


@pytest.fixture
def precio_lento(monkeypatch):
    """El precio completo tarda; la validación del ticker no."""
    llamadas = {"completo": 0}

    def _lento(asset):
        llamadas["completo"] += 1
        time.sleep(1.5)
        return False

    monkeypatch.setattr(router_activos, "_fetch_price", _lento)
    monkeypatch.setattr(router_activos, "_ticker_existe", lambda tipo, ticker: True)
    return llamadas


def test_el_precio_se_delega_y_no_se_busca_dentro_de_la_peticion(client, monkeypatch):
    """El alta ya no llama a `_fetch_price`: la encola.

    No se mide con un cronómetro. `TestClient` ejecuta las tareas de fondo
    dentro del mismo ciclo, así que el reloj marcaría casi lo mismo con y sin
    el arreglo; y en producción lo que importa no es el reloj del servidor sino
    que el navegador reciba la página sin esperar a Yahoo. Lo que sí distingue
    una cosa de otra, y de forma inequívoca, es quién llama a qué.
    """
    encoladas = []
    dentro_de_la_peticion = []

    monkeypatch.setattr(router_activos, "buscar_precio_en_segundo_plano",
                        lambda asset_id: encoladas.append(asset_id))
    monkeypatch.setattr(router_activos, "_fetch_price",
                        lambda asset: dentro_de_la_peticion.append(1))
    monkeypatch.setattr(router_activos, "_ticker_existe", lambda tipo, ticker: True)

    respuesta = client.post_form("/activos", data={
        "name": "MUESTRA", "asset_type": "accion_etf_fondo", "currency": "EUR", "ticker": "MSTR",
    }, follow_redirects=False)

    assert respuesta.status_code == 303
    assert dentro_de_la_peticion == [], "el precio se sigue buscando dentro de la petición"
    assert len(encoladas) == 1, "no se encoló la búsqueda del precio"
    assert encoladas[0] == client.db.query(Asset).filter_by(ticker="MSTR").one().id


def test_editar_el_ticker_tambien_delega(client, monkeypatch):
    """Editar el ticker costaba lo mismo que crear el activo."""
    encoladas = []
    monkeypatch.setattr(router_activos, "buscar_precio_en_segundo_plano",
                        lambda asset_id: encoladas.append(asset_id))
    monkeypatch.setattr(router_activos, "_fetch_price", lambda asset: False)
    activo = Asset(name="MUESTRA", asset_type=AssetType.ACCION, ticker="VIEJO")
    client.db.add(activo)
    client.db.commit()

    client.post_form("/activos/%d/editar" % activo.id, data={
        "name": "MUESTRA", "asset_type": "accion_etf_fondo", "currency": "EUR", "ticker": "NUEVO",
    }, follow_redirects=False)

    assert encoladas == [activo.id]


def test_el_aviso_dice_que_el_precio_viene_luego(client, precio_lento):
    """Lo que ve el usuario cambia, y tiene que decir la verdad: el activo está
    añadido y el precio se está buscando."""
    respuesta = client.post_form("/activos", data={
        "name": "MUESTRA", "asset_type": "accion_etf_fondo", "currency": "EUR", "ticker": "MSTR",
    }, follow_redirects=False)

    import urllib.parse
    aviso = urllib.parse.unquote(respuesta.cookies.get("flash", ""))

    assert "buscando" in aviso.lower()


def test_el_activo_queda_guardado_aunque_el_precio_llegue_despues(client, precio_lento):
    client.post_form("/activos", data={
        "name": "MUESTRA", "asset_type": "accion_etf_fondo", "currency": "EUR", "ticker": "MSTR",
    }, follow_redirects=False)

    assert client.db.query(Asset).filter_by(ticker="MSTR").count() == 1


def test_un_ticker_invalido_se_avisa_en_el_momento(client, monkeypatch):
    """El aviso útil no se pierde al pasar el precio a segundo plano."""
    monkeypatch.setattr(router_activos, "_ticker_existe", lambda tipo, ticker: False)
    monkeypatch.setattr(router_activos, "_fetch_price", lambda asset: False)

    respuesta = client.post_form("/activos", data={
        "name": "MUESTRA", "asset_type": "accion_etf_fondo", "currency": "EUR", "ticker": "NOEXISTE",
    }, follow_redirects=False)

    import urllib.parse
    aviso = urllib.parse.unquote(respuesta.cookies.get("flash", ""))

    assert "ticker" in aviso.lower()
    # Y aun así se guarda: el usuario lo corrige editando, no repitiendo el alta
    assert client.db.query(Asset).filter_by(ticker="NOEXISTE").count() == 1


def test_un_activo_manual_no_pide_ningun_precio(client, monkeypatch):
    """Una cuenta o un inmueble no tienen cotización que buscar."""
    pedidos = []
    monkeypatch.setattr(router_activos, "_ticker_existe",
                        lambda tipo, ticker: pedidos.append(1) or True)

    client.post_form("/activos", data={
        "name": "CUENTA", "asset_type": "inmueble_otro", "currency": "EUR", "manual_value": "1000",
    }, follow_redirects=False)

    assert pedidos == []


def test_la_tarea_de_fondo_sobrevive_a_que_borren_el_activo(client, monkeypatch):
    """Se puede borrar entre el alta y la tarea; no puede reventar por eso."""
    monkeypatch.setattr(router_activos, "_fetch_price", lambda asset: False)

    router_activos.buscar_precio_en_segundo_plano(999999)  # no existe


def test_la_tarea_de_fondo_se_traga_los_fallos(client, monkeypatch):
    """La petición ya se respondió y el activo ya está guardado: un fallo aquí
    no puede tumbar nada, y el job de precios lo reintentará."""
    def _revienta(asset):
        raise RuntimeError("Yahoo no responde")

    monkeypatch.setattr(router_activos, "_fetch_price", _revienta)
    activo = Asset(name="MUESTRA", asset_type=AssetType.ACCION, ticker="MSTR")
    client.db.add(activo)
    client.db.commit()

    router_activos.buscar_precio_en_segundo_plano(activo.id)  # no propaga
