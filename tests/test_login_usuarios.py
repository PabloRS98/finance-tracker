"""Entrar, salir y contraseñas opcionales.

Sustituye al HTTP Basic porque Basic no sabe hacer lo que aquí hace falta: una
contraseña **por persona** y **opcional**. El navegador manda unas credenciales
o ninguna, y no hay forma de decirle "para esta no pidas nada y para esta sí".
"""
import pytest

from app import sesion
from app.models import Usuario

# ---------- Contraseñas ----------

def test_una_contraseña_correcta_pasa():
    guardado = sesion.hashear("mi contraseña")

    assert sesion.comprobar("mi contraseña", guardado) is True


def test_una_contraseña_incorrecta_no_pasa():
    assert sesion.comprobar("otra", sesion.hashear("mi contraseña")) is False


def test_sin_contraseña_guardada_entra_cualquiera():
    """Es lo que significa no ponerla, y es una decisión deliberada."""
    assert sesion.comprobar("", None) is True
    assert sesion.comprobar("lo que sea", None) is True


def test_la_contraseña_no_se_guarda_en_claro():
    guardado = sesion.hashear("secreto")

    assert "secreto" not in guardado


def test_dos_veces_la_misma_contraseña_dan_hashes_distintos():
    """Sal por usuario: sin ella, dos personas con la misma contraseña tendrían
    el mismo hash y se vería a simple vista."""
    assert sesion.hashear("igual") != sesion.hashear("igual")


def test_un_hash_ilegible_se_rechaza_en_vez_de_reventar():
    assert sesion.comprobar("lo que sea", "esto-no-tiene-formato") is False


# ---------- Cookie ----------

def test_la_cookie_manipulada_no_vale(client):
    """La firma es lo único que impide entrar como otro cambiando un número."""
    from fastapi import Request

    peticion = Request({"type": "http", "headers": [
        (b"cookie", b"sesion=99.firmafalsa"),
    ]})

    assert sesion.leer(peticion) is None


def test_la_cookie_legitima_se_lee(client):
    from fastapi import Request, Response

    respuesta = Response()
    sesion.abrir(respuesta, 42)
    valor = respuesta.headers["set-cookie"].split(";")[0].split("=", 1)[1]
    peticion = Request({"type": "http", "headers": [
        (b"cookie", ("sesion=%s" % valor).encode()),
    ]})

    assert sesion.leer(peticion) == 42


# ---------- Rutas ----------

def test_sin_sesion_te_manda_a_elegir(client):
    client.cookies.delete(sesion.COOKIE)

    respuesta = client.get("/", follow_redirects=False)

    assert respuesta.status_code == 303
    assert respuesta.headers["location"] == "/entrar"


def test_la_pantalla_de_entrada_no_pide_sesion(client):
    client.cookies.delete(sesion.COOKIE)

    assert client.get("/entrar").status_code == 200


def test_el_healthcheck_sigue_abierto(client):
    """Docker no manda cookie, ni debe."""
    client.cookies.delete(sesion.COOKIE)

    assert client.get("/salud").status_code != 303


def test_entrar_sin_contraseña_abre_sesion(client):
    client.cookies.delete(sesion.COOKIE)
    usuario = client.db.query(Usuario).one()

    respuesta = client.post_form("/entrar", data={"usuario_id": usuario.id},
                                 follow_redirects=False)

    assert respuesta.status_code == 303
    assert client.get("/", follow_redirects=False).status_code == 200


def test_entrar_con_contraseña_incorrecta_no_abre_sesion(client):
    usuario = client.db.query(Usuario).one()
    usuario.password_hash = sesion.hashear("la buena")
    client.db.commit()
    client.cookies.delete(sesion.COOKIE)

    client.post_form("/entrar", data={"usuario_id": usuario.id, "password": "la mala"},
                     follow_redirects=False)

    assert client.get("/", follow_redirects=False).status_code == 303


def test_salir_manda_borrar_la_cookie(client):
    """Se comprueba la cabecera y no el estado del cliente: el jar del
    TestClient no suelta una cookie que se le puso a mano, y lo que el servidor
    controla —lo único que puede garantizar— es lo que manda en la respuesta.
    Que sin cookie se redirige lo cubre `test_sin_sesion_te_manda_a_elegir`."""
    respuesta = client.post_form("/salir", follow_redirects=False)

    borrado = [
        valor for clave, valor in respuesta.headers.multi_items()
        if clave.lower() == "set-cookie" and valor.startswith("%s=" % sesion.COOKIE)
    ]

    assert borrado, "no se emitió ninguna cabecera para la cookie de sesión"
    assert "Max-Age=0" in borrado[0]
    assert "1970" in borrado[0], "la caducidad tiene que estar en el pasado"


def test_crear_una_cuenta_nueva(client):
    client.post_form("/usuarios", data={"nombre": "Beto", "password": ""},
                     follow_redirects=False)

    assert client.db.query(Usuario).filter_by(nombre="Beto").count() == 1


def test_no_se_repiten_los_nombres(client):
    client.post_form("/usuarios", data={"nombre": "Beto"}, follow_redirects=False)
    client.post_form("/usuarios", data={"nombre": "Beto"}, follow_redirects=False)

    assert client.db.query(Usuario).filter_by(nombre="Beto").count() == 1


@pytest.mark.parametrize("nombre", ["", "   "])
def test_un_nombre_vacio_se_rechaza(client, nombre):
    antes = client.db.query(Usuario).count()

    client.post_form("/usuarios", data={"nombre": nombre}, follow_redirects=False)

    assert client.db.query(Usuario).count() == antes


def test_una_contraseña_demasiado_corta_se_rechaza(client):
    client.post_form("/usuarios", data={"nombre": "Corta", "password": "ab"},
                     follow_redirects=False)

    assert client.db.query(Usuario).filter_by(nombre="Corta").count() == 0


def test_se_puede_poner_y_quitar_la_contraseña(client):
    usuario = client.db.query(Usuario).one()

    client.post_form("/usuarios/password", data={"actual": "", "nueva": "nueva clave"},
                     follow_redirects=False)
    client.db.expire_all()
    assert client.db.query(Usuario).one().password_hash is not None

    client.post_form("/usuarios/password", data={"actual": "nueva clave", "nueva": ""},
                     follow_redirects=False)
    client.db.expire_all()
    assert client.db.query(Usuario).one().password_hash is None
    assert usuario.id  # el usuario sigue siendo el mismo


def test_no_se_cambia_la_contraseña_sin_saber_la_actual(client):
    usuario = client.db.query(Usuario).one()
    usuario.password_hash = sesion.hashear("la buena")
    client.db.commit()
    hash_previo = usuario.password_hash

    client.post_form("/usuarios/password", data={"actual": "la mala", "nueva": "otra"},
                     follow_redirects=False)

    client.db.expire_all()
    assert client.db.query(Usuario).one().password_hash == hash_previo
