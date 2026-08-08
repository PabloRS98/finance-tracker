"""Lectura acotada de ficheros subidos.

`await archivo.read()` sin argumento carga el fichero ENTERO en memoria, y el
`.decode()` posterior crea una segunda copia. Arrastrar por error un fichero de
1 GB —un backup, un vídeo, cualquier cosa— consume 2-3 GB de RSS y el kernel
mata el proceso. En un NAS doméstico con 4 GB eso tumba también lo que haya al
lado, y sin ningún mensaje de error útil.

Los dos límites son distintos a propósito. Un extracto de banco en CSV puede ser
grande si lleva años de movimientos; un extracto de bróker en PDF nunca pasa de
unos pocos MB, y ahí conviene ser más estricto porque los bytes van a
`fitz.open()`, que con un PDF malformado o con bombas de descompresión consume
mucha más memoria que el fichero original.
"""
from fastapi import HTTPException, UploadFile

MAX_CSV_BYTES = 20 * 1024 * 1024   # 20 MB
MAX_PDF_BYTES = 10 * 1024 * 1024   # 10 MB

_TROZO = 1024 * 1024


async def leer_limitado(archivo: UploadFile, maximo: int = MAX_CSV_BYTES) -> bytes:
    """Bytes del fichero, o 413 en cuanto se pasa del tope.

    Se lee por trozos y se corta al superar el límite: así el fichero grande
    nunca llega a estar entero en memoria."""
    trozos: list[bytes] = []
    total = 0
    while True:
        trozo = await archivo.read(_TROZO)
        if not trozo:
            break
        total += len(trozo)
        if total > maximo:
            raise HTTPException(
                status_code=413,
                detail="El fichero supera el límite de %d MB" % (maximo // (1024 * 1024)),
            )
        trozos.append(trozo)
    return b"".join(trozos)


async def leer_texto_limitado(archivo: UploadFile, maximo: int = MAX_CSV_BYTES) -> str:
    """Ídem, ya decodificado. `utf-8-sig` porque Excel escribe BOM."""
    return (await leer_limitado(archivo, maximo)).decode("utf-8-sig", errors="ignore")
