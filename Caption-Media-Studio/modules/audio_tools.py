"""Audio extraction, conversion and trimming."""

from pathlib import Path
import math

import config
from modules.ffmpeg_tools import media_info, run_ffmpeg
from modules.file_store import new_id

AUDIO_CODECS = {
    "mp3": ("libmp3lame", ["-b:a", "192k"]),
    "wav": ("pcm_s16le", []),
    "m4a": ("aac", ["-b:a", "192k"]),
    "aac": ("aac", ["-b:a", "192k"]),
    "opus": ("libopus", ["-b:a", "128k"]),
    "flac": ("flac", []),
}
COPY_CODECS = {
    "mp3": {"mp3"},
    "wav": {"pcm_s16le"},
    "m4a": {"aac"},
    "aac": {"aac"},
    "opus": {"opus"},
    "flac": {"flac"},
}


def _can_copy_audio(info: dict, output_format: str, bitrate: str) -> bool:
    stream = next((item for item in info["streams"] if item.get("type") == "audio"), None)
    if not stream or stream.get("codec") not in COPY_CODECS[output_format]:
        return False
    if output_format in {"wav", "flac"}:
        return True
    return stream.get("bit_rate") == int(bitrate[:-1]) * 1000


def extract_audio(context, input_path: str, output_format: str = "mp3",
                  bitrate: str = "192k") -> dict:
    output_format = output_format.lower()
    if output_format not in AUDIO_CODECS:
        raise ValueError("Choose MP3, WAV, M4A, AAC, Opus, or FLAC.")
    codec, options = AUDIO_CODECS[output_format]
    if output_format in {"mp3", "m4a", "aac", "opus"}:
        if bitrate not in {"96k", "128k", "160k", "192k", "256k", "320k"}:
            raise ValueError("Unsupported audio bitrate.")
        options = ["-b:a", bitrate]
    info = media_info(input_path)
    if not info["audio"]:
        raise ValueError("The selected media file does not contain an audio stream.")
    copy_stream = _can_copy_audio(info, output_format, bitrate)
    output_id = new_id()
    output_path = config.OUTPUT_DIR / f"{output_id}.{output_format}"
    temporary_path = config.TEMP_DIR / f"{output_id}.{output_format}"
    context.progress(15, "Extracting audio")
    try:
        if copy_stream:
            run_ffmpeg(["-i", input_path, "-vn", "-c:a", "copy", str(temporary_path)])
        else:
            run_ffmpeg(["-i", input_path, "-vn", "-c:a", codec, *options, str(temporary_path)])
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce a valid audio file.")
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"file_id": output_id, "filename": output_path.name, "format": output_format}


def convert_audio(context, input_path: str, output_format: str = "mp3",
                  bitrate: str = "192k") -> dict:
    return extract_audio(context, input_path, output_format, bitrate)


def trim_audio(context, input_path: str, start: float, end: float,
               output_format: str | None = None) -> dict:
    start, end = float(start), float(end)
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end - start > 24 * 60 * 60:
        raise ValueError("Trim end must be after start, within 24 hours.")
    info = media_info(input_path)
    if info["duration"] and end > info["duration"] + 0.1:
        raise ValueError(f"Trim end exceeds media duration ({info['duration']:.2f} seconds).")
    source_extension = Path(input_path).suffix.lstrip(".").lower()
    extension = (output_format or (source_extension if source_extension in AUDIO_CODECS else "mp3")).lower()
    if extension not in AUDIO_CODECS:
        raise ValueError("Unsupported output format.")
    output_id = new_id()
    output_path = config.OUTPUT_DIR / f"{output_id}.{extension}"
    temporary_path = config.TEMP_DIR / f"{output_id}.{extension}"
    codec, options = AUDIO_CODECS[extension]
    context.progress(20, "Trimming audio")
    try:
        run_ffmpeg([
            "-ss", f"{start:.3f}", "-i", input_path, "-t", f"{end-start:.3f}",
            "-vn", "-c:a", codec, *options, str(temporary_path),
        ])
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce a valid trimmed file.")
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return {"file_id": output_id, "filename": output_path.name, "format": extension}
