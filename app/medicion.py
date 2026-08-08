"""Conteo de sentencias SQL por petición.

Los hallazgos de rendimiento de la auditoría estaban razonados sobre lectura de
código, no sobre medición. Sin un número, optimizar es una cuestión de opinión y
no hay forma de demostrar que un cambio mejoró algo — ni de que no lo empeoró
seis meses después.

Se activa con `DEBUG_SQL=1` y publica el total en la cabecera
`X-Consultas-SQL`; apagado no cuesta nada, porque ni siquiera se registra el
listener.

**El contador es global, no por hilo.** El primer intento usó un
`threading.local` y contaba cero: FastAPI ejecuta los endpoints síncronos en un
hilo del pool, así que el SQL ocurre en un hilo distinto de aquel donde se abrió
la medición. Con contadores por hilo el número nunca llegaba al sitio correcto.

La consecuencia asumida es que dos peticiones simultáneas mezclan sus cuentas.
Para lo que esto es —diagnóstico puntual y tests secuenciales— da igual; para
mirar producción bajo carga, no serviría. Por eso vive detrás de una variable de
entorno y no está encendido por defecto.
"""
import contextlib
import threading

from sqlalchemy import event

_lock = threading.Lock()
_estado = {"activo": False, "consultas": 0}


def _sumar(conn, cursor, statement, parameters, context, executemany):
    with _lock:
        if _estado["activo"]:
            _estado["consultas"] += 1


def instrumentar(engine) -> None:
    """Empieza a contar en este motor. Idempotente."""
    if not event.contains(engine, "before_cursor_execute", _sumar):
        event.listen(engine, "before_cursor_execute", _sumar)


@contextlib.contextmanager
def contar_consultas(engine):
    """Cuenta las sentencias emitidas dentro del bloque.

        with contar_consultas(engine) as consultas:
            client.get("/")
        assert consultas.total <= 40

    El valor solo es válido al salir del bloque: dentro va creciendo.
    Anidar bloques funciona; el interior no descuenta del exterior."""
    instrumentar(engine)
    with _lock:
        previo_activo, previo_total = _estado["activo"], _estado["consultas"]
        _estado["activo"] = True
        _estado["consultas"] = 0

    class _Contador:
        total = 0

    contador = _Contador()
    try:
        yield contador
    finally:
        with _lock:
            contador.total = _estado["consultas"]
            # El bloque exterior sigue contando lo suyo más lo del interior:
            # anidar no puede hacer que el de fuera pierda sentencias.
            _estado["consultas"] = previo_total + contador.total
            _estado["activo"] = previo_activo
