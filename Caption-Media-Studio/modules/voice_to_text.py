"""Browser recordings and uploaded audio use the caption engine."""

from modules.caption_engine import transcribe


def transcribe_voice(context, audio_path: str, language: str | None = None,
                     model_name: str | None = None, correct: bool = True) -> dict:
    return transcribe(
        context, audio_path, language=language, model_name=model_name,
        correct=correct, word_timestamps=True,
    )
