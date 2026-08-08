"""[FT-A4] Ningún endpoint de subida acotaba el tamaño del fichero.

`await archivo.read()` sin argumento carga el fichero entero en memoria y el
`.decode()` posterior crea una segunda copia. Arrastrar por error un fichero de
1 GB consume 2-3 GB de RSS y el kernel mata el proceso; en un NAS con 4 GB eso
tumba también lo que haya al lado, sin ningún mensaje útil.

Peor en el PDF: los bytes van a `fitz.open()`, que con un PDF malformado o con
bombas de descompresión consume mucha más memoria que el fichero original. Por
eso su límite es más bajo.
"""
import io

from app.uploads import MAX_CSV_BYTES, MAX_PDF_BYTES

CSV_CABECERA = b"fecha,importe,descripcion\n"
FILA = b"2026-01-01,-12.34,compra\n"


_DESCRIPCION_LARGA = b"x" * 60_000


def _csv_de(bytes_totales: int) -> bytes:
    """Un CSV del tamaño pedido, con pocas filas y descripciones largas.

    Dos motivos para no rellenarlo con filas normales ni con una sola gigante:

    - Con filas normales, 20 MB son ~800.000 movimientos y, sin el límite, el
      endpoint se pone a crearlos: el test tardaba minutos. Que pase eso es, de
      hecho, la mejor demostración del problema que se está arreglando.
    - Con una sola fila enorme, `csv` corta con "field larger than field limit"
      (131072) antes de llegar a nada interesante.
    """
    fila = b"2026-01-01,-12.34," + _DESCRIPCION_LARGA + b"\n"
    cuantas = max(1, (bytes_totales - len(CSV_CABECERA)) // len(fila) + 1)
    return CSV_CABECERA + fila * cuantas


def _csv_normal(filas: int) -> bytes:
    return CSV_CABECERA + FILA * filas


def _subir_csv_banco(client, contenido: bytes):
    return client.post(
        "/transacciones/importar-csv",
        data={
            "_csrf": client.csrf(),
            "columna_fecha": "fecha",
            "columna_importe": "importe",
            "columna_descripcion": "descripcion",
            "formato_fecha": "%Y-%m-%d",
        },
        files={"archivo": ("movimientos.csv", io.BytesIO(contenido), "text/csv")},
        follow_redirects=False,
    )


def _subir_operaciones(client, nombre: str, contenido: bytes):
    return client.post(
        "/operaciones/importar/preview",
        data={"_csrf": client.csrf(), "formato": "generic"},
        files={"archivo": (nombre, io.BytesIO(contenido), "application/octet-stream")},
        follow_redirects=False,
    )


def test_csv_demasiado_grande_devuelve_413(client):
    assert _subir_csv_banco(client, _csv_de(MAX_CSV_BYTES + 1024)).status_code == 413


def test_csv_dentro_del_limite_se_importa(client):
    """Un CSV normal tiene que seguir procesándose con normalidad."""
    respuesta = _subir_csv_banco(client, _csv_normal(50))

    assert respuesta.status_code != 413
    from app.models import Transaction
    assert client.db.query(Transaction).count() == 50


def test_pdf_demasiado_grande_devuelve_413(client):
    grande = b"%PDF-1.4\n" + b"0" * (MAX_PDF_BYTES + 1024)

    assert _subir_operaciones(client, "extracto.pdf", grande).status_code == 413


def test_el_pdf_tiene_un_limite_mas_bajo_que_el_csv(client):
    """Un fichero entre los dos límites: pasa como CSV y se rechaza como PDF.

    Es lo que justifica que sean dos números y no uno: fitz.open() puede
    consumir mucha más memoria que el propio fichero."""
    intermedio = MAX_PDF_BYTES + 2 * 1024 * 1024
    assert intermedio < MAX_CSV_BYTES

    como_pdf = _subir_operaciones(client, "extracto.pdf", b"%PDF-1.4\n" + b"0" * intermedio)
    como_csv = _subir_operaciones(client, "operaciones.csv", _csv_de(intermedio))

    assert como_pdf.status_code == 413
    assert como_csv.status_code != 413


def test_csv_de_operaciones_demasiado_grande_devuelve_413(client):
    respuesta = _subir_operaciones(client, "operaciones.csv", _csv_de(MAX_CSV_BYTES + 1024))

    assert respuesta.status_code == 413


def test_los_formularios_declaran_que_ficheros_aceptan(client):
    """Del lado del cliente no protege de nada, pero evita el error más común:
    arrastrar el fichero equivocado."""
    for ruta, esperado in (("/operaciones/importar", ".pdf"), ("/transacciones", ".csv")):
        html = client.get(ruta).text
        entradas = [linea for linea in html.splitlines() if 'type="file"' in linea]

        assert entradas, "no hay input de fichero en %s" % ruta
        assert all("accept=" in e for e in entradas), "falta accept en %s" % ruta
        assert any(esperado in e for e in entradas), "%s no acepta %s" % (ruta, esperado)
