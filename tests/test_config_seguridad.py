"""[FT-C1] La app no debe poder arrancar en una combinación insegura.

Tres decisiones que por separado son defendibles se sumaban en algo que no lo
era: el puerto publicado en todas las interfaces, la autenticación desactivada
de fábrica, y nada que impidiera arrancar con admin/changeme. Cualquiera en la
misma red Wi-Fi tenía lectura y escritura del patrimonio entero.

Un fallo al arrancar es ruidoso y se corrige en un minuto; una app expuesta con
la contraseña de fábrica puede pasar meses sin que nadie lo note.
"""
import pytest
from pydantic import ValidationError

from app.config import DEFAULT_PASSWORD, MIN_PASSWORD_LENGTH, Settings


def _construir(monkeypatch, **entorno) -> Settings:
    """Settings como al arrancar, pero sin leer el .env real de la máquina."""
    for clave, valor in entorno.items():
        monkeypatch.setenv(clave, valor)
    return Settings(_env_file=None)


def test_no_arranca_con_contrasena_de_fabrica(monkeypatch):
    with pytest.raises(ValidationError) as fallo:
        _construir(monkeypatch, ENABLE_AUTH="true", AUTH_PASSWORD=DEFAULT_PASSWORD)

    assert "AUTH_PASSWORD" in str(fallo.value)


def test_no_arranca_con_contrasena_corta(monkeypatch):
    with pytest.raises(ValidationError):
        _construir(monkeypatch, ENABLE_AUTH="true", AUTH_PASSWORD="corta")


def test_arranca_con_una_contrasena_decente(monkeypatch):
    ajustes = _construir(monkeypatch, ENABLE_AUTH="true", AUTH_PASSWORD="una-contraseña-larga")

    assert ajustes.enable_auth is True


def test_sin_autenticacion_la_contrasena_de_fabrica_no_bloquea(monkeypatch):
    """Con ENABLE_AUTH desactivado la contraseña no se usa para nada.

    Importa: si el validador se aplicara siempre, cualquier instalación
    existente -que arranca sin auth y con el default- dejaría de levantar al
    actualizar."""
    ajustes = _construir(monkeypatch, ENABLE_AUTH="false", AUTH_PASSWORD=DEFAULT_PASSWORD)

    assert ajustes.enable_auth is False


def test_el_minimo_de_longitud_es_el_declarado(monkeypatch):
    """El límite lo fija la constante, no un número suelto en el validador."""
    justa = "a" * MIN_PASSWORD_LENGTH

    assert _construir(monkeypatch, ENABLE_AUTH="true", AUTH_PASSWORD=justa).auth_password == justa

    with pytest.raises(ValidationError):
        _construir(monkeypatch, ENABLE_AUTH="true", AUTH_PASSWORD="a" * (MIN_PASSWORD_LENGTH - 1))


# ---------- Aviso de arranque ----------

def test_avisa_cuando_la_app_queda_sin_autenticacion(monkeypatch, caplog):
    """Sin auth no hay nada en la interfaz que lo diga: el log es el único sitio."""
    import logging

    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "enable_auth", False)
    with caplog.at_level(logging.WARNING):
        main.avisar_si_no_hay_autenticacion()

    assert "ENABLE_AUTH" in caplog.text


def test_no_avisa_cuando_la_autenticacion_esta_activa(monkeypatch, caplog):
    import logging

    from app import main
    from app.config import settings

    monkeypatch.setattr(settings, "enable_auth", True)
    with caplog.at_level(logging.WARNING):
        main.avisar_si_no_hay_autenticacion()

    assert caplog.text == ""


# ---------- Binding del puerto ----------

def test_el_compose_publica_en_loopback_por_defecto():
    """El default no puede exponer el patrimonio a toda la LAN sin decidirlo.

    Se comprueba sobre el fichero porque es donde vive la decisión: la app no
    ve esta variable, solo docker compose."""
    from pathlib import Path

    compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text(encoding="utf-8")

    assert "${FINANCE_BIND:-127.0.0.1}:${FINANCE_PORT:-8001}:8000" in compose
