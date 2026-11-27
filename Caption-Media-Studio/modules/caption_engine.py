"""Lazy Faster-Whisper integration."""

import gc
import json
import logging
from pathlib import Path

import config
from modules import correction_engine
from modules.file_store import new_id
from modules.memory_guard import ensure_model_memory

LOGGER = logging.getLogger(__name__)
_MODEL = None
_MODEL_KEY = None


def transcribe(context, media_path: str, language: str | None = None,
               model_name: str | None = None, correct: bool = True,
               word_timestamps: bool = True) -> dict:
    global _MODEL, _MODEL_KEY
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Faster-Whisper is not installed or is incompatible with this Python/Termux build. "
            "Install a compatible faster-whisper/CTranslate2 build before transcribing."
        ) from exc

    model_name = model_name or config.WHISPER_MODEL
    ensure_model_memory(model_name)
    model_key = (model_name, config.WHISPER_DEVICE, config.WHISPER_COMPUTE_TYPE)
    if _MODEL_KEY != model_key:
        _MODEL = None
        gc.collect()
        try:
            import ctranslate2
            supported_types = ctranslate2.get_supported_compute_types(config.WHISPER_DEVICE)
            if config.WHISPER_COMPUTE_TYPE not in supported_types:
                available_types = ", ".join(sorted(supported_types)) or "none reported"
                raise RuntimeError(
                    f"Compute type '{config.WHISPER_COMPUTE_TYPE}' is not supported on "
                    f"{config.WHISPER_DEVICE}. Available types: {available_types}."
                )
            _MODEL = WhisperModel(
                model_name,
                device=config.WHISPER_DEVICE,
                compute_type=config.WHISPER_COMPUTE_TYPE,
                cpu_threads=config.WHISPER_CPU_THREADS,
                num_workers=1,
            )
        except Exception as exc:
            raise RuntimeError(
                f"Could not load Whisper model '{model_name}' with compute type "
                f"'{config.WHISPER_COMPUTE_TYPE}': {exc}"
            ) from exc
        _MODEL_KEY = model_key

    context.progress(10, "Model ready; analyzing media")
    try:
        segments_iter, info = _MODEL.transcribe(
            str(media_path),
            language=language or None,
            beam_size=1,
            vad_filter=True,
            word_timestamps=bool(word_timestamps),
        )
        segments = []
        for segment in segments_iter:
            words = []
            if word_timestamps and segment.words:
                words = [
                    {"start": float(word.start), "end": float(word.end), "word": word.word}
                    for word in segment.words
                ]
            segments.append({
                "start": float(segment.start),
                "end": float(segment.end),
                "text": segment.text.strip(),
                "original_text": segment.text.strip(),
                "words": words,
            })
            context.progress(min(90, 10 + len(segments)), f"Recognized {len(segments)} segments")
    except Exception as exc:
        raise RuntimeError(f"Speech recognition failed: {exc}") from exc

    if correct:
        segments = correction_engine.correct_segments(segments)
    transcript_id = new_id()
    transcript = {
        "id": transcript_id,
        "language": getattr(info, "language", language or "auto"),
        "language_probability": getattr(info, "language_probability", None),
        "duration": getattr(info, "duration", None),
        "model": model_name,
        "segments": segments,
    }
    transcript_path = config.OUTPUT_DIR / f"{transcript_id}.json"
    transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
    context.progress(95, "Saved transcript")
    return transcript
