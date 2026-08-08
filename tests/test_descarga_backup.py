"""[FT-A7] La descarga de backup escribía siempre en la misma ruta fija.

`backup_database("/tmp/finance-backup.db")` tenía tres problemas:

1. Ruta POSIX fija. Fuera de Linux `/tmp` no existe, así que en desarrollo
   escribía un fichero suelto en la raíz del disco o fallaba.
2. Nombre fijo. Dos descargas a la vez -o una mientras la anterior sigue
   transmitiéndose- escriben sobre el mismo fichero, y `sqlite3.backup()` sobre
   un fichero que se está leyendo produce una descarga corrupta sin ningún
   error. Un backup corrupto sin aviso es el peor tipo de fallo en un backup.
3. Nunca se borraba. Quedaba una copia completa del patrimonio, las operaciones
   y los gastos en una ruta legible por cualquier proceso del contenedor.

El ROADMAP asumía el punto 2 como deuda ("es mono-usuario, en la práctica no
ocurre"). Los puntos 1 y 3 no se habían pesado, y el 3 es una fuga de datos.
"""
import os
import re

from app.routers import dashboard


def _rutas_generadas(client, monkeypatch, veces: int) -> list[str]:
    """Rutas por las que pasa `backup_database` en llamadas sucesivas."""
    rutas: list[str] = []
    real = dashboard.backup_database

    def espia(destino=None):
        rutas.append(destino)
        return real(destino)

    monkeypatch.setattr(dashboard, "backup_database", espia)
    for _ in range(veces):
        assert client.get("/patrimonio/backup").status_code == 200
    return rutas


def test_descarga_de_backup_usa_fichero_temporal_unico(client, monkeypatch):
    rutas = _rutas_generadas(client, monkeypatch, 2)

    assert rutas[0] != rutas[1], "dos descargas seguidas se pisaban el fichero"


def test_el_temporal_se_borra_tras_la_descarga(client, monkeypatch):
    rutas = _rutas_generadas(client, monkeypatch, 1)

    assert not os.path.exists(rutas[0]), "queda una copia entera de los datos en disco"


def test_la_ruta_temporal_no_es_posix_fija(client, monkeypatch):
    """`/tmp` no existe en todas partes; el temporal lo elige el sistema."""
    rutas = _rutas_generadas(client, monkeypatch, 1)

    assert rutas[0] != "/tmp/finance-backup.db"
    assert "finance-backup-" in os.path.basename(rutas[0])


def test_la_descarga_llega_entera_y_con_nombre_fechado(client):
    respuesta = client.get("/patrimonio/backup")

    assert respuesta.status_code == 200
    assert len(respuesta.content) > 0
    # SQLite marca sus ficheros con esta cabecera: si el backup saliera a medias
    # o vacío, esto no estaría.
    assert respuesta.content.startswith(b"SQLite format 3")
    assert re.search(r"finance-backup-\d{4}-\d{2}-\d{2}\.db",
                     respuesta.headers["content-disposition"])


def test_la_descarga_no_rota_los_backups_diarios(client, monkeypatch, tmp_path):
    """`backup_database` solo rota dentro del directorio llamado `backups`. Con
    un temporal del sistema sigue saltándose la rotación, que es lo correcto:
    descargar una copia no puede borrar las diarias."""
    rutas = _rutas_generadas(client, monkeypatch, 1)

    assert os.path.basename(os.path.dirname(rutas[0])) != "backups"
