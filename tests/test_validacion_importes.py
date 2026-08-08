"""[FT-M5] [FT-M8] Los importes del libro entraban sin validar y como float.

**FT-M5.** Cinco endpoints recibían el importe sin comprobar signo ni magnitud.
Un GASTO de -50 € **suma** al balance del mes, descuadra los presupuestos
(`porcentaje` sale negativo y `min(100, porcentaje)` lo deja tal cual) y
contamina el CSV exportado. Contrasta con el rigor del resto: las operaciones
validan cantidad y precio, las alertas validan el valor, los pesos objetivo
validan el rango.

**FT-M8.** `models.py` documenta con detalle por qué los importes del libro van
en `Numeric` y no en `float`: *"con float, diez gastos de 0,10 € suman
0.9999999999999999 y el error se arrastra a los totales del mes"*. Pero la
puerta de entrada era `float`, así que el ruido binario se colaba igual. El
camino de la voz y de Telegram sí era exacto, porque pasa por `to_base`, que usa
`Decimal(str(x))`. Dos rutas de entrada al mismo campo con precisión distinta.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models import RecurringTransaction, Transaction


def _alta(client, **campos):
    datos = {"date": "2026-01-15", "type": "gasto", "amount": "10", "description": "prueba"}
    datos.update(campos)
    return client.post_form("/transacciones", data=datos, follow_redirects=False)


def _recurrente(client, **campos):
    datos = {
        "name": "Cuota", "type": "gasto", "amount": "10", "currency": "EUR",
        "interval_months": "1", "day_of_month": "1", "start_date": "2026-01-01",
    }
    datos.update(campos)
    return client.post_form("/recurrentes", data=datos, follow_redirects=False)


# ---------- FT-M5: validación ----------

@pytest.mark.parametrize("importe", ["-50", "0", "-0.01"])
def test_no_se_guarda_una_transaccion_con_importe_no_positivo(client, importe):
    _alta(client, amount=importe)

    assert client.db.query(Transaction).count() == 0


def test_no_se_guarda_una_transaccion_con_importe_desmesurado(client):
    """Money es Numeric(12, 2): por encima de eso SQLite guarda el valor sin
    quejarse y el dato vuelve mal al leerlo."""
    _alta(client, amount="1000000000.01")

    assert client.db.query(Transaction).count() == 0


def test_un_importe_normal_si_se_guarda(client):
    _alta(client, amount="42.50")

    assert client.db.query(Transaction).one().amount == Decimal("42.50")


def test_no_se_guarda_una_recurrente_con_importe_negativo(client):
    _recurrente(client, amount="-30")

    assert client.db.query(RecurringTransaction).count() == 0


def test_editar_una_transaccion_tampoco_admite_un_importe_negativo(client):
    _alta(client, amount="20")
    tx = client.db.query(Transaction).one()

    client.post_form("/transacciones/%d/editar" % tx.id, data={
        "date": "2026-01-15", "type": "gasto", "amount": "-5", "description": "prueba",
    }, follow_redirects=False)

    client.db.expire_all()
    assert client.db.query(Transaction).one().amount == Decimal("20.00")


def test_confirmar_un_pendiente_tampoco(client):
    tx = Transaction(date=date(2026, 1, 15), amount=Decimal("15.00"), type="gasto",
                     description="pendiente", status="pendiente")
    client.db.add(tx)
    client.db.commit()

    client.post_form("/transacciones/%d/confirmar" % tx.id,
                     data={"amount": "-1", "type": "gasto"}, follow_redirects=False)

    client.db.expire_all()
    assert client.db.query(Transaction).one().amount == Decimal("15.00")


# ---------- FT-M8: exactitud decimal ----------

def test_diez_gastos_de_diez_centimos_suman_un_euro_exacto(client):
    """El defecto que la decisión de diseño declaraba evitar, entrando por la
    puerta del formulario."""
    for _ in range(10):
        _alta(client, amount="0.10")

    total = sum(t.amount for t in client.db.query(Transaction).all())

    assert total == Decimal("1.00")
    assert str(total) == "1.00"


def test_el_formulario_acepta_coma_decimal(client):
    """Es lo que teclea un usuario español, y antes daba un 422."""
    _alta(client, amount="15,50")

    assert client.db.query(Transaction).one().amount == Decimal("15.50")


def test_la_recurrente_tambien_guarda_decimal_exacto(client):
    _recurrente(client, amount="0,10")

    assert client.db.query(RecurringTransaction).one().amount == Decimal("0.10")
