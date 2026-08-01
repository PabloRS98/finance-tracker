"""Configuración centralizada vía variables de entorno (.env)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # General
    app_name: str = "Tracker de Patrimonio"
    timezone: str = "UTC"

    # Autenticación HTTP Basic opcional (recomendado activar si se expone vía VPN)
    enable_auth: bool = False
    auth_username: str = "admin"
    auth_password: str = "changeme"

    # Base de datos SQLite
    db_path: str = "/data/finance.db"

    # Moneda base para totales consolidados (patrimonio, gastos, ingresos)
    base_currency: str = "EUR"

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


settings = Settings()
