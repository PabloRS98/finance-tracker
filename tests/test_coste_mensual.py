"""Coste mensual de las recurrentes y cuánto suman entre todas.

Un recibo trimestral de 300 aparecía en la lista como un gasto de 300, tres
veces al año, compitiendo visualmente con el alquiler. Normalizarlo a lo que
pesa cada mes es lo que permite comparar reglas de periodicidades distintas y
saber cuánto se va en fijos sin esperar a que caiga el cargo.
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models import Category, Currency, RecurringTransaction, TransactionType
from app.services.recurring import coste_mensual, resumen_mensual


def _regla(db, nombre, importe, meses=1, tipo=TransactionType.GASTO,
           divisa=Currency.EUR, activa=True, categoria=None):
    regla = RecurringTransaction(
        name=nombre, type=tipo, amount=Decimal(str(importe)), currency=divisa,
        interval_months=meses, day_of_month=1, start_date=date(2025, 1, 1),
        active=activa, category_id=categoria,
    )
    db.add(regla)
    db.commit()
    return regla


# ---------- Coste de una regla ----------

@pytest.mark.parametrize("importe,meses,esperado", [
    ("30.00", 1, "30.00"),     # mensual: se queda igual
    ("300.00", 3, "100.00"),   # trimestral
    ("120.00", 12, "10.00"),   # anual
    ("16.00", 6, "2.67"),      # semestral, con redondeo
    ("49.90", 12, "4.16"),     # anual, con redondeo
])
def test_reparte_el_importe_entre_los_meses_del_periodo(db, importe, meses, esperado):
    regla = _regla(db, "Prueba", importe, meses)

    assert coste_mensual(regla) == Decimal(esperado)


def test_una_periodicidad_desconocida_se_trata_como_mensual(db):
    """Es lo que hace ya el generador: si el intervalo no es de los soportados,
    no se puede inventar un reparto."""
    regla = _regla(db, "Rara", "50.00", meses=7)

    assert coste_mensual(regla) == Decimal("50.00")


# ---------- Total ----------

def test_suma_lo_que_pesa_al_mes_cada_regla(db, sin_red):
    _regla(db, "Alquiler", "800.00", 1)
    _regla(db, "Seguro", "300.00", 3)      # 100/mes
    _regla(db, "Dominio", "120.00", 12)    # 10/mes

    resumen = resumen_mensual(db)

    assert resumen["gastos"] == Decimal("910.00")


def test_el_total_cuadra_con_la_columna_que_se_ve(db, sin_red):
    """Sumar los importes exactos y redondear al final sería algo más preciso,
    pero dejaría un total que no cuadra con la lista que el usuario tiene
    delante, y un céntimo que no encaja se nota más que un céntimo perdido."""
    for i in range(3):
        _regla(db, "Regla %d" % i, "100.00", 3)   # 33,33 cada una

    resumen = resumen_mensual(db)
    reglas = db.query(RecurringTransaction).all()

    assert resumen["gastos"] == sum(coste_mensual(r) for r in reglas)
    assert resumen["gastos"] == Decimal("99.99")


def test_ingresos_y_gastos_van_por_separado(db, sin_red):
    _regla(db, "Nómina", "1300.00", 1, tipo=TransactionType.INGRESO)
    _regla(db, "Alquiler", "800.00", 1)

    resumen = resumen_mensual(db)

    assert resumen["ingresos"] == Decimal("1300.00")
    assert resumen["gastos"] == Decimal("800.00")
    assert resumen["neto"] == Decimal("500.00")


def test_el_anual_son_doce_meses(db, sin_red):
    _regla(db, "Seguro", "300.00", 3)

    assert resumen_mensual(db)["gastos_anuales"] == Decimal("1200.00")


def test_una_pausada_no_suma(db, sin_red):
    """No se cobra, así que meterla daría una cifra que no se corresponde con lo
    que sale de la cuenta."""
    _regla(db, "Activa", "50.00", 1)
    _regla(db, "Pausada", "500.00", 1, activa=False)

    resumen = resumen_mensual(db)

    assert resumen["gastos"] == Decimal("50.00")
    assert resumen["pausadas"] == 1


def test_sin_tipo_de_cambio_no_se_suma_pero_se_avisa(db, monkeypatch):
    """Contar 20 USD como 20 EUR inventaría el total; callarlo dejaría un total
    incompleto sin decirlo."""
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: None)
    _regla(db, "Suscripción USA", "20.00", 1, divisa=Currency.USD)
    _regla(db, "Alquiler", "800.00", 1)

    resumen = resumen_mensual(db)

    assert resumen["gastos"] == Decimal("800.00")
    assert resumen["sin_cambio"] == ["Suscripción USA"]


def test_convierte_a_la_moneda_base(db, monkeypatch):
    from app.services import market_data

    monkeypatch.setattr(market_data, "get_exchange_rate", lambda a, b: 0.9)
    _regla(db, "Suscripción USA", "120.00", 12, divisa=Currency.USD)   # 10 USD/mes

    assert resumen_mensual(db)["gastos"] == Decimal("9.00")


# ---------- En qué se va ----------

def test_agrupa_los_gastos_por_categoria(db, sin_red):
    ocio = Category(name="Ocio", keywords="")
    casa = Category(name="Vivienda", keywords="")
    db.add_all([ocio, casa])
    db.commit()
    _regla(db, "Netflix", "12.00", 1, categoria=ocio.id)
    _regla(db, "Dominio", "120.00", 12, categoria=ocio.id)   # 10/mes
    _regla(db, "Alquiler", "800.00", 1, categoria=casa.id)

    por_categoria = dict(resumen_mensual(db)["por_categoria"])

    assert por_categoria == {"Vivienda": Decimal("800.00"), "Ocio": Decimal("22.00")}


def test_lo_que_no_tiene_categoria_no_se_pierde(db, sin_red):
    _regla(db, "Suelto", "30.00", 1)

    assert dict(resumen_mensual(db)["por_categoria"]) == {"Sin categoría": Decimal("30.00")}


def test_las_categorias_van_de_mayor_a_menor(db, sin_red):
    """Es el orden en el que se busca dónde recortar."""
    poco = Category(name="Poco", keywords="")
    mucho = Category(name="Mucho", keywords="")
    db.add_all([poco, mucho])
    db.commit()
    _regla(db, "A", "10.00", 1, categoria=poco.id)
    _regla(db, "B", "900.00", 1, categoria=mucho.id)

    assert [n for n, _ in resumen_mensual(db)["por_categoria"]] == ["Mucho", "Poco"]


def test_los_ingresos_no_entran_en_el_desglose_de_gastos(db, sin_red):
    nomina = Category(name="Nómina/Ingresos", keywords="")
    db.add(nomina)
    db.commit()
    _regla(db, "Nómina", "1300.00", 1, tipo=TransactionType.INGRESO, categoria=nomina.id)

    assert resumen_mensual(db)["por_categoria"] == []


# ---------- La página ----------

def test_la_pagina_ensena_el_total_y_la_columna(client):
    _regla(client.db, "Seguro", "300.00", 3)

    html = client.get("/recurrentes").text

    assert "Gasto recurrente al mes" in html
    assert "Al mes" in html
    assert "100,00" in html, "el trimestral de 300 pesa 100 al mes"


def test_sin_recurrentes_no_se_ensena_un_total_de_cero(client):
    """Una portada con 0,00 € en grande sugiere que hay algo cuando no lo hay."""
    html = client.get("/recurrentes").text

    assert "Gasto recurrente al mes" not in html
    assert "Sin recurrentes todavía" in html


def test_las_mas_caras_al_mes_salen_primero(client):
    """El importe del cargo no dice cuál sale más caro: un anual de 120 pesa
    menos que un mensual de 15."""
    _regla(client.db, "Anual gordo", "120.00", 12)   # 10/mes
    _regla(client.db, "Mensual", "15.00", 1)         # 15/mes

    html = client.get("/recurrentes").text

    assert html.index("Mensual<") < html.index("Anual gordo")
