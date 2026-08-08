"""[FT-C3] La app tiene que arrancar desde cualquier directorio de trabajo.

Las plantillas, los estáticos y el `.env` se resolvían con rutas relativas al
cwd del proceso. Funcionaba de casualidad, porque el `WORKDIR /app` del
Dockerfile lo salvaba; cualquier otra forma de arrancar —uvicorn desde otra
carpeta, un systemd unit sin WorkingDirectory, un `docker run -w /`— reventaba.

El caso del `.env` es el peor de los tres porque **no revienta**: si no se lee,
la app se levanta con todos los valores por defecto, es decir sin autenticación.
Combinado con [FT-C1], un cambio de directorio convertía la app en pública sin
ningún error visible.
"""
import importlib
import os

from starlette.staticfiles import StaticFiles

from app import config, main, templating


def test_env_file_es_absoluto():
    assert config.ENV_FILE.is_absolute()


def test_el_env_file_apunta_a_la_raiz_del_repo():
    """No basta con que sea absoluto: tiene que apuntar donde está el fichero."""
    assert config.ENV_FILE.name == ".env"
    assert (config.ENV_FILE.parent / "app" / "config.py").is_file()


def test_arranque_fuera_de_la_raiz(tmp_path):
    """Con el proceso arrancado desde otro sitio, las plantillas siguen ahí."""
    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        recargado = importlib.reload(templating)
        plantilla = recargado.templates.env.get_template("_icons.html")

        assert plantilla.render() is not None
    finally:
        # Se deja el módulo como estaba: los routers guardan su propia
        # referencia a `templates`, pero un test posterior podría usar la del
        # módulo y encontrarse la construida desde el directorio temporal.
        os.chdir(original)
        importlib.reload(templating)


def test_los_estaticos_se_montan_fuera_de_la_raiz(tmp_path):
    """StaticFiles sí comprueba que el directorio existe, y lo hace al montar.

    Con la ruta relativa esto lanzaba RuntimeError en el import de app.main,
    o sea que la app ni siquiera llegaba a arrancar."""
    assert main.STATIC_DIR.is_absolute()

    original = os.getcwd()
    os.chdir(tmp_path)
    try:
        StaticFiles(directory=str(main.STATIC_DIR))
    finally:
        os.chdir(original)


def test_las_plantillas_se_resuelven_desde_el_propio_modulo():
    assert templating.TEMPLATES_DIR.is_absolute()
    assert (templating.TEMPLATES_DIR / "base.html").is_file()
