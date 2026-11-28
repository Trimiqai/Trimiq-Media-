"""Neural-only Edge-TTS voice discovery and synthesis."""

import asyncio
import logging
import re
import threading
import time
from pathlib import Path

import config
from modules.file_store import new_id

LOGGER = logging.getLogger(__name__)
VOICE_CACHE_TTL_SECONDS = 6 * 60 * 60
VOICE_CATALOG_TIMEOUT_SECONDS = 30
SYNTHESIS_TIMEOUT_SECONDS = 180
_VOICE_CACHE: list[dict] | None = None
_VOICE_CACHE_TIME = 0.0
_VOICE_CACHE_LOCK = threading.Lock()


async def _fetch_edge_voices() -> list[dict]:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Edge-TTS is not installed. Install the optional edge-tts package.") from exc
    return await asyncio.wait_for(
        edge_tts.list_voices(),
        timeout=VOICE_CATALOG_TIMEOUT_SECONDS,
    )


def _is_neural_voice(voice: dict) -> bool:
    if str(voice.get("Status", "")).strip().casefold() == "deprecated":
        return False
    voice_type = voice.get("VoiceType") or voice.get("VoiceTypeName")
    if voice_type:
        return str(voice_type).strip().casefold() == "neural"
    short_name = str(voice.get("ShortName", ""))
    name = str(voice.get("Name", ""))
    return short_name.casefold().endswith("neural") or bool(re.search(r"\bneural\b", name, re.I))


def _neural_voices(voices: list[dict]) -> list[dict]:
    return [
        voice for voice in voices
        if _is_neural_voice(voice)
        and voice.get("ShortName")
        and voice.get("Locale")
    ]


def list_neural_voices(force_refresh: bool = False) -> dict:
    global _VOICE_CACHE, _VOICE_CACHE_TIME
    with _VOICE_CACHE_LOCK:
        cache_fresh = (
            _VOICE_CACHE is not None
            and time.monotonic() - _VOICE_CACHE_TIME < VOICE_CACHE_TTL_SECONDS
        )
        stale = False
        if force_refresh or not cache_fresh:
            try:
                _VOICE_CACHE = asyncio.run(_fetch_edge_voices())
                _VOICE_CACHE_TIME = time.monotonic()
            except Exception as exc:
                if _VOICE_CACHE is None:
                    raise RuntimeError(f"Could not retrieve the Edge-TTS voice list: {exc}") from exc
                stale = True
                LOGGER.warning("Could not refresh the cached Edge-TTS voice list: %s", exc)

        voices = _neural_voices(_VOICE_CACHE or [])

    locale_names = sorted({str(voice["Locale"]) for voice in voices}, key=str.casefold)
    items = [{
        "short_name": str(voice["ShortName"]),
        "display_name": str(voice.get("FriendlyName") or voice.get("Name") or voice["ShortName"]),
        "locale": str(voice["Locale"]),
        "language": str(voice["Locale"]).split("-", 1)[0].lower(),
        "gender": str(voice.get("Gender", "")),
        "status": str(voice.get("Status", "")),
    } for voice in voices]
    return {
        "voices": items,
        "languages": [{"locale": locale, "language": locale.split("-", 1)[0].lower()} for locale in locale_names],
        "stale": stale,
        "message": (
            "Using a cached voice list because Edge-TTS could not be reached."
            if stale else
            f"{len(items)} Neural voice(s) available." if items else
            "Edge-TTS returned no verified Neural voices."
        ),
    }


def cached_voice_labels() -> dict[str, str]:
    with _VOICE_CACHE_LOCK:
        voices = _neural_voices(_VOICE_CACHE or [])
    return {
        str(voice["ShortName"]): str(voice.get("FriendlyName") or voice["ShortName"])
        for voice in voices
    }


def _base_language(locale: str) -> str:
    return locale.split("-", 1)[0].casefold()


def _automatic_language(text: str, available_languages: set[str]) -> str:
    if any("\u0900" <= char <= "\u097f" for char in text):
        return "hi"
    if any("\u0600" <= char <= "\u06ff" for char in text):
        if "ur" in available_languages:
            return "ur"
        if "ar" in available_languages:
            return "ar"
        return "ur"
    return "en" if "en" in available_languages else sorted(available_languages)[0]


def _voice_candidates(voices: list[dict], preferred_voice: str,
                      language: str, text: str) -> list[dict]:
    if not voices:
        raise RuntimeError("Edge-TTS has no verified Neural voices available.")
    by_name = {voice["short_name"]: voice for voice in voices}
    preferred = by_name.get(preferred_voice)
    available_languages = {voice["language"] for voice in voices}

    if language == "auto":
        if preferred:
            target_language = preferred["language"]
            target_locale = preferred["locale"]
        elif preferred_voice and preferred_voice != "auto":
            parts = preferred_voice.split("-")
            target_locale = "-".join(parts[:2]) if len(parts) >= 3 else ""
            target_language = parts[0].lower() if parts else _automatic_language(text, available_languages)
        else:
            target_language = _automatic_language(text, available_languages)
            target_locale = ""
    else:
        target_locale = language
        target_language = _base_language(language)
        if preferred and preferred["language"] != target_language:
            raise ValueError("The selected Neural voice does not match the chosen language.")

    candidates = [
        voice for voice in voices
        if (voice["locale"] == target_locale if target_locale else voice["language"] == target_language)
    ]
    if not candidates and target_locale:
        candidates = [voice for voice in voices if voice["language"] == target_language]
    if not candidates:
        requested = target_locale or target_language
        raise RuntimeError(f"No verified Neural voice is available for language/locale '{requested}'.")
    if preferred:
        candidates = [preferred, *(voice for voice in candidates if voice["short_name"] != preferred_voice)]
    return candidates


def _voice_unavailable(error: Exception) -> bool:
    message = str(error).casefold()
    return any(marker in message for marker in (
        "voice not found", "voice is not available", "unsupported voice",
        "invalid voice", "no audio was received",
    ))


def _is_mp3(path: Path) -> bool:
    try:
        with path.open("rb") as audio_file:
            header = audio_file.read(3)
    except OSError:
        return False
    return header.startswith(b"ID3") or (
        len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
    )


def synthesize(context, text: str, voice: str = "auto", rate: str = "+0%",
               pitch: str = "+0Hz", language: str = "auto",
               volume: str = "+0%") -> dict:
    if not text.strip() or len(text) > 5000:
        raise ValueError("Text-to-speech requires 1-5000 characters.")
    if rate not in {f"{value:+d}%" for value in range(-50, 51, 5)}:
        raise ValueError("Rate must be between -50% and +50% in 5% steps.")
    if volume not in {f"{value:+d}%" for value in range(-50, 51, 5)}:
        raise ValueError("Volume must be between -50% and +50% in 5% steps.")
    if pitch not in {f"{value:+d}Hz" for value in range(-50, 51, 5)}:
        raise ValueError("Pitch must be between -50Hz and +50Hz in 5Hz steps.")

    catalog = list_neural_voices()
    voices = catalog["voices"]
    candidates = _voice_candidates(voices, voice, language, text)
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Edge-TTS is not installed. Install the optional edge-tts package.") from exc

    output_id = new_id()
    output_path = config.OUTPUT_DIR / f"{output_id}.mp3"
    temporary_path = config.TEMP_DIR / f"{output_id}.mp3"
    context.progress(10, "Connecting to Edge-TTS (internet required)")

    async def create(selected_voice: str):
        communicate = edge_tts.Communicate(
            text,
            selected_voice,
            rate=rate,
            pitch=pitch,
            volume=volume,
        )
        await asyncio.wait_for(
            communicate.save(str(temporary_path)),
            timeout=SYNTHESIS_TIMEOUT_SECONDS,
        )

    selected = None
    last_error = None
    try:
        for index, candidate in enumerate(candidates):
            temporary_path.unlink(missing_ok=True)
            try:
                awaitable = create(candidate["short_name"])
                asyncio.run(awaitable)
                if not temporary_path.is_file() or temporary_path.stat().st_size == 0 or not _is_mp3(temporary_path):
                    raise RuntimeError("Edge-TTS did not produce a valid MP3 audio file.")
                selected = candidate
                break
            except Exception as exc:
                last_error = exc
                can_fallback = index + 1 < len(candidates) and _voice_unavailable(exc)
                if not can_fallback:
                    raise
                LOGGER.warning(
                    "Preferred Edge-TTS Neural voice %s was unavailable; trying %s.",
                    candidate["short_name"],
                    candidates[index + 1]["short_name"],
                )

        if selected is None:
            raise RuntimeError(f"No verified Neural voice could synthesize the text: {last_error}")
        temporary_path.replace(output_path)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"Edge-TTS failed: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)

    return {
        "file_id": output_id,
        "filename": output_path.name,
        "format": "mp3",
        "voice": selected["short_name"],
        "voice_name": selected["display_name"],
        "locale": selected["locale"],
        "voice_fallback": selected["short_name"] != voice and voice != "auto",
    }
