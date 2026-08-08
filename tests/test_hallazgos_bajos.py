"""Los hallazgos BAJOS de la auditoría: coherencia y limpieza.

No son bugs con síntoma, salvo dos: el temporal huérfano de la transcripción
(FT-B11) y el ancho de caja distinto en el mismo mensaje (FT-B6). El resto son
duplicados y nombres que engañan, que es de donde salen los bugs de dentro de
seis meses.
"""
from pathlib import Path

import pytest

from app.services.history import DIAS_DE_BACKFILL
from app.services.recurring import sumar_meses

RAIZ = Path(__file__).resolve().parent.parent


# ---------- FT-B3: la cuenta de meses, una sola vez ----------

@pytest.mark.parametrize(("anio", "mes", "n", "esperado"), [
    (2026, 8, 0, (2026, 8)),
    (2026, 8, -1, (2026, 7)),
    (2026, 1, -1, (2025, 12)),      # cruzar el año hacia atrás
    (2026, 1, -13, (2024, 12)),     # más de un año
    (2026, 12, 1, (2027, 1)),       # y hacia delante
])
def test_sumar_meses_cruza_el_ano_en_los_dos_sentidos(anio, mes, n, esperado):
    assert sumar_meses(anio, mes, n) == esperado


def test_el_dashboard_no_repite_la_cuenta_de_meses():
    """Tenerla dos veces es tenerla dos veces mal el día que alguien arregle una."""
    codigo = (RAIZ / "app" / "routers" / "dashboard.py").read_text(encoding="utf-8")

    assert "while m <= 0" not in codigo
    assert "sumar_meses" in codigo


# ---------- FT-B4: dos claves para dos cosas distintas ----------

def test_los_dos_porcentajes_del_presupuesto_se_llaman_por_lo_que_son(client):
    """`porcentaje` es lo gastado de verdad y puede pasar del 100 %;
    `porcentaje_barra` es lo que mide la barra y no puede pasarse de su ancho.
    Antes se llamaban `porcentaje` y `porcentaje_real`, y había que abrir la
    plantilla para saber cuál era cuál."""
    plantilla = (RAIZ / "app" / "templates" / "dashboard.html").read_text(encoding="utf-8")

    assert "porcentaje_real" not in plantilla
    assert "p.porcentaje_barra" in plantilla


# ---------- FT-B5 y FT-B6: constantes repetidas ----------

def test_el_umbral_de_cantidad_no_esta_repetido():
    """El literal 1e-9 estaba escrito dos veces en telegram_bot, con su propio
    comentario cada vez, mientras portfolio ya tenía la constante."""
    codigo = (RAIZ / "app" / "services" / "telegram_bot.py").read_text(encoding="utf-8")

    assert "1e-9" not in codigo
    assert "CANTIDAD_MINIMA" in codigo


def test_las_cajas_del_resumen_tienen_un_solo_ancho():
    """Eran 48 y 42, y esas cajas se ven juntas en el mismo mensaje."""
    codigo = (RAIZ / "app" / "services" / "telegram_bot.py").read_text(encoding="utf-8")

    assert codigo.count("BOX_W = ") == 1


# ---------- FT-B8: el backfill, con nombre ----------

def test_el_backfill_son_cinco_anos():
    assert DIAS_DE_BACKFILL == 5 * 365


# ---------- FT-B11: el temporal de la transcripción no queda huérfano ----------

def test_la_transcripcion_borra_el_temporal_aunque_falle(monkeypatch, tmp_path):
    """Si la escritura falla —disco lleno, audio enorme— el fichero se quedaba
    en el temporal del sistema para siempre: el `finally` que lo borraba se
    abría después de escribir."""
    import os
    import tempfile

    from app.services import stt

    monkeypatch.setattr(stt, "_get_model", lambda: object())
    creados = []
    mkstemp_real = tempfile.mkstemp

    def _espia(*args, **kwargs):
        fd, ruta = mkstemp_real(*args, **kwargs)
        creados.append(ruta)
        return fd, ruta

    monkeypatch.setattr(stt.tempfile, "mkstemp", _espia)

    class _AudioQueRevienta(bytes):
        def __len__(self):
            raise OSError("no queda espacio en el dispositivo")

    assert stt.transcribe(_AudioQueRevienta(b"x")) is None
    assert creados, "no se llegó a crear el temporal"
    assert not os.path.exists(creados[0]), "el temporal quedó huérfano"


# ---------- FT-B13: configuración de pytest ----------

def test_pytest_va_configurado_en_pyproject():
    """Un solo sitio para la configuración, y con `--strict-markers`: sin eso,
    una marca mal escrita se ignora en silencio."""
    pyproject = (RAIZ / "pyproject.toml").read_text(encoding="utf-8")

    assert "[tool.pytest.ini_options]" in pyproject
    assert "--strict-markers" in pyproject
    assert not (RAIZ / "pytest.ini").exists(), "la configuración está en dos sitios"
