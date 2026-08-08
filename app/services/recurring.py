"""Generación de transacciones a partir de reglas recurrentes.

La periodicidad la marca `interval_months` (1 mensual, 2, 3 trimestral, 6
semestral, 12 anual), anclada a `start_date`. Idempotente y con catch-up: cada
regla recuerda la última ocurrencia generada (last_generated) y aquí se crean
todas las que falten hasta hoy. Se ejecuta al arrancar la app y una vez al día,
así que da igual si el servidor estuvo apagado el día del cargo.

Si la regla está en otra divisa, el importe se convierte a la moneda base del
libro con el tipo de cambio del momento de la generación.
"""
import calendar
import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.orm import Session

from ..config import settings
from ..models import RecurringTransaction, Transaction, TransactionStatus, TransactionType
from .market_data import to_base

logger = logging.getLogger(__name__)

# Periodicidades soportadas: meses entre cargos -> etiqueta legible
FREQUENCIES = {1: "Mensual", 2: "Cada 2 meses", 3: "Trimestral", 6: "Semestral", 12: "Anual"}

_CENT = Decimal("0.01")
SIN_CATEGORIA = "Sin categoría"


def clamped_date(year: int, month: int, day: int) -> date:
    """Fecha con el día ajustado al último del mes si no existe (31 -> 28/30)."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _add_months(year: int, month: int, n: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + n
    return total // 12, total % 12 + 1


def _interval(rule: RecurringTransaction) -> int:
    return rule.interval_months if rule.interval_months in FREQUENCIES else 1


def _occurrences(rule: RecurringTransaction):
    """Fechas de cargo en orden ascendente, ancladas al mes de start_date y
    separadas por interval_months. Generador infinito: cortar con la fecha tope."""
    step = _interval(rule)
    k = 0
    while True:
        year, month = _add_months(rule.start_date.year, rule.start_date.month, k * step)
        yield clamped_date(year, month, rule.day_of_month)
        k += 1


# Tope de ocurrencias a recorrer: 100 años de cargos mensuales. Ninguna regla
# real llega ahí, y sin tope el recorrido depende de lo que haya en
# `last_generated`, que no valida nadie.
MAX_OCURRENCIAS = 1200


def next_due_date(rule: RecurringTransaction, today: date | None = None) -> date | None:
    """Próxima ocurrencia que la regla generará (primera >= start_date aún no generada).

    Con `today`, además se descartan las ocurrencias anteriores a esa fecha: una
    regla reactivada tras meses parada tiene un `last_generated` viejo, y la
    siguiente ocurrencia teórica cae en el pasado. Sin `today` no se filtra, que
    es lo que necesita el catch-up de generación.

    Devuelve None si no encuentra ninguna dentro del tope. Antes el bucle no
    tenía fin declarado y el retorno decía `date`: con un `last_generated`
    absurdo —lo escriben la generación y el toggle, y nada valida lo que llegue
    de una base restaurada a medias— recorría cientos de miles de ocurrencias
    para acabar proponiendo un cargo en el año 2999, y con `date.max` reventaba
    con `ValueError: year 10000 is out of range` en mitad de /recurrentes.
    """
    floor = rule.last_generated
    for _, due in zip(range(MAX_OCURRENCIAS), _occurrences(rule), strict=False):
        if due < rule.start_date:
            continue
        if floor is not None and due <= floor:
            continue
        if today is not None and due < today:
            continue
        return due

    logger.warning(
        "La regla recurrente %s no tiene próxima fecha dentro de %d ocurrencias; "
        "revisa su last_generated (%s)",
        rule.id, MAX_OCURRENCIAS, rule.last_generated,
    )
    return None


def generate_due_transactions(db: Session) -> int:
    """Crea las transacciones pendientes de generar de todas las reglas activas.
    Devuelve cuántas se han creado."""
    today = date.today()
    created = 0

    for rule in db.query(RecurringTransaction).filter(RecurringTransaction.active.is_(True)).all():
        for due in _occurrences(rule):
            if due > today:
                break
            if due < rule.start_date:
                continue
            if rule.last_generated is not None and due <= rule.last_generated:
                continue

            rule_cur = rule.currency.value
            amount = to_base(rule.amount, rule_cur, settings.base_currency)
            if amount is None:
                # Sin tipo de cambio no se genera: `last_generated` no avanza,
                # así que el catch-up del próximo arranque/job la creará bien.
                # Crearla ahora sin convertir metería un importe falso en el libro.
                logger.warning(
                    "Recurrente %r (%s) aplazada: sin tipo de cambio %s->%s",
                    rule.name, due, rule_cur, settings.base_currency,
                )
                break

            db.add(Transaction(
                date=due,
                type=rule.type,
                category_id=rule.category_id,
                amount=amount,
                description=rule.name,
                status=TransactionStatus.CONFIRMADO,
                source="recurrente",
            ))
            rule.last_generated = due
            created += 1

    db.commit()
    if created:
        logger.info("Recurrentes: %d transacciones generadas", created)
    return created


# ---------- Coste mensual ----------
# Un recibo trimestral de 300 aparece como un gasto de 300 tres veces al año, y
# en la lista compite visualmente con el alquiler. Normalizarlo a lo que pesa
# cada mes es lo que permite comparar reglas de periodicidades distintas y saber
# cuánto se va en recurrentes sin esperar a que caiga el cargo.

def coste_mensual(rule: RecurringTransaction) -> Decimal:
    """Lo que pesa al mes la regla, en SU divisa. Trimestral de 300 -> 100."""
    return (Decimal(rule.amount) / _interval(rule)).quantize(_CENT, rounding=ROUND_HALF_UP)


def resumen_mensual(db: Session) -> dict:
    """Cuánto suman al mes las recurrentes, convertido a la moneda base.

    Solo cuentan las activas: una regla pausada no se cobra, y meterla en el
    total daría una cifra que no se corresponde con lo que sale de la cuenta.

    Los totales se suman a partir de los importes YA redondeados de cada regla,
    los mismos que se ven en la lista. Sumar los exactos y redondear al final
    sería algo más preciso, pero dejaría un total que no cuadra con la columna
    que el usuario tiene delante, y eso se nota más que un céntimo.
    """
    base = settings.base_currency
    gastos = ingresos = Decimal("0.00")
    por_categoria: dict[str, Decimal] = {}
    sin_cambio: list[str] = []
    pausadas = 0

    for rule in db.query(RecurringTransaction).all():
        if not rule.active:
            pausadas += 1
            continue
        en_base = to_base(coste_mensual(rule), rule.currency.value, base)
        if en_base is None:
            # Sin tipo de cambio no se suma: contarlo 1:1 inventaría el total.
            sin_cambio.append(rule.name)
            continue
        if rule.type == TransactionType.INGRESO:
            ingresos += en_base
        else:
            gastos += en_base
            etiqueta = rule.category.name if rule.category else SIN_CATEGORIA
            por_categoria[etiqueta] = por_categoria.get(etiqueta, Decimal("0.00")) + en_base

    return {
        "gastos": gastos,
        "ingresos": ingresos,
        "neto": ingresos - gastos,
        "gastos_anuales": gastos * 12,
        "por_categoria": sorted(por_categoria.items(), key=lambda kv: kv[1], reverse=True),
        "sin_cambio": sin_cambio,
        "pausadas": pausadas,
        "base_currency": base,
    }
