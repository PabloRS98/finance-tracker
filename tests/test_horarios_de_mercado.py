"""Los avisos de apertura y cierre tienen que caer a la hora real de su plaza.

Las horas estaban escritas en UTC pero el scheduler corre en `settings.timezone`,
así que con TIMEZONE=Europe/Madrid los cuatro saltaban dos horas antes de lo
debido: el "cierre de Europa" llegaba a las 15:30, con la bolsa aún abierta.

Se comprueba con el scheduler real, sin arrancarlo, mirando a qué hora local de
cada plaza queda programada la siguiente ejecución. Así el test no depende de la
zona horaria de la máquina que lo ejecuta.
"""
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import scheduler as scheduler_svc

# Hora local que debe tener cada aviso en la zona de su mercado
HORARIO_ESPERADO = {
    "market_open_eu": ("Europe/Madrid", 9, 0),
    "market_close_eu": ("Europe/Madrid", 17, 30),
    "market_open_us": ("America/New_York", 9, 30),
    "market_close_us": ("America/New_York", 16, 0),
}


@pytest.fixture
def scheduler(monkeypatch):
    """Scheduler configurado pero sin arrancar: no dispara ningún job ni toca
    la red. Se fija una zona distinta a las de los mercados a propósito, porque
    el fallo era justo que las horas se interpretaban en la del servidor."""
    monkeypatch.setattr(scheduler_svc.settings, "timezone", "Europe/Madrid")

    creado = {}

    class _SchedulerSinArrancar(scheduler_svc.BackgroundScheduler):
        def start(self, *args, **kwargs):
            creado["jobs"] = {job.id: job for job in self.get_jobs()}

    monkeypatch.setattr(scheduler_svc, "BackgroundScheduler", _SchedulerSinArrancar)
    scheduler_svc.start_scheduler()
    return creado["jobs"]


@pytest.mark.parametrize("job_id", sorted(HORARIO_ESPERADO))
def test_cada_aviso_cae_a_la_hora_local_de_su_plaza(scheduler, job_id):
    zona, hora, minuto = HORARIO_ESPERADO[job_id]
    disparo = scheduler[job_id].trigger

    # Siguiente ejecución a partir de un lunes cualquiera, en la zona del mercado
    lunes = datetime(2026, 3, 2, 0, 0, tzinfo=ZoneInfo(zona))
    siguiente = disparo.get_next_fire_time(None, lunes).astimezone(ZoneInfo(zona))

    assert (siguiente.hour, siguiente.minute) == (hora, minuto)


@pytest.mark.parametrize("job_id", sorted(HORARIO_ESPERADO))
def test_el_horario_de_verano_no_desplaza_los_avisos(scheduler, job_id):
    """En invierno y en verano tiene que ser la misma hora local. EE. UU. y
    Europa no cambian el mismo fin de semana, así que un desfase fijo en UTC
    falla durante un par de semanas al año."""
    zona, hora, minuto = HORARIO_ESPERADO[job_id]
    disparo = scheduler[job_id].trigger

    for arranque in (datetime(2026, 1, 5, tzinfo=ZoneInfo(zona)),   # invierno
                     datetime(2026, 7, 6, tzinfo=ZoneInfo(zona))):  # verano
        siguiente = disparo.get_next_fire_time(None, arranque).astimezone(ZoneInfo(zona))
        assert (siguiente.hour, siguiente.minute) == (hora, minuto)


@pytest.mark.parametrize("job_id", sorted(HORARIO_ESPERADO))
def test_cada_aviso_lleva_su_propio_titulo(scheduler, job_id):
    """Sin título los cinco resúmenes del día llegaban idénticos."""
    titulo = scheduler[job_id].args[0]

    assert titulo
    otros = {j.args[0] for i, j in scheduler.items() if i in HORARIO_ESPERADO and i != job_id}
    assert titulo not in otros


def test_el_resumen_diario_no_lleva_titulo(scheduler):
    """El de la hora configurada por el usuario sigue siendo el "Resumen" a secas."""
    assert scheduler["daily_telegram_summary"].args == ()


def test_el_titulo_encabeza_el_mensaje(db):
    """Con el resumen reformateado en cajas el título va en la cabecera y en
    mayúsculas, ya no como <b>…</b>. Lo que importa es que siga distinguiendo
    de cuál de los cinco avisos del día se trata."""
    from app.services.telegram_bot import DIAS_SEMANA, build_summary

    assert "CIERRE DE EE. UU." in build_summary(db, "Cierre de EE. UU.")
    # Sin título (el /resumen a mano) encabeza el día de la semana
    assert DIAS_SEMANA[date.today().weekday()].upper() in build_summary(db)
