"""[FT-A2] Las alertas escapan el HTML y no se marcan si el envío falla.

`telegram.send_message` manda siempre con `parse_mode: "HTML"`. `telegram_bot`
es escrupuloso con esto y tiene `_esc()` aplicada en sus 8 interpolaciones;
`alertas.py` no la usaba en ninguna.

Los nombres de activo se autorrellenan desde Yahoo, así que llegan nombres
reales del mercado con `&`. Telegram devuelve entonces
`400 Bad Request: can't parse entities`, `send_message` se traga la excepción y
devuelve None... y la alerta ya se había marcado como disparada. Resultado: la
alerta no llega nunca y no vuelve a intentarse hasta que la condición se rearme.
"""
from app.models import Alerta, Asset, AssetType, Currency, TipoAlerta
from app.services import alertas


def _activo_con_ampersand(db):
    asset = Asset(name="AT&T Inc.", asset_type=AssetType.ACCION, currency=Currency.USD,
                  ticker="T", current_price=125.0, previous_close=100.0)
    db.add(asset)
    db.commit()
    return asset


def _alerta(db, asset, tipo=TipoAlerta.POR_ENCIMA, valor=120.0):
    a = Alerta(asset_id=asset.id, tipo=tipo, valor=valor)
    db.add(a)
    db.commit()
    return a


# ---------- Escapado ----------

def test_alerta_escapa_el_nombre_del_activo(db):
    asset = _activo_con_ampersand(db)
    alerta = _alerta(db, asset)

    texto = alertas.mensaje(alerta)

    assert "AT&amp;T Inc." in texto
    assert "AT&T" not in texto


def test_escapa_en_las_tres_ramas(db):
    """Las tres condiciones construyen su propio mensaje por separado."""
    asset = _activo_con_ampersand(db)

    for tipo, valor in (
        (TipoAlerta.POR_ENCIMA, 120.0),
        (TipoAlerta.POR_DEBAJO, 130.0),
        (TipoAlerta.CAIDA_DIARIA, 5.0),
    ):
        texto = alertas.mensaje(Alerta(asset_id=asset.id, tipo=tipo, valor=valor, asset=asset))

        assert "AT&amp;T Inc." in texto, "la rama %s no escapa" % tipo.value


def test_no_se_escapa_el_apostrofo(db):
    """Telegram solo decodifica &lt; &gt; &amp; y &quot;, no las entidades
    numéricas: escapar el apóstrofo dejaría "Delaney&#x27;s" tal cual."""
    asset = Asset(name="Delaney's Corporation", asset_type=AssetType.ACCION,
                  currency=Currency.EUR, ticker="DLN", current_price=125.0)
    db.add(asset)
    db.commit()

    texto = alertas.mensaje(_alerta(db, asset))

    assert "Delaney's Corporation" in texto


# ---------- Marcado condicionado al envío ----------

def test_alerta_no_se_marca_si_el_envio_falla(db, monkeypatch):
    asset = _activo_con_ampersand(db)
    alerta = _alerta(db, asset)

    from app.services import telegram
    monkeypatch.setattr(telegram, "is_configured", lambda: True)
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: None)

    enviadas = alertas.comprobar_y_enviar(db)

    assert enviadas == 0
    assert alerta.ultimo_disparo is None, "sin envío no hay marca: el próximo ciclo reintenta"


def test_el_siguiente_ciclo_reintenta_lo_que_no_se_envio(db, monkeypatch):
    asset = _activo_con_ampersand(db)
    alerta = _alerta(db, asset)

    from app.services import telegram
    monkeypatch.setattr(telegram, "is_configured", lambda: True)
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: None)
    alertas.comprobar_y_enviar(db)

    enviados = []
    monkeypatch.setattr(telegram, "send_message", lambda texto, *a, **k: enviados.append(texto) or {"ok": True})
    reintentadas = alertas.comprobar_y_enviar(db)

    assert reintentadas == 1
    assert alerta.ultimo_disparo is not None
    assert len(enviados) == 1


def test_alerta_se_marca_cuando_el_envio_funciona(db, monkeypatch):
    asset = _activo_con_ampersand(db)
    alerta = _alerta(db, asset)

    from app.services import telegram
    monkeypatch.setattr(telegram, "is_configured", lambda: True)
    monkeypatch.setattr(telegram, "send_message", lambda *a, **k: {"message_id": 1})

    assert alertas.comprobar_y_enviar(db) == 1
    assert alerta.ultimo_disparo is not None

    # Y no repite mientras la condición siga cumpliéndose
    assert alertas.comprobar_y_enviar(db) == 0


def test_sin_telegram_configurado_no_se_marca_nada(db, monkeypatch):
    """Si no hay bot, no se ha avisado: marcar sería perder el aviso el día que
    se configure."""
    asset = _activo_con_ampersand(db)
    alerta = _alerta(db, asset)

    from app.services import telegram
    monkeypatch.setattr(telegram, "is_configured", lambda: False)

    assert alertas.comprobar_y_enviar(db) == 0
    assert alerta.ultimo_disparo is None
