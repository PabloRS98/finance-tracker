"""Tareas periódicas en segundo plano: actualización de precios, snapshot diario
de patrimonio, backups de la BD y generación de transacciones recurrentes."""
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import (
    Asset, AssetType, Currency, NetWorthIntraday, NetWorthSnapshot, SnapshotSource, Watchlist,
    currency_from_code, utcnow,
)
from . import alertas, classify, market_data
from .history import refresh_price_history
from .recurring import generate_due_transactions

logger = logging.getLogger(__name__)


def _currency_str(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def update_all_prices() -> None:
    db: Session = SessionLocal()
    try:
        assets = db.query(Asset).filter(Asset.asset_type.in_([AssetType.ACCION, AssetType.CRIPTO])).all()
        for asset in assets:
            if not asset.ticker:
                continue
            if asset.asset_type == AssetType.ACCION:
                result = market_data.get_stock_price(asset.ticker)
                if result:
                    asset.current_price = result["price"]
                    asset.previous_close = result["previous_close"]
                    detected = currency_from_code(result["currency"])
                    if detected is not None:
                        asset.currency = detected
                    classify.autofill(asset, result)
                    # Renombrado automático "MSFT" -> "Microsoft Corporation" (nombre editable)
                    if result.get("name") and market_data.name_is_placeholder(asset):
                        asset.name = result["name"]
                    asset.last_price_update = utcnow().replace(microsecond=0)
            elif asset.asset_type == AssetType.CRIPTO:
                result = market_data.get_crypto_price(asset.ticker, _currency_str(asset.currency).lower())
                if result is not None:
                    asset.current_price, asset.previous_close = result
                    classify.autofill(asset)
                    if market_data.name_is_placeholder(asset):
                        asset.name = market_data.get_crypto_name(asset.ticker) or asset.name
                    asset.last_price_update = utcnow().replace(microsecond=0)
        seguidos = update_watchlist_prices(db)
        # Las alertas se miran aquí porque es el único momento con cotización
        # nueva; en cualquier otro sitio se evaluarían sobre datos ya vistos.
        # Van dentro del try para que un fallo de Telegram no impida guardar los
        # precios que ya se han descargado.
        try:
            disparadas = alertas.comprobar_y_enviar(db)
        except Exception:
            logger.exception("Fallo al comprobar las alertas de precio")
            disparadas = 0
        db.commit()
        logger.info(
            "Precios actualizados (%d activos, %d en seguimiento, %d alertas)",
            len(assets), seguidos, disparadas,
        )
    finally:
        db.close()


def update_watchlist_prices(db: Session) -> int:
    """Refresca los valores en seguimiento. Devuelve cuántos se han mirado.

    Va con el mismo job que los activos: no tiene sentido enseñar la variación
    del día de un valor que vigilas con un precio de la semana pasada. No hace
    commit: lo cierra quien llama, para que activos y seguidos entren juntos."""
    seguidos = db.query(Watchlist).all()
    for item in seguidos:
        if item.asset_type == AssetType.CRIPTO:
            result = market_data.get_crypto_price(item.ticker, _currency_str(item.currency).lower())
            if result is not None:
                item.current_price, item.previous_close = result
                item.last_price_update = utcnow().replace(microsecond=0)
        else:
            result = market_data.get_stock_price(item.ticker)
            if result:
                item.current_price = result["price"]
                item.previous_close = result["previous_close"]
                detected = currency_from_code(result["currency"])
                if detected is not None:
                    item.currency = detected
                item.last_price_update = utcnow().replace(microsecond=0)
    return len(seguidos)


@dataclass
class Valuation:
    """Resultado de una valoración agregada. `missing` lista las divisas para las
    que no se pudo obtener tipo de cambio: esos activos quedan FUERA de `total`,
    así que un total incompleto nunca debe persistirse ni presentarse como firme."""

    total: float = 0.0
    missing: set[str] = field(default_factory=set)

    @property
    def complete(self) -> bool:
        return not self.missing


def _value_assets(assets, base: str) -> Valuation:
    """Suma los activos convertidos a `base`, apartando los que no se pueden convertir."""
    result = Valuation()
    for asset in assets:
        currency = _currency_str(asset.currency)
        rate = market_data.get_exchange_rate(currency, base)
        if rate is None:
            result.missing.add(currency)
            continue
        result.total += asset.current_value() * rate
    result.total = round(result.total, 2)
    return result


def compute_net_worth(db: Session) -> Valuation:
    """Valor de todos los activos convertido a la moneda base."""
    return _value_assets(db.query(Asset).all(), settings.base_currency)


def compute_manual_value(db: Session) -> Valuation:
    """Parte manual del patrimonio (cuentas/inmuebles), en la moneda base."""
    manual_types = (AssetType.CUENTA, AssetType.OTRO)
    return _value_assets(
        db.query(Asset).filter(Asset.asset_type.in_(manual_types)).all(), settings.base_currency
    )


def compute_net_worth_eur(db: Session) -> float:
    """Atajo para las vistas: el total sin más (puede ser parcial si falta FX).
    Quien necesite saber si está completo debe usar compute_net_worth()."""
    return compute_net_worth(db).total


def sample_intraday(db: Session) -> None:
    """Muestra intradía del patrimonio (total e invertido) para la curva 1D del
    dashboard, y purga de las muestras más viejas que la retención configurada."""
    from .portfolio import portfolio_totals  # import perezoso: portfolio pesa y solo se usa aquí

    valuation = compute_net_worth(db)
    if not valuation.complete:
        logger.warning(
            "Muestra intradía omitida: sin tipo de cambio para %s", ", ".join(sorted(valuation.missing))
        )
        return
    invested = portfolio_totals(db)["invested_value"]
    db.add(NetWorthIntraday(
        ts=utcnow().replace(microsecond=0), total_value=valuation.total, invested_value=invested,
    ))
    cutoff = utcnow() - timedelta(hours=settings.intraday_retention_hours)
    db.query(NetWorthIntraday).filter(NetWorthIntraday.ts < cutoff).delete()
    db.commit()


def sample_intraday_job() -> None:
    db: Session = SessionLocal()
    try:
        sample_intraday(db)
    finally:
        db.close()


def snapshot_net_worth() -> None:
    db: Session = SessionLocal()
    try:
        total = compute_net_worth(db)
        manual = compute_manual_value(db)
        # Un snapshot es histórico permanente: si falta algún tipo de cambio el
        # total estaría incompleto y contaminaría la curva de evolución para
        # siempre. Mejor un hueco en la serie (el job reintenta) que un dato falso.
        if not (total.complete and manual.complete):
            logger.warning(
                "Snapshot de patrimonio omitido: sin tipo de cambio para %s",
                ", ".join(sorted(total.missing | manual.missing)),
            )
            return
        today = date.today()
        existing = db.query(NetWorthSnapshot).filter(NetWorthSnapshot.date == today).first()
        if existing:
            existing.total_value = total.total
            existing.manual_value = manual.total
            existing.source = SnapshotSource.AUTO
        else:
            db.add(NetWorthSnapshot(
                date=today, total_value=total.total, manual_value=manual.total,
                source=SnapshotSource.AUTO,
            ))
        db.commit()
        logger.info("Snapshot de patrimonio guardado: %.2f %s", total.total, settings.base_currency)
    finally:
        db.close()


def refresh_history() -> None:
    """Job diario: rellena los cierres que falten (activos, FX y benchmarks)."""
    db: Session = SessionLocal()
    try:
        refresh_price_history(db)
        logger.info("Histórico de precios actualizado")
    finally:
        db.close()


def generate_recurring() -> None:
    """Job diario: crea las transacciones recurrentes que hayan vencido."""
    db: Session = SessionLocal()
    try:
        generate_due_transactions(db)
    finally:
        db.close()


def backup_database(dest_path: str | None = None) -> str:
    """Copia consistente de la BD (API de backup de SQLite, segura aunque haya
    escrituras). Sin `dest_path` va a /data/backups/finance-AAAAMMDD.db y rota
    las copias antiguas (se conservan `backup_keep`). Devuelve la ruta creada."""
    if dest_path is None:
        backups_dir = os.path.join(os.path.dirname(settings.db_path), "backups")
        os.makedirs(backups_dir, exist_ok=True)
        dest_path = os.path.join(backups_dir, "finance-%s.db" % date.today().strftime("%Y%m%d"))

    src = sqlite3.connect(settings.db_path)
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()

    # Rotación (solo en el directorio estándar de backups)
    backups_dir = os.path.dirname(dest_path)
    if os.path.basename(backups_dir) == "backups":
        existing = sorted(
            f for f in os.listdir(backups_dir)
            if f.startswith("finance-") and f.endswith(".db")
        )
        # existing[:-0] es existing[:0] (lista vacía), no "todos": con
        # BACKUP_KEEP=0 no se borraba ningún backup, justo lo contrario de
        # la intención. Con 0 se conserva al menos el que se acaba de crear.
        a_borrar = existing[:-settings.backup_keep] if settings.backup_keep > 0 else existing[:-1]
        for old in a_borrar:
            os.remove(os.path.join(backups_dir, old))
    logger.info("Backup de la BD guardado en %s", dest_path)
    return dest_path


def send_telegram_summary(titulo: str | None = None) -> None:
    """Job de resumen por Telegram (no-op sin bot configurado). `titulo` distingue
    de qué aviso se trata: sin él, los cinco del día llegaban idénticos."""
    from .telegram_bot import send_daily_summary  # import perezoso: evita ciclo scheduler<->bot

    send_daily_summary(titulo)


# Avisos de apertura y cierre, cada uno anclado a la zona horaria de SU plaza.
#
# Antes las horas estaban puestas en UTC pero el scheduler corre en
# settings.timezone, así que con TIMEZONE=Europe/Madrid los cuatro saltaban dos
# horas antes: el "cierre de Europa" llegaba a las 15:30, con la bolsa aún
# abierta. Declarando la zona de cada mercado la hora es la real, y además se
# ajusta sola al horario de verano, que EE. UU. y Europa no cambian el mismo fin
# de semana (durante un par de semanas al año la diferencia no es de 6 horas).
SESIONES_DE_MERCADO = (
    # id, zona horaria, hora, minuto, título del aviso
    ("market_open_eu", "Europe/Madrid", 9, 0, "Apertura de Europa"),
    ("market_close_eu", "Europe/Madrid", 17, 30, "Cierre de Europa"),
    ("market_open_us", "America/New_York", 9, 30, "Apertura de EE. UU."),
    ("market_close_us", "America/New_York", 16, 0, "Cierre de EE. UU."),
)


def start_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    # next_run_time debe llevar tz: el reloj del contenedor va en UTC y un datetime
    # naive se interpreta en la zona del scheduler (quedaría "en el pasado").
    scheduler.add_job(
        update_all_prices, "interval",
        minutes=settings.price_refresh_minutes,
        next_run_time=datetime.now(scheduler.timezone),
        id="update_prices",
    )
    scheduler.add_job(
        sample_intraday_job, "interval",
        minutes=settings.intraday_sample_minutes,
        next_run_time=datetime.now(scheduler.timezone) + timedelta(seconds=40),
        id="intraday_sample",
    )
    scheduler.add_job(snapshot_net_worth, "cron", hour=23, minute=55, id="daily_snapshot")
    scheduler.add_job(
        send_telegram_summary, "cron",
        hour=settings.telegram_summary_hour, minute=settings.telegram_summary_minute,
        id="daily_telegram_summary",
    )
    scheduler.add_job(generate_recurring, "cron", hour=0, minute=15, id="daily_recurring")
    scheduler.add_job(backup_database, "cron", hour=4, minute=45, id="daily_backup")
    scheduler.add_job(
        refresh_history, "cron",
        hour=6, minute=30,
        next_run_time=datetime.now(scheduler.timezone) + timedelta(seconds=20),  # también al poco de arrancar
        id="daily_history",
    )
    # ── Resúmenes de apertura/cierre de mercados ──────────────────────
    for job_id, zona, hora, minuto, titulo in SESIONES_DE_MERCADO:
        scheduler.add_job(
            send_telegram_summary, "cron",
            day_of_week="mon-fri", hour=hora, minute=minuto,
            timezone=ZoneInfo(zona),
            args=[titulo],
            id=job_id,
        )
    scheduler.start()
    return scheduler
