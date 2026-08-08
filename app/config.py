"""Configuración centralizada vía variables de entorno (.env)."""
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PASSWORD = "changeme"
MIN_PASSWORD_LENGTH = 8

# Absoluta, por el mismo motivo que las plantillas y los estáticos, pero este
# caso es peor: no revienta. Con una ruta relativa el .env solo se lee si el
# proceso arranca desde la raíz del repo, y si no, la app se levanta en silencio
# con toda la configuración por defecto — es decir, sin autenticación.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # General
    app_name: str = "Tracker de Patrimonio"
    timezone: str = "UTC"

    # Autenticación HTTP Basic opcional (recomendado activar si se expone vía VPN)
    enable_auth: bool = False
    auth_username: str = "admin"
    auth_password: str = DEFAULT_PASSWORD

    # Base de datos SQLite
    db_path: str = "/data/finance.db"

    # Moneda base para totales consolidados (patrimonio, gastos, ingresos)
    base_currency: str = "EUR"

    # Cuenta las sentencias SQL de cada petición y las publica en la cabecera
    # X-Consultas-SQL. Solo para diagnosticar: apagado no cuesta nada, porque
    # ni siquiera se registra el listener de SQLAlchemy.
    debug_sql: bool = False

    # Frecuencia de actualización de precios de mercado (acciones/cripto)
    price_refresh_minutes: int = 60

    # Muestreo intradía del patrimonio (curva 1D del dashboard) y su retención.
    # Nota: la curva solo cambia cuando cambian los precios (price_refresh_minutes).
    intraday_sample_minutes: int = 15
    intraday_retention_hours: int = 48

    # Nº de backups diarios de la BD que se conservan en /data/backups
    backup_keep: int = 14

    # Presupuestos por categoría (activable/desactivable globalmente)
    budgets_enabled: bool = True

    # Umbrales del análisis X-Ray de la cartera invertida
    xray_max_asset_pct: float = 25.0       # % máximo de un activo sobre lo invertido
    xray_max_currency_pct: float = 50.0    # % máximo en divisas distintas de la base
    xray_stale_price_days: int = 7         # días sin precio de mercado para avisar
    xray_stale_manual_days: int = 120      # días sin revisar un valor manual para avisar

    # Bot de Telegram (opcional): resumen diario y registro de operaciones/gastos
    # por texto o nota de voz. Sin token el bot no arranca; sin chat_id responde
    # a cualquier mensaje con el chat_id para poder configurarlo.
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_summary_hour: int = 9         # hora local del resumen diario
    telegram_summary_minute: int = 0
    whisper_model: str = "small"           # tiny/base/small/medium (calidad vs CPU)

    @model_validator(mode="after")
    def _reject_insecure_password(self) -> "Settings":
        """Con la autenticación activada, no arrancar con la contraseña de fábrica.

        Un fallo al arrancar es ruidoso y se corrige en un minuto; una app
        expuesta con admin/changeme puede pasar meses sin que nadie lo note.

        Solo se comprueba con `enable_auth` activo: aplicarlo siempre dejaría
        sin levantar a cualquier instalación existente, que arranca sin
        autenticación y con el valor por defecto.
        """
        if not self.enable_auth:
            return self
        if self.auth_password == DEFAULT_PASSWORD:
            raise ValueError(
                "ENABLE_AUTH está activado pero AUTH_PASSWORD sigue siendo el valor "
                "de fábrica. Cámbialo en el .env antes de exponer la aplicación."
            )
        if len(self.auth_password) < MIN_PASSWORD_LENGTH:
            raise ValueError(
                "AUTH_PASSWORD debe tener al menos %d caracteres." % MIN_PASSWORD_LENGTH
            )
        return self


settings = Settings()
