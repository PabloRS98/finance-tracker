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

from sqlalchemy.orm import Session

from ..config import settings
from ..models import RecurringTransaction, Transaction, TransactionStatus
from .market_data import to_base

logger = logging.getLogger(__name__)

# Periodicidades soportadas: meses entre cargos -> etiqueta legible
FREQUENCIES = {1: "Mensual", 2: "Cada 2 meses", 3: "Trimestral", 6: "Semestral", 12: "Anual"}


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


def next_due_date(rule: RecurringTransaction, today: date | None = None) -> date:
    """Próxima ocurrencia que la regla generará (primera >= start_date aún no generada)."""
    floor = rule.last_generated
    for due in _occurrences(rule):
        if due < rule.start_date:
            continue
        if floor is None or due > floor:
            return due


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
