"""Modelos de datos: activos, transacciones, categorías, snapshots y recurrentes."""
import enum
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

# Importes del libro (gastos, ingresos, presupuestos): dinero exacto que se suma,
# así que va en Numeric -> Decimal, no en float. Con float, diez gastos de 0,10 €
# suman 0.9999999999999999 y el error se arrastra a los totales del mes.
#
# Los precios, cantidades y valoraciones de cartera SIGUEN en Float a propósito:
# son estimaciones marcadas a mercado a partir de floats que vienen de Yahoo,
# CoinGecko y Frankfurter. Ahí Decimal daría una falsa sensación de exactitud
# sobre un dato que ya es aproximado, y contagiaría de conversiones todo el
# cálculo de P&L, TWR y efecto divisa.
Money = Numeric(12, 2)


def utcnow() -> datetime:
    """UTC naive (compatible con las filas ya guardadas); evita datetime.utcnow(), deprecado en 3.12."""
    return datetime.now(UTC).replace(tzinfo=None)


def _by_value(enum_cls):
    """Fuerza a SQLAlchemy a guardar el .value del enum (no el .name) para que la
    base de datos sea legible si se inspecciona directamente."""
    return [e.value for e in enum_cls]


class AssetType(enum.Enum):
    CUENTA = "cuenta_bancaria"
    ACCION = "accion_etf_fondo"
    CRIPTO = "criptomoneda"
    OTRO = "inmueble_otro"


class Currency(enum.Enum):
    """Divisas soportadas: EUR/USD primero (las habituales) y después el resto
    del set de referencia del BCE (lo que cubre Frankfurter, la fuente de FX)."""
    EUR = "EUR"
    USD = "USD"
    AUD = "AUD"
    BGN = "BGN"
    BRL = "BRL"
    CAD = "CAD"
    CHF = "CHF"
    CNY = "CNY"
    CZK = "CZK"
    DKK = "DKK"
    GBP = "GBP"
    HKD = "HKD"
    HUF = "HUF"
    IDR = "IDR"
    ILS = "ILS"
    INR = "INR"
    ISK = "ISK"
    JPY = "JPY"
    KRW = "KRW"
    MXN = "MXN"
    MYR = "MYR"
    NOK = "NOK"
    NZD = "NZD"
    PHP = "PHP"
    PLN = "PLN"
    RON = "RON"
    SEK = "SEK"
    SGD = "SGD"
    THB = "THB"
    TRY = "TRY"
    ZAR = "ZAR"


CURRENCY_CODES = {c.value for c in Currency}


def currency_from_code(code: str | None) -> Currency | None:
    """Currency desde un código tipo "HKD"; None si no está soportada (nunca lanza)."""
    if not code:
        return None
    try:
        return Currency(code.strip().upper())
    except ValueError:
        return None


class TransactionType(enum.Enum):
    GASTO = "gasto"
    INGRESO = "ingreso"


class TransactionStatus(enum.Enum):
    PENDIENTE = "pendiente"
    CONFIRMADO = "confirmado"


class SnapshotSource(enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"


class AccountKind(enum.Enum):
    BROKER = "broker"
    EXCHANGE = "exchange"
    BANCO = "banco"
    OTRO = "otro"


class OperationType(enum.Enum):
    COMPRA = "compra"
    VENTA = "venta"


class Usuario(Base):
    """Una persona de la casa, con su patrimonio y sus gastos aparte.

    La app nació mono-usuario. Al abrirla a varias personas la decisión de fondo
    fue que los datos son de cada una y no compartidos: un tracker de patrimonio
    responde "cuánto tengo", y esa pregunta no tiene sentido sumada entre dos.

    La contraseña es **opcional** a propósito. En casa, obligar a teclearla
    varias veces al día desde el móvil acaba en una contraseña de cuatro letras
    o en la app abierta permanentemente, que es peor que no tenerla. Quien la
    quiera la pone; el aislamiento entre usuarios funciona igual, porque no
    depende de ella.

    Lo que sí protege de fuera es el binding a loopback y `ENABLE_AUTH`: elegir
    usuario NO es un control de acceso contra terceros, es separar los datos de
    quienes ya están dentro de casa.
    """

    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(40), unique=True)
    # None = entra sin contraseña. No se guarda en claro ni cifrada: hash con
    # sal, que es lo único que no permite recuperar la original.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    @property
    def pide_password(self) -> bool:
        return bool(self.password_hash)


class Account(Base):
    """Cuenta/plataforma donde viven activos u operaciones (Trade Republic, OKX, banco...)."""

    __tablename__ = "accounts"

    __table_args__ = (UniqueConstraint("usuario_id", "name", name="uq_accounts_usuario_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[AccountKind] = mapped_column(
        SAEnum(AccountKind, values_callable=_by_value), default=AccountKind.BROKER
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Operation(Base):
    """Compra o venta de un activo invertible. La posición (cantidad, coste medio,
    P&L) se deriva de las operaciones; los activos sin operaciones conservan el
    comportamiento anterior (cantidad manual)."""

    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), index=True)
    asset: Mapped["Asset"] = relationship(back_populates="operations")
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    account: Mapped["Account | None"] = relationship()
    type: Mapped[OperationType] = mapped_column(SAEnum(OperationType, values_callable=_by_value))
    date: Mapped[date] = mapped_column(Date, default=date.today, index=True)  # se filtra/ordena por fecha
    quantity: Mapped[float] = mapped_column(Float)
    unit_price: Mapped[float] = mapped_column(Float)  # en la divisa del activo
    fee: Mapped[float] = mapped_column(Float, default=0.0)  # comisión, en la divisa del activo
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, values_callable=_by_value), default=TransactionStatus.CONFIRMADO
    )
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | csv | voz
    # Huella de deduplicación para importaciones CSV (fecha+ticker+cantidad+precio)
    import_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    asset_type: Mapped[AssetType] = mapped_column(SAEnum(AssetType, values_callable=_by_value))
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, values_callable=_by_value), default=Currency.EUR
    )

    # Para acciones/ETFs/cripto: símbolo (Yahoo Finance) o id (CoinGecko) + cantidad
    ticker: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # ISIN (llega en los CSV de Trade Republic; permite casar importaciones con activos)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Clasificación para allocations (editable; se rellena sola donde se puede:
    # cripto -> Global/Cripto, acciones -> región según el exchange de Yahoo)
    region: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # Divisa de EXPOSICIÓN para activos que cotizan en la base pero cuyo subyacente
    # va en otra divisa (clase "USD (Acc)" de un fondo, cross-listing de Frankfurt):
    # el efecto divisa se descompone contra ella. Vacía = sin exposición aparte.
    exposure_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price_update: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Para cuentas/inmuebles/otros: valor manual directo
    manual_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cierre anterior (acciones: previous_close de Yahoo; cripto: derivado del cambio 24h)
    previous_close: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Precio medio de compra manual (útil para criptos sin ticker CoinGecko o activos
    # importados donde las operaciones no reflejan el coste real)
    avg_cost_override: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Cuenta/plataforma por defecto del activo (informativa; las operaciones llevan la suya)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    account: Mapped["Account | None"] = relationship()

    operations: Mapped[list["Operation"]] = relationship(
        back_populates="asset", cascade="all, delete-orphan", order_by="Operation.date, Operation.id"
    )
    # Con cascade en la relación (y no solo el ON DELETE del esquema): SQLite no
    # aplica claves foráneas salvo que se active el PRAGMA, así que borrar un
    # activo dejaba estas filas apuntando a un id inexistente. Y una alerta
    # huérfana rompía el ciclo de comprobación para TODOS los activos: el
    # AttributeError quedaba enterrado en el except del scheduler y las alertas
    # dejaban de funcionar indefinidamente.
    alertas: Mapped[list["Alerta"]] = relationship(cascade="all, delete-orphan")
    pesos_objetivo: Mapped[list["PesoObjetivo"]] = relationship(cascade="all, delete-orphan")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def quantity_from_operations(self) -> float | None:
        """Cantidad derivada de las operaciones confirmadas; None si no hay ninguna."""
        ops = [o for o in self.operations if o.status == TransactionStatus.CONFIRMADO]
        if not ops:
            return None
        qty = 0.0
        for op in ops:
            qty += op.quantity if op.type == OperationType.COMPRA else -op.quantity
        return qty

    def effective_quantity(self) -> float | None:
        """Cantidad real de la posición: derivada de operaciones si las hay,
        si no la cantidad manual heredada de la v2."""
        derived = self.quantity_from_operations()
        return derived if derived is not None else self.quantity

    def effective_price(self) -> float | None:
        """Precio para valorar la posición: precio de mercado si está disponible;
        si no, el coste medio de la posición abierta. Así un activo recién importado
        (aún sin precio de mercado) cuenta en el patrimonio, valorado a coste, hasta
        que el job de precios le asigne una cotización real."""
        if self.current_price is not None:
            return self.current_price
        if self.asset_type in (AssetType.ACCION, AssetType.CRIPTO):
            from .services.portfolio import compute_position  # perezoso: evita ciclo de import
            return compute_position(self.operations).avg_cost
        return None

    def current_value(self) -> float:
        """Valor actual del activo, en su propia moneda (currency)."""
        if self.asset_type in (AssetType.ACCION, AssetType.CRIPTO):
            qty = self.effective_quantity()
            price = self.effective_price()
            if qty is not None and price is not None:
                return price * qty
            return 0.0
        return self.manual_value or 0.0


class Category(Base):
    __tablename__ = "categories"

    __table_args__ = (UniqueConstraint("usuario_id", "name", name="uq_categories_usuario_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(60))
    # Palabras clave separadas por coma, usadas para auto-categorizar CSV/voz
    keywords: Mapped[str] = mapped_column(Text, default="")
    budget_limit: Mapped[Decimal | None] = mapped_column(Money, nullable=True)  # None = sin límite


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date, default=date.today, index=True)  # se filtra/ordena por fecha
    type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType, values_callable=_by_value))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    category: Mapped["Category | None"] = relationship()
    amount: Mapped[Decimal] = mapped_column(Money)  # siempre expresado en base_currency (EUR)
    description: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[TransactionStatus] = mapped_column(
        SAEnum(TransactionStatus, values_callable=_by_value), default=TransactionStatus.CONFIRMADO
    )
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | voz | csv | recurrente
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class RecurringTransaction(Base):
    """Regla de gasto/ingreso recurrente (alquiler, suscripciones, nómina...).
    La periodicidad la marca `interval_months` (1=mensual, 2, 3=trimestral,
    6=semestral, 12=anual). Un job diario + un catch-up al arrancar generan las
    transacciones que toquen, incluso si el servidor estuvo apagado el día del cargo."""

    __tablename__ = "recurring_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[TransactionType] = mapped_column(
        SAEnum(TransactionType, values_callable=_by_value), default=TransactionType.GASTO
    )
    amount: Mapped[Decimal] = mapped_column(Money)  # en `currency`; se convierte a EUR al generar
    # Divisa del importe; si es USD se convierte a EUR (base) al generar la transacción
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, values_callable=_by_value), default=Currency.EUR
    )
    category_id: Mapped[int | None] = mapped_column(ForeignKey("categories.id"), nullable=True)
    category: Mapped["Category | None"] = relationship()
    # Cada cuántos meses se repite: 1 mensual, 2, 3 trimestral, 6 semestral, 12 anual
    interval_months: Mapped[int] = mapped_column(Integer, default=1)
    # Día del mes en que se genera (se ajusta al último día en meses cortos: 31 -> 28/30)
    day_of_month: Mapped[int] = mapped_column(Integer, default=1)
    # Primera fecha desde la que aplica; si es pasada, el catch-up genera el histórico
    start_date: Mapped[date] = mapped_column(Date, default=date.today)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Fecha de la última ocurrencia ya generada (control del catch-up)
    last_generated: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class NetWorthSnapshot(Base):
    __tablename__ = "net_worth_snapshots"

    __table_args__ = (UniqueConstraint("usuario_id", "date", name="uq_snapshots_usuario_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    total_value: Mapped[float] = mapped_column(Float)  # en base_currency (EUR)
    # Parte manual (cuentas/inmuebles) del total; la invertida se reconstruye
    # desde operaciones + histórico de precios. Nullable: snapshots antiguos no la traen.
    manual_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[SnapshotSource] = mapped_column(
        SAEnum(SnapshotSource, values_callable=_by_value), default=SnapshotSource.AUTO
    )


class NetWorthIntraday(Base):
    """Muestras intradía del patrimonio para la curva 1D del dashboard.
    Retención corta (~48 h): el propio job de muestreo purga las viejas."""
    __tablename__ = "net_worth_intraday"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC
    total_value: Mapped[float] = mapped_column(Float)      # en base_currency
    invested_value: Mapped[float] = mapped_column(Float)   # parte invertida (acciones/cripto)


class PriceHistory(Base):
    """Cierre diario cacheado por símbolo: ticker Yahoo, id CoinGecko, par FX
    ("FX:USD:EUR") o benchmark ("^GSPC"). Rellenado por el job diario."""

    __tablename__ = "price_history"
    __table_args__ = (UniqueConstraint("symbol", "date", name="uq_price_history_symbol_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[date] = mapped_column(Date)
    price: Mapped[float] = mapped_column(Float)


class Benchmark(Base):
    """Índice de referencia contra el que compararse (símbolo de Yahoo).

    Antes eran dos constantes en el código. Al pasarlos a tabla el usuario puede
    seguir el índice que le toque —su plan de pensiones, el IBEX, un ETF
    concreto— sin tocar el código. `clave` es el identificador estable que usan
    el selector del dashboard y la tabla anual; se deriva del símbolo."""

    __tablename__ = "benchmarks"

    __table_args__ = (UniqueConstraint("usuario_id", "clave", name="uq_benchmarks_usuario_clave"), UniqueConstraint("usuario_id", "symbol", name="uq_benchmarks_usuario_symbol"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    # Los índices de comparación son una preferencia, no un dato de mercado.
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    clave: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(60))
    symbol: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Watchlist(Base):
    """Valor en seguimiento: se mira el precio pero no se tiene posición.

    Tabla aparte y no un Asset con bandera: los activos entran en el patrimonio,
    en las allocations, en el X-Ray y en la reconstrucción del histórico, así que
    una bandera obligaría a acordarse de excluirlos en cada una de esas consultas
    y cualquier olvido inflaría el patrimonio con dinero que no tienes."""

    __tablename__ = "watchlist"

    __table_args__ = (UniqueConstraint("usuario_id", "ticker", name="uq_watchlist_usuario_ticker"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    asset_type: Mapped[AssetType] = mapped_column(SAEnum(AssetType, values_callable=_by_value))
    currency: Mapped[Currency] = mapped_column(
        SAEnum(Currency, values_callable=_by_value), default=Currency.EUR
    )
    current_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    previous_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price_update: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def day_change_pct(self) -> float | None:
        """Variación del día (%). Sin posición no hay P&L que enseñar: esto es
        lo único que se puede decir de un valor que solo se vigila."""
        if self.current_price is None or not self.previous_close:
            return None
        return 100.0 * (self.current_price - self.previous_close) / self.previous_close


class TipoAlerta(enum.Enum):
    """Qué condición vigila la regla."""
    POR_ENCIMA = "por_encima"     # el precio sube por encima del objetivo
    POR_DEBAJO = "por_debajo"     # el precio baja por debajo del objetivo
    CAIDA_DIARIA = "caida_diaria"  # el activo cae hoy más de X %


class Alerta(Base):
    """Aviso por Telegram cuando un activo cumple una condición de precio.

    Se comprueban en el job que ya refresca precios: es el único momento en que
    hay cotización nueva, así que mirarlas en cualquier otro sitio solo repetiría
    trabajo sobre datos que no han cambiado."""

    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id", ondelete="CASCADE"), index=True)
    asset: Mapped["Asset"] = relationship(overlaps="alertas")
    tipo: Mapped[TipoAlerta] = mapped_column(SAEnum(TipoAlerta, values_callable=_by_value))
    # Precio objetivo (en la divisa del activo) o porcentaje de caída, según el tipo
    valor: Mapped[float] = mapped_column(Float)
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    # Última vez que saltó: evita repetir el mismo aviso en cada refresco de
    # precios mientras la condición se mantenga (un activo que cruza a la baja
    # sigue por debajo horas). Se rearma al volver a cruzar en sentido contrario.
    ultimo_disparo: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PesoObjetivo(Base):
    """Peso que quieres que tenga un activo sobre la cartera invertida.

    Se guarda por activo y no por clase (región, sector...) porque es lo único
    que el usuario controla al comprar: se compra un ETF concreto, no "un 20% de
    emergentes". Los pesos no tienen por qué sumar 100: lo que no esté asignado
    se trata como "sin objetivo" y se informa aparte."""

    __tablename__ = "pesos_objetivo"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), index=True
    )
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("assets.id", ondelete="CASCADE"), unique=True, index=True
    )
    asset: Mapped["Asset"] = relationship(overlaps="pesos_objetivo")
    # % objetivo sobre el total invertido
    porcentaje: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
