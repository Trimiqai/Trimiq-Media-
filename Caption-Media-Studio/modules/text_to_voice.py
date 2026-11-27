"""Optional Edge-TTS integration."""

import asyncio

import config
from modules.file_store import new_id

SUPPORTED_VOICES = {
    "hi-IN-SwaraNeural": "Hindi - Swara",
    "hi-IN-MadhurNeural": "Hindi - Madhur",
    "en-US-AriaNeural": "English - Aria",
    "en-US-GuyNeural": "English - Guy",
    "ur-PK-UzmaNeural": "Urdu - Uzma",
    "ur-PK-AsadNeural": "Urdu - Asad",
}


def synthesize(context, text: str, voice: str, rate: str = "+0%",
               pitch: str = "+0Hz") -> dict:
    if not text.strip() or len(text) > 5000:
        raise ValueError("Text-to-speech requires 1-5000 characters.")
    if voice not in SUPPORTED_VOICES:
        raise ValueError("Choose one of the supported Hindi, English, or Urdu voices.")
    if rate not in {f"{value:+d}%" for value in range(-50, 51, 5)}:
        raise ValueError("Rate must be between -50% and +50% in 5% steps.")
    if pitch not in {f"{value:+d}Hz" for value in range(-50, 51, 5)}:
        raise ValueError("Pitch must be between -50Hz and +50Hz in 5Hz steps.")
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("Edge-TTS is not installed. Install the optional edge-tts package.") from exc

    output_id = new_id()
    output_path = config.OUTPUT_DIR / f"{output_id}.mp3"
    temporary_path = config.TEMP_DIR / f"{output_id}.mp3"
    context.progress(10, "Connecting to Edge-TTS (internet required)")

    async def create():
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        await communicate.save(str(temporary_path))

    try:
        asyncio.run(create())
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("Edge-TTS did not produce an audio file.")
        temporary_path.replace(output_path)
    except Exception as exc:
        output_path.unlink(missing_ok=True)
        temporary_path.unlink(missing_ok=True)
        raise RuntimeError(f"Edge-TTS failed: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"file_id": output_id, "filename": output_path.name, "format": "mp3"}
