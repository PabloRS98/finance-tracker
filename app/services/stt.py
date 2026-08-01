"""Transcripción local de notas de voz con faster-whisper (sin APIs, sin coste).

El modelo (FINANCE_WHISPER_MODEL, por defecto "small") se descarga una única vez
a /data/whisper (persiste en el volumen entre rebuilds) y se carga perezosamente
en la primera nota de voz: el arranque de la app no lo paga."""
import logging
import os
import tempfile
import threading

from ..config import settings

logger = logging.getLogger(__name__)

_model = None
_lock = threading.Lock()  # una sola carga aunque lleguen dos notas a la vez


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel  # import pesado: solo si se usa voz

            download_root = os.path.join(os.path.dirname(settings.db_path), "whisper")
            logger.info("Cargando modelo Whisper %r (primera vez descarga a %s)",
                        settings.whisper_model, download_root)
            _model = WhisperModel(
                settings.whisper_model, device="cpu", compute_type="int8",
                download_root=download_root,
            )
        return _model


def transcribe(audio: bytes) -> str | None:
    """Texto de una nota de voz (OGG/Opus de Telegram; PyAV decodifica solo).
    None si falla la carga del modelo o la transcripción."""
    try:
        model = _get_model()
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp.write(audio)
            path = tmp.name
        try:
            segments, _info = model.transcribe(path, language="es", vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
            return text or None
        finally:
            os.unlink(path)
    except Exception:
        logger.exception("Fallo al transcribir la nota de voz")
        return None
