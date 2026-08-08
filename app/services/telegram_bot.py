"""Bot de Telegram del tracker: resumen diario de activos y registro de
compras/ventas y gastos/ingresos por mensaje de texto o nota de voz.

Funciona por long polling (getUpdates) en un hilo de fondo del lifespan: nada
queda expuesto a internet (local-first). Solo atiende al chat configurado en
FINANCE_TELEGRAM_CHAT_ID; a cualquier otro chat le responde una única cosa: su
chat_id, para poder configurarlo la primera vez.

Lo que llega se interpreta con el mismo parser que la voz del navegador
(services/voice_parser.py) y se crea PENDIENTE; el bot pide confirmación con
botones ✅/❌ y solo al confirmar pasa a CONFIRMADO (o se borra al rechazar)."""
import logging
import re
import threading
from datetime import date, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..database import SessionLocal
from ..models import (
    Asset, AssetType, Operation, OperationType, PriceHistory, Transaction, TransactionStatus, TransactionType,
)
from ..templating import dinero
from . import market_data, stt, telegram
from ._telegram_fmt import escapar
from .history import eur_usd_snapshot
from .portfolio import asset_summary, portfolio_totals
from .scheduler import compute_net_worth_eur
from .voice_parser import parse_voice_operation, parse_voice_text

logger = logging.getLogger(__name__)

MAX_VOICE_SECONDS = 300  # nota de voz máxima; más largo no es una operación, es un podcast

AYUDA = (
    "🤖 <b>Midas</b> — tu patrimonio por Telegram\n\n"
    "Mándame un mensaje de texto o una nota de voz:\n"
    " · «compré 0,5 bitcoin a 54.000»\n"
    " · «vendí 2 nvidia a 190 ayer»\n"
    " · «gasté 25 euros en comida»\n"
    " · «me ingresaron 1.500 de nómina»\n\n"
    "Te enseñaré lo que he entendido y no aplico nada sin tu ✅.\n"
    "El activo debe existir ya en la app (lo busco por nombre o ticker).\n\n"
    "Comandos: /resumen · /ayuda\n"
    "Resumen automático todos los días a las %02d:%02d."
    % (settings.telegram_summary_hour, settings.telegram_summary_minute)
)


DIAS_SEMANA = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")


# La función vive ahora en _telegram_fmt, compartida con alertas.py, que la
# necesitaba y no la tenía. Se conserva el nombre local para no tocar las 8
# interpolaciones de este módulo, que ya la aplicaban bien.
_esc = escapar


def _fmt_pct(value: float) -> str:
    return ("%+.2f%%" % value).replace(".", ",")


_PALABRAS_ETF = (
    "ETF", "MSCI", "S&P", "NASDAQ", "FTSE", "STOXX",
    "Index", "Fondo", "Fidelity", "Vanguard", "iShares",
    "Amundi", "Xtrackers", "Lyxor", "Invesco", "WisdomTree",
    "World", "Emerging Markets", "Core", "Acc", "Dist",
    "Treasury", "Bond", "Government",
)

# Palabra completa, no subcadena: buscando "ACC" o "BOND" sueltos, "Accenture" y
# "Bonduelle" pasaban por fondos. Los límites se miran contra alfanuméricos y no
# con \b, porque hay claves con símbolo ("S&P") a las que \b no se aplica igual.
_RE_ETF = re.compile(
    "|".join(r"(?<![A-Z0-9])%s(?![A-Z0-9])" % re.escape(p.upper()) for p in _PALABRAS_ETF)
)


def _is_etf(name: str | None) -> bool:
    """Heurística: detecta ETFs/fondos por palabras clave en el nombre.

    Es una aproximación a propósito: el modelo no distingue fondo de acción
    (ambos son AssetType.ACCION) y el nombre es lo único que hay. Falla en
    nombres exóticos, pero solo afecta a en qué caja del resumen sale cada
    posición, no a ningún cálculo."""
    return bool(name) and _RE_ETF.search(name.upper()) is not None


def _buttons(kind: str, item_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Confirmar", "callback_data": "%s:ok:%d" % (kind, item_id)},
        {"text": "❌ Rechazar", "callback_data": "%s:no:%d" % (kind, item_id)},
    ]]}


# ---------- Interpretar texto -> pendiente con botones ----------

def process_text(db: Session, text: str) -> tuple[str, dict | None]:
    """Interpreta el texto y crea la operación/transacción PENDIENTE.
    Devuelve (mensaje de respuesta, teclado de botones o None si hubo error)."""
    op = parse_voice_operation(text, db)
    if op is not None:
        if op["error"]:
            return "⚠️ " + op["error"], None
        operation = Operation(
            asset_id=op["asset"].id,
            type=OperationType(op["type"]),
            date=op["date"],
            quantity=op["quantity"],
            unit_price=op["unit_price"],
            status=TransactionStatus.PENDIENTE,
            source="telegram",
        )
        db.add(operation)
        db.commit()
        db.refresh(operation)
        reply = "%s <b>%s</b> · %g × %s %s · %s\n¿La aplico?" % (
            "📈 Compra" if op["type"] == "compra" else "📉 Venta",
            _esc(op["asset"].name), op["quantity"],
            dinero(op["unit_price"]), op["asset"].currency.value,
            op["date"].strftime("%d/%m/%Y"),
        )
        return reply, _buttons("op", operation.id)

    parsed = parse_voice_text(text, db)
    if parsed["amount"] is None:
        return ("⚠️ No he entendido el importe. Ejemplos: «gasté 20 euros en comida», "
                "«compré 0,5 bitcoin a 54.000»."), None

    # Transaction.amount va SIEMPRE en la moneda base: un «gasté 20 dólares»
    # dictado por Telegram hay que convertirlo antes de apuntarlo.
    amount = market_data.to_base(parsed["amount"], parsed["currency"], settings.base_currency)
    if amount is None:
        return "⚠️ No hay tipo de cambio %s→%s ahora mismo; inténtalo en unos minutos." % (
            parsed["currency"], settings.base_currency,
        ), None

    tx = Transaction(
        date=parsed["date"],
        type=TransactionType(parsed["type"]),
        category_id=parsed["category_id"],
        amount=amount,
        description=parsed["description"],
        status=TransactionStatus.PENDIENTE,
        source="telegram",
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    origen = ""
    if parsed["currency"] != settings.base_currency:
        origen = " (%s %s)" % (dinero(parsed["amount"]), parsed["currency"])
    reply = "%s <b>%s %s</b>%s%s · %s\n¿Lo apunto?" % (
        "💰 Ingreso" if parsed["type"] == "ingreso" else "💸 Gasto",
        dinero(amount), settings.base_currency, origen,
        " · %s" % _esc(parsed["category_name"]) if parsed["category_name"] else "",
        parsed["date"].strftime("%d/%m/%Y"),
    )
    return reply, _buttons("tx", tx.id)


def handle_callback(db: Session, data: str) -> str:
    """Aplica un botón ✅/❌ ("op:ok:12"). Devuelve el texto final del mensaje."""
    try:
        kind, action, raw_id = data.split(":")
        item_id = int(raw_id)
    except ValueError:
        return "⚠️ Botón no reconocido."

    model = Operation if kind == "op" else Transaction
    item = db.get(model, item_id)
    if item is None:
        return "⚠️ Ya no existe (¿confirmado o rechazado desde la web?)."
    if item.status != TransactionStatus.PENDIENTE:
        return "✅ Ya estaba confirmado."

    if action == "no":
        db.delete(item)
        db.commit()
        return "❌ Descartado."

    item.status = TransactionStatus.CONFIRMADO
    db.commit()
    if kind == "op":
        return "✅ Operación aplicada: %s %g × %s de %s." % (
            "compra" if item.type == OperationType.COMPRA else "venta",
            item.quantity, dinero(item.unit_price), _esc(item.asset.name),
        )
    return "✅ Apuntado: %s de %s €." % (
        "ingreso" if item.type == TransactionType.INGRESO else "gasto", dinero(item.amount),
    )


# ---------- Resumen diario ----------

def _recent_ops_analysis(db: Session, days: int = 30) -> list[str]:
    """Analiza compras/ventas recientes: compara precio de ejecución con el actual."""
    since = date.today() - timedelta(days=days - 1)
    ops = (
        db.query(Operation)
        .filter(
            Operation.status == TransactionStatus.CONFIRMADO,
            Operation.date >= since,
        )
        .order_by(Operation.date.desc(), Operation.id.desc())
        .all()
    )
    if not ops:
        return []

    lines = []
    for op in ops:
        asset = op.asset
        current = asset.current_price
        if current is None and asset.ticker:
            last = (
                db.query(PriceHistory.price)
                .filter(PriceHistory.symbol == asset.ticker)
                .order_by(PriceHistory.date.desc())
                .first()
            )
            current = last[0] if last else None
        if current is None or op.unit_price == 0:
            continue

        name = _esc(asset.name or asset.ticker or "?")
        if op.type == OperationType.COMPRA:
            pct = (current - op.unit_price) / op.unit_price * 100
            emoji = "🟢" if pct >= 0 else "🔴"
            lines.append(
                "  %s %s · compra a %s → ahora %s (%s)"
                % (emoji, name, dinero(op.unit_price), dinero(current), _fmt_pct(pct))
            )
        else:
            pct = (op.unit_price - current) / op.unit_price * 100
            emoji = "🟢" if pct >= 0 else "🔴"
            lines.append(
                "  %s %s · venta a %s → ahora %s (%s)"
                % (emoji, name, dinero(op.unit_price), dinero(current), _fmt_pct(pct))
            )

    return lines


def _positions_grouped(db: Session, total_eur: float) -> list[str]:
    """Posiciones abiertas agrupadas por categoría (ETFs / Acciones / Crypto)
    con % de asignación sobre el patrimonio total. Ordenadas por P&L dentro de
    cada grupo. Devuelve líneas con formato de caja ┌─┐│└─┘."""
    assets = (
        db.query(Asset)
        .filter(Asset.asset_type.in_([AssetType.ACCION, AssetType.CRIPTO]))
        .all()
    )
    rows = []
    sin_cambio: list[str] = []
    for a in assets:
        s = asset_summary(a)
        qty = s.get("quantity")
        # El umbral no es 0 sino una fracción ínfima: al vender entera una
        # posición, restar los flotantes deja un residuo tipo 4.44e-16 que
        # aparecía en el resumen como una posición viva de cero unidades.
        if not qty or qty <= 1e-9:
            continue
        # El peso se calcula contra `total_eur`, que YA viene convertido a la
        # moneda base. Antes el numerador se dejaba en la divisa del activo:
        # se dividían peras entre manzanas y una posición en dólares aparecía
        # con un peso ~8 % por debajo del real, inflando todas las demás.
        rate = market_data.get_exchange_rate(a.currency.value, settings.base_currency)
        if rate is None:
            # Fuera del agregado antes que contarlo 1:1, igual que hace
            # portfolio_totals: un peso falso contamina la decisión de
            # rebalanceo y no deja rastro de que estaba mal.
            sin_cambio.append(a.name)
            continue
        value_base = (a.current_price or 0) * qty * rate
        alloc = (value_base / total_eur * 100) if total_eur > 0 else 0
        rows.append((a, s, alloc))

    # Agrupar
    grupos: dict[str, list] = {
        "🌍 ETFs": [],
        "🇺🇸 Acciones": [],
        "🪙 Crypto": [],
    }
    for a, s, alloc in rows:
        if a.asset_type == AssetType.CRIPTO:
            grupos["🪙 Crypto"].append((a, s, alloc))
        elif _is_etf(a.name):
            grupos["🌍 ETFs"].append((a, s, alloc))
        else:
            grupos["🇺🇸 Acciones"].append((a, s, alloc))

    def _sort_key(item):
        _, s, _ = item
        pnl = s.get("pnl_pct")
        if pnl is None:
            return (1, 0)
        return (0, -pnl)

    BOX_W = 48  # ancho interior de las cajas
    lines = []
    for grupo_nombre, items in grupos.items():
        if not items:
            continue
        items.sort(key=_sort_key)
        grupo_peso = sum(alloc for _, _, alloc in items)

        header = "%s · %.0f%% de cartera" % (grupo_nombre, grupo_peso)
        lines.append("┌─ %s %s┐" % (header, "─" * max(1, BOX_W - len(header) - 3)))
        for a, s, alloc in items:
            name = _esc(a.name or a.ticker or "?")
            if len(name) > 30:
                name = name[:28] + "…"
            qty = s["quantity"]
            cost = s.get("avg_cost")
            price = a.current_price
            pnl = s.get("pnl_pct")

            if price is None:
                inner = "  ⚪ %s · %.4gu · (%.0f%%)" % (name, qty, alloc)
            elif pnl is not None and cost is not None and cost > 0:
                emoji = "🟢" if pnl >= 0 else "🔴"
                inner = "  %s %s %s · %.4gu · (%.0f%%)" % (
                    emoji, _fmt_pct(pnl), name, qty, alloc,
                )
            else:
                inner = "  %s · %.4gu × %s · (%.0f%%)" % (
                    name, qty, dinero(price), alloc,
                )
            lines.append("│%s%s│" % (inner, " " * max(0, BOX_W - len(inner) + 1)))
        lines.append("└" + "─" * (BOX_W + 1) + "┘")
        lines.append("")

    if sin_cambio:
        # Un porcentaje que no suma 100 sin explicación es peor que uno que
        # falta: si se han dejado posiciones fuera, hay que decirlo.
        lines.append("⚠️ Sin tipo de cambio, fuera del reparto: %s" % _esc(", ".join(sin_cambio)))
        lines.append("")

    return lines


def build_summary(db: Session, titulo: str | None = None) -> str:
    """Resumen de activos para Telegram: KPIs en caja, cartera agrupada por
    tipo de activo (ETFs / Acciones / Crypto) con % de asignación, top
    movimientos del día y operaciones recientes (30 días)."""
    total = compute_net_worth_eur(db)
    invested = portfolio_totals(db)

    BOX_W = 42

    # ── Cabecera ──
    # El título distingue de cuál de los cinco avisos diarios se trata
    # ("Cierre de EE. UU."); sin él, el día de la semana. No se usa strftime("%A")
    # porque el contenedor va en locale C y devolvía "FRIDAY" en una app que
    # está entera en español.
    fecha = date.today().strftime("%d/%m/%Y")
    encabezado = _esc(titulo) if titulo else DIAS_SEMANA[date.today().weekday()]
    lines = ["┌─ 📊 %s %s ──┐" % (encabezado.upper(), fecha)]

    lines.append("│  💰 %s %s" % (dinero(total), settings.base_currency))

    if invested["day_change"] is not None:
        emoji = "🟢" if invested["day_change"] >= 0 else "🔴"
        pct = " (%s)" % _fmt_pct(invested["day_change_pct"]) if invested["day_change_pct"] is not None else ""
        lines.append("│  %s %s %s hoy" % (emoji, dinero(invested["day_change"]), pct))

    if invested["unrealized"] is not None:
        pct = " (%s)" % _fmt_pct(invested["pnl_pct"]) if invested["pnl_pct"] is not None else ""
        lines.append("│  📈 %s %s P&L total" % (dinero(invested["unrealized"]), pct))

    fx_parts = []
    if invested["fx_effect_pct"] is not None:
        fx_parts.append("💱 Divisa %s" % _fmt_pct(invested["fx_effect_pct"]))
    fx = eur_usd_snapshot(db)
    if fx["change_pct"] is not None:
        fx_parts.append("€/$ %s" % ("%.4f" % fx["rate"]).replace(".", ","))
    if fx_parts:
        lines.append("│  %s" % "  ·  ".join(fx_parts))

    lines.append("└" + "─" * (BOX_W + 1) + "┘")
    lines.append("")

    # ── Cartera agrupada ──
    positions = _positions_grouped(db, total)
    if positions:
        lines.extend(positions)

    # ── Top movimientos del día ──
    movers = []
    assets = db.query(Asset).filter(Asset.asset_type.in_([AssetType.ACCION, AssetType.CRIPTO])).all()
    for asset in assets:
        s = asset_summary(asset)
        # Mismo umbral que en la cartera: una posición vendida entera deja un
        # residuo de coma flotante (4e-16) que es "verdadero" pero no es nada.
        if s["day_change_pct"] is not None and (s["quantity"] or 0) > 1e-9:
            movers.append((asset.name, s["day_change_pct"]))
    movers.sort(key=lambda m: abs(m[1]), reverse=True)
    if movers:
        lines.append("┌─ 🔥 Top movimientos hoy ──────────────────┐")
        for name, pct in movers[:5]:
            arrow = "▲" if pct >= 0 else "▼"
            lines.append("│  %s %s %s" % (arrow, _esc(name or "?"), _fmt_pct(pct)))
        lines.append("└" + "─" * (BOX_W + 1) + "┘")
        lines.append("")

    # ── Operaciones 30 días ──
    ops_lines = _recent_ops_analysis(db)
    if ops_lines:
        lines.append("┌─ 🛒 Operaciones 30 días ───────────────────┐")
        for ol in ops_lines:
            # _recent_ops_analysis devuelve líneas con "  " prefijo; las
            # metemos dentro de la caja recortando el prefijo sobrante
            inner = ol[2:] if ol.startswith("  ") else ol
            lines.append("│ %s%s│" % (inner, " " * max(0, BOX_W - len(inner) + 1)))
        lines.append("└" + "─" * (BOX_W + 1) + "┘")

    return "\n".join(lines)


def send_daily_summary(titulo: str | None = None) -> None:
    """Jobs de resumen del scheduler (y comando /resumen)."""
    if not telegram.is_configured():
        return
    db = SessionLocal()
    try:
        telegram.send_message(build_summary(db, titulo))
    finally:
        db.close()


# ---------- Bucle de recepción (long polling) ----------

_stop = threading.Event()


def _handle_message(msg: dict) -> None:
    chat_id = str(msg["chat"]["id"])
    if not settings.telegram_chat_id:
        # Bootstrap: sin chat autorizado, lo único que hace el bot es decirte tu id
        logger.info("Telegram sin chat_id configurado; mensaje recibido de chat_id=%s", chat_id)
        telegram.send_message(
            "👋 Tu chat_id es <code>%s</code>. Ponlo en FINANCE_TELEGRAM_CHAT_ID "
            "del .env y reinicia la app para activarme." % chat_id,
            chat_id=chat_id,
        )
        return
    if chat_id != settings.telegram_chat_id:
        logger.warning("Mensaje de chat no autorizado ignorado: %s", chat_id)
        return

    text = (msg.get("text") or "").strip()
    voice = msg.get("voice") or msg.get("audio")
    prefix = ""
    if voice:
        if (voice.get("duration") or 0) > MAX_VOICE_SECONDS:
            telegram.send_message("⚠️ Nota de voz demasiado larga (máx. %d s)." % MAX_VOICE_SECONDS)
            return
        audio = telegram.download_file(voice["file_id"])
        text = stt.transcribe(audio) if audio else None
        if not text:
            telegram.send_message("⚠️ No he podido transcribir la nota de voz.")
            return
        prefix = "🎤 «%s»\n\n" % _esc(text)

    if not text:
        return
    if text.startswith("/"):
        command = text.split()[0].split("@")[0].lower()
        if command == "/resumen":
            send_daily_summary()
        else:  # /start, /ayuda y cualquier cosa desconocida
            telegram.send_message(AYUDA)
        return

    db = SessionLocal()
    try:
        reply, markup = process_text(db, text)
    finally:
        db.close()
    telegram.send_message(prefix + reply, reply_markup=markup)


def _handle_callback_query(cq: dict) -> None:
    if str(cq["message"]["chat"]["id"]) != settings.telegram_chat_id:
        telegram.answer_callback(cq["id"])
        return
    db = SessionLocal()
    try:
        result = handle_callback(db, cq.get("data") or "")
    finally:
        db.close()
    telegram.answer_callback(cq["id"])
    # Reescribe el mensaje original conservando la descripción, sin botones.
    # `message.text` llega ya renderizado (sin HTML): se re-escapa al reenviar.
    original = cq["message"].get("text") or ""
    summary_line = _esc(original.split("\n¿")[0])
    telegram.edit_message(cq["message"]["message_id"], "%s\n\n%s" % (summary_line, result))


def _poll_loop() -> None:
    logger.info("Bot de Telegram escuchando (long polling)")
    offset = None
    while not _stop.is_set():
        try:
            for update in telegram.get_updates(offset):
                offset = update["update_id"] + 1
                if "callback_query" in update:
                    _handle_callback_query(update["callback_query"])
                elif "message" in update:
                    _handle_message(update["message"])
        except Exception:
            logger.exception("Error en el bucle del bot de Telegram")
            _stop.wait(10)


def start_bot() -> threading.Thread | None:
    """Arranca el hilo de polling si hay token. Hilo daemon: muere con la app."""
    if not settings.telegram_bot_token:
        logger.info("Bot de Telegram desactivado (sin FINANCE_TELEGRAM_BOT_TOKEN)")
        return None
    _stop.clear()
    thread = threading.Thread(target=_poll_loop, name="telegram-bot", daemon=True)
    thread.start()
    return thread


def stop_bot() -> None:
    _stop.set()
