"""FFmpeg/ffprobe wrappers with bounded execution and clear errors."""

import json
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

import config


class MediaToolError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _available_encoders(executable: str) -> frozenset[str]:
    try:
        result = subprocess.run(
            [executable, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError("FFmpeg encoder detection exceeded its time limit.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "Could not query FFmpeg encoders.").strip()
        raise MediaToolError(detail[-1500:])
    encoders = set()
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        fields = line.split(maxsplit=2)
        if len(fields) >= 2 and len(fields[0]) == 6 and "V" in fields[0]:
            encoders.add(fields[1])
        elif len(fields) >= 2 and len(fields[0]) == 6 and "A" in fields[0]:
            encoders.add(fields[1])
    return frozenset(encoders)


def require_encoder(encoder: str) -> None:
    executable = require_ffmpeg()
    if encoder not in _available_encoders(executable):
        raise MediaToolError(
            f"This FFmpeg build does not provide the required {encoder} encoder. "
            "Install or build FFmpeg with that encoder enabled."
        )


def require_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if not executable:
        raise MediaToolError("FFmpeg was not found. Install the Termux ffmpeg package to use media tools.")
    return executable


def run_ffmpeg(arguments: list[str], timeout: int | None = None) -> None:
    executable = require_ffmpeg()
    command = [executable, "-hide_banner", "-nostdin", "-y", *map(str, arguments)]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True,
            timeout=timeout or config.JOB_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaToolError("FFmpeg exceeded the configured processing time limit.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "Unknown FFmpeg error").strip()
        raise MediaToolError(detail[-2500:])


def media_info(path: str | Path) -> dict:
    executable = shutil.which("ffprobe")
    if not executable:
        raise MediaToolError("ffprobe was not found. Install the Termux ffmpeg package.")
    result = subprocess.run(
        [executable, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise MediaToolError((result.stderr or "Could not read media information.")[-1500:])
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    probed_streams = []
    for stream in streams:
        stream_info = {
            "type": stream.get("codec_type"),
            "codec": stream.get("codec_name"),
            "sample_rate": stream.get("sample_rate"),
            "channels": stream.get("channels"),
            "bit_rate": int(stream["bit_rate"]) if str(stream.get("bit_rate", "")).isdigit() else None,
            "width": stream.get("width"),
            "height": stream.get("height"),
            "pixel_format": stream.get("pix_fmt"),
            "profile": stream.get("profile"),
            "frame_rate": stream.get("avg_frame_rate"),
            "start_time": _float_or_none(stream.get("start_time")),
            "duration": _float_or_none(stream.get("duration")),
        }
        probed_streams.append(stream_info)
    return {
        "duration": _float_or_none(fmt.get("duration")) or 0,
        "size": int(fmt.get("size", 0) or 0),
        "format": fmt.get("format_name", ""),
        "video": any(stream.get("codec_type") == "video" for stream in streams),
        "audio": any(stream.get("codec_type") == "audio" for stream in streams),
        "streams": probed_streams,
    }


def _float_or_none(value) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None
