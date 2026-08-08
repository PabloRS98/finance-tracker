"""Formato del texto que va a Telegram.

Vive aparte porque lo necesitan dos módulos que no se pueden importar entre sí:
`telegram_bot` (que ya lo tenía, con el nombre `_esc`) y `alertas`, que no lo
usaba en ninguna de sus tres ramas y por eso perdía avisos."""
import html


def escapar(valor: str) -> str:
    """Escapa texto para el HTML de Telegram.

    `quote=False` a propósito: Telegram solo decodifica &lt; &gt; &amp; y &quot;,
    no las entidades numéricas, así que escapar el apóstrofo dejaba
    "Delaney&#x27;s Corporation" tal cual en el mensaje."""
    return html.escape(valor, quote=False)
