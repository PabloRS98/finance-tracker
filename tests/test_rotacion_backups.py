"""[FT-A6] La rotación de backups con BACKUP_KEEP=0 hacía lo contrario de lo que dice.

`existing[:-0]` es `existing[:0]`, o sea la lista vacía: con `BACKUP_KEEP=0` no
se borraba ningún backup. Quien lo configura esperando "no conservar backups"
obtenía "conservarlos todos", y el disco se llena a razón de una copia completa
de la base al día, para siempre.

Cuando SQLite se queda sin espacio en un volumen con WAL, las escrituras fallan
y la app entra en el estado peor de todos: las lecturas funcionan y las
escrituras no.
"""
import os

import pytest

from app.config import settings
from app.services import scheduler


@pytest.fixture
def carpeta_de_backups(tmp_path, monkeypatch):
    """Una BD real y su directorio `backups/` al lado, como en el volumen."""
    datos = tmp_path / "datos"
    datos.mkdir()
    db = datos / "finance.db"

    import sqlite3

    con = sqlite3.connect(db)
    con.execute("CREATE TABLE prueba (id INTEGER)")
    con.commit()
    con.close()

    monkeypatch.setattr(settings, "db_path", str(db))
    backups = datos / "backups"
    backups.mkdir()
    return backups


def _copias(carpeta) -> list[str]:
    return sorted(f for f in os.listdir(carpeta) if f.startswith("finance-"))


def _rellenar(carpeta, cuantas: int) -> None:
    """Backups viejos, con fecha anterior a hoy para que el nuevo ordene último."""
    for i in range(cuantas):
        (carpeta / ("finance-2020%02d01.db" % (i + 1))).write_text("viejo", encoding="utf-8")


def test_backup_keep_cero_conserva_solo_el_ultimo(carpeta_de_backups, monkeypatch):
    """Con 0 se conserva el que se acaba de crear: borrarlo también dejaría la
    llamada sin resultado, y `backup_database` devuelve su ruta."""
    monkeypatch.setattr(settings, "backup_keep", 0)
    _rellenar(carpeta_de_backups, 5)

    ruta = scheduler.backup_database()

    assert len(_copias(carpeta_de_backups)) == 1
    assert os.path.basename(ruta) in _copias(carpeta_de_backups)


def test_backup_keep_tres_conserva_tres(carpeta_de_backups, monkeypatch):
    monkeypatch.setattr(settings, "backup_keep", 3)
    _rellenar(carpeta_de_backups, 5)

    scheduler.backup_database()

    assert len(_copias(carpeta_de_backups)) == 3


def test_backup_keep_alto_no_borra_nada(carpeta_de_backups, monkeypatch):
    monkeypatch.setattr(settings, "backup_keep", 14)
    _rellenar(carpeta_de_backups, 5)

    scheduler.backup_database()

    assert len(_copias(carpeta_de_backups)) == 6


def test_se_conservan_los_mas_recientes(carpeta_de_backups, monkeypatch):
    """La rotación ordena por nombre, y el nombre lleva la fecha: lo que se
    borra tiene que ser lo viejo."""
    monkeypatch.setattr(settings, "backup_keep", 2)
    _rellenar(carpeta_de_backups, 4)

    scheduler.backup_database()
    quedan = _copias(carpeta_de_backups)

    assert len(quedan) == 2
    assert "finance-20200101.db" not in quedan


def test_una_ruta_explicita_no_rota_nada(tmp_path, carpeta_de_backups, monkeypatch):
    """Fuera del directorio estándar no se toca nada: la descarga manual de un
    backup no puede llevarse por delante las copias diarias."""
    monkeypatch.setattr(settings, "backup_keep", 1)
    _rellenar(carpeta_de_backups, 3)

    scheduler.backup_database(str(tmp_path / "suelto.db"))

    assert len(_copias(carpeta_de_backups)) == 3
