"""Whisper.cpp Tiny model management and CLI transcription."""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import config
from modules import correction_engine
from modules.ffmpeg_tools import run_ffmpeg
from modules.file_store import new_id

LOGGER = logging.getLogger(__name__)

TINY_MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.bin"
TINY_MODEL_SHA1 = "bd577a113a864445d4c299885e0cb97d4ba92b5f"
TINY_MODEL_MIN_BYTES = 70 * 1024 * 1024
TINY_MODEL_MAX_BYTES = 100 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
MODEL_PATH = config.DATA_DIR / "models" / "ggml-tiny.bin"


def _find_cli() -> str | None:
    configured = os.environ.get("CMS_WHISPER_CPP_BIN", "").strip()
    candidates = [configured] if configured else []
    candidates.extend(("whisper-cli", "main"))
    for candidate in candidates:
        executable = shutil.which(candidate)
        if executable:
            return executable
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    return None


def _model_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as model_file:
        for chunk in iter(lambda: model_file.read(DOWNLOAD_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _model_is_valid(path: Path | None = None) -> bool:
    path = path or MODEL_PATH
    try:
        if not path.is_file() or not TINY_MODEL_MIN_BYTES <= path.stat().st_size <= TINY_MODEL_MAX_BYTES:
            return False
        with path.open("rb") as model_file:
            if model_file.read(4) != b"lmgg":
                return False
        return _model_sha1(path) == TINY_MODEL_SHA1
    except OSError:
        return False


def model_status() -> dict:
    executable = _find_cli()
    model_exists = MODEL_PATH.is_file()
    model_valid = _model_is_valid() if model_exists else False
    if not executable:
        message = "whisper-cli is not installed or is not on PATH."
    elif model_valid:
        message = "Whisper.cpp Tiny model is downloaded and verified."
    elif model_exists:
        message = "The Tiny model file failed verification; download it again."
    else:
        message = "Whisper.cpp Tiny model is not downloaded."
    return {
        "cli_available": executable is not None,
        "model_exists": model_exists,
        "model_valid": model_valid,
        "model_size_bytes": MODEL_PATH.stat().st_size if model_exists else 0,
        "model_filename": MODEL_PATH.name,
        "message": message,
    }


def download_tiny_model(context) -> dict:
    if _model_is_valid():
        return {
            "filename": MODEL_PATH.name,
            "size": MODEL_PATH.stat().st_size,
            "already_present": True,
        }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MODEL_PATH.with_name(f"{MODEL_PATH.name}.{new_id()}.part")
    digest = hashlib.sha1()
    received = 0
    context.progress(5, "Connecting to the official Whisper.cpp model host")
    request = urllib.request.Request(
        TINY_MODEL_URL,
        headers={"User-Agent": "Caption-Media-Studio/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            content_length = response.headers.get("Content-Length")
            expected_length = int(content_length) if content_length and content_length.isdigit() else None
            if expected_length is not None and not TINY_MODEL_MIN_BYTES <= expected_length <= TINY_MODEL_MAX_BYTES:
                raise RuntimeError("The model host returned an unexpected file size.")
            with temporary_path.open("wb") as model_file:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > TINY_MODEL_MAX_BYTES:
                        raise RuntimeError("The model download exceeded the 100 MB safety limit.")
                    model_file.write(chunk)
                    digest.update(chunk)
                    if expected_length:
                        progress = min(85, 10 + int(received * 75 / expected_length))
                        context.progress(progress, f"Downloading Whisper.cpp Tiny ({received // (1024 * 1024)} MB)")

        if received < TINY_MODEL_MIN_BYTES:
            raise RuntimeError("The downloaded model is incomplete or too small.")
        if expected_length is not None and received != expected_length:
            raise RuntimeError("The downloaded model size does not match the server response.")
        with temporary_path.open("rb") as model_file:
            if model_file.read(4) != b"lmgg":
                raise RuntimeError("The downloaded file is not a Whisper.cpp GGML model.")
        if digest.hexdigest() != TINY_MODEL_SHA1:
            raise RuntimeError("The downloaded Tiny model checksum does not match the official checksum.")

        temporary_path.replace(MODEL_PATH)
        context.progress(95, "Whisper.cpp Tiny model verified and saved")
        return {
            "filename": MODEL_PATH.name,
            "size": received,
            "already_present": False,
        }
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not download the Whisper.cpp Tiny model: {exc}") from exc
    finally:
        temporary_path.unlink(missing_ok=True)


def _timestamp_seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value) / 1000
    parts = str(value).split(":")
    if len(parts) != 3:
        raise ValueError("Whisper.cpp returned an invalid timestamp.")
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_transcription(data: dict) -> tuple[list[dict], str]:
    transcription = data.get("transcription")
    if not isinstance(transcription, list):
        raise ValueError("Whisper.cpp returned JSON without a transcription list.")
    segments = []
    for item in transcription:
        if not isinstance(item, dict):
            continue
        timestamps = item.get("offsets") or item.get("timestamps") or {}
        start_value = timestamps.get("from", 0)
        end_value = timestamps.get("to", start_value)
        start = _timestamp_seconds(start_value)
        end = _timestamp_seconds(end_value)
        text = str(item.get("text", "")).strip()
        if text and start >= 0 and end >= start:
            segments.append({
                "start": start,
                "end": end,
                "text": text,
                "original_text": text,
                "words": [],
            })
    language = str(data.get("result", {}).get("language", "auto"))
    return segments, language


def transcribe_tiny(context, media_path: str, language: str | None = None,
                    correct: bool = True, threads: int | None = None) -> dict:
    executable = _find_cli()
    if not executable:
        raise RuntimeError(
            "Whisper.cpp CLI was not found. Install whisper-cli and ensure it is on PATH, "
            "or set CMS_WHISPER_CPP_BIN to its executable path."
        )
    if not _model_is_valid():
        raise RuntimeError(
            "Whisper.cpp Tiny model is missing or failed its official checksum. "
            "Download and verify the Tiny model from the Caption Studio controls first."
        )

    output_id = new_id()
    wav_path = config.TEMP_DIR / f"{output_id}.wav"
    output_prefix = config.TEMP_DIR / f"{output_id}-transcript"
    json_path = Path(f"{output_prefix}.json")
    log_path = config.TEMP_DIR / f"{output_id}.whisper.log"
    context.progress(5, "Preparing audio for Whisper.cpp")
    try:
        run_ffmpeg([
            "-i", media_path, "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(wav_path),
        ])
        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce audio for Whisper.cpp.")

        command = [
            executable, "-m", str(MODEL_PATH), "-f", str(wav_path),
            "-l", language or "auto", "-t",
            str(max(1, min(8, int(threads or config.WHISPER_CPU_THREADS)))),
            "-oj", "-of", str(output_prefix), "-np",
        ]
        context.progress(15, "Running Whisper.cpp Tiny on the audio")
        started = time.monotonic()
        with log_path.open("w", encoding="utf-8") as log_file:
            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    if elapsed >= config.JOB_TIMEOUT_SECONDS:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                        raise TimeoutError("Whisper.cpp exceeded the configured processing time limit.")
                    progress = min(85, 20 + int(elapsed * 65 / max(1, config.JOB_TIMEOUT_SECONDS)))
                    context.progress(progress, "Whisper.cpp is transcribing the audio")
                    time.sleep(2)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait()

        if process.returncode:
            detail = log_path.read_text(encoding="utf-8", errors="replace")[-2500:].strip()
            raise RuntimeError(detail or f"Whisper.cpp exited with status {process.returncode}.")
        if not json_path.is_file() or json_path.stat().st_size == 0:
            raise RuntimeError("Whisper.cpp completed without producing its JSON transcript.")

        data = json.loads(json_path.read_text(encoding="utf-8"))
        segments, detected_language = parse_transcription(data)
        if correct:
            segments = correction_engine.correct_segments(segments)
        transcript = {
            "id": output_id,
            "language": detected_language if language is None else language,
            "language_probability": None,
            "duration": max((segment["end"] for segment in segments), default=0),
            "model": "whisper.cpp-tiny",
            "engine": "whisper.cpp",
            "word_timestamps_available": False,
            "segments": segments,
        }
        transcript_path = config.OUTPUT_DIR / f"{output_id}.json"
        transcript_path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8")
        context.progress(95, "Saved Whisper.cpp transcript")
        return transcript
    except (OSError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        LOGGER.exception("Whisper.cpp transcription failed")
        raise RuntimeError(f"Whisper.cpp transcription failed: {exc}") from exc
    finally:
        for path in (wav_path, json_path, log_path):
            path.unlink(missing_ok=True)
