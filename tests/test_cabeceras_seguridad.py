"""[FT-A8] Cabeceras de seguridad HTTP en todas las respuestas.

La app no emitía ninguna. Consecuencias concretas aquí:

- Sin CSP, cualquier XSS futuro tiene ejecución total. Ya hay bloques <script>
  en línea en tres plantillas.
- Sin frame-ancestors, la app se puede embeber desde otro origen. Con las
  credenciales Basic cacheadas por el navegador eso permite clickjacking sobre
  "Eliminar activo" o "Eliminar operación": el token CSRF protege el POST, pero
  no protege de que el clic lo dé el propio usuario engañado sobre la página
  real embebida.
- Sin Referrer-Policy, cada enlace externo filtra la URL completa de la app.
"""
import pytest

PAGINAS = ["/", "/activos", "/transacciones", "/analisis", "/salud"]


@pytest.mark.parametrize("ruta", PAGINAS)
def test_las_paginas_llevan_cabeceras_de_seguridad(client, ruta):
    cabeceras = client.get(ruta).headers

    assert cabeceras["X-Content-Type-Options"] == "nosniff"
    assert cabeceras["Referrer-Policy"] == "same-origin"
    assert cabeceras["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in cabeceras


def test_la_csp_prohibe_iframes(client):
    assert "frame-ancestors 'none'" in client.get("/").headers["Content-Security-Policy"]


def test_la_csp_limita_los_origenes_externos(client):
    """La mitad del valor de la CSP con 'unsafe-inline' sigue estando aquí:
    un script de otro origen no carga y no hay por dónde exfiltrar."""
    csp = client.get("/").headers["Content-Security-Policy"]

    assert "default-src 'self'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


def test_los_estaticos_tambien_las_llevan(client):
    """El middleware va a nivel de app: cubre también lo que sirve StaticFiles."""
    respuesta = client.get("/static/css/style.css")

    assert respuesta.status_code == 200
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"


def test_las_paginas_de_error_tambien_las_llevan(client):
    """Un 404 sale por el manejador de excepciones, no por el flujo normal."""
    respuesta = client.get("/esta-ruta-no-existe")

    assert respuesta.status_code == 404
    assert respuesta.headers["X-Content-Type-Options"] == "nosniff"
