"""Local-only Flask application for Caption Media Studio."""

import fcntl
import logging
import math
import mimetypes
import os
import re
import signal
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge

import config
from modules import audio_tools, correction_engine, subtitle_export, text_to_voice, video_tools
from modules.caption_engine import transcribe
from modules.file_store import (
    ALLOWED_MEDIA, ALLOWED_OUTPUTS, FileValidationError, new_id,
    output_path, owned_path, validate_upload,
)
from modules.job_manager import JobManager
from modules.memory_guard import memory_status
from modules.voice_to_text import transcribe_voice

logging.basicConfig(
    level=os.environ.get("CMS_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_DIR / "app.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
LOGGER = logging.getLogger("caption_media_studio")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = config.MAX_UPLOAD_BYTES
app.json.ensure_ascii = False
jobs = JobManager()


def _is_allowed_origin(origin: str) -> bool:
    if not origin:
        return True
    if origin == "null":
        return False
    try:
        parsed_origin = urlparse(origin)
        hostname = parsed_origin.hostname
        return (
            hostname in {"localhost", "127.0.0.1", "::1"}
            or parsed_origin.netloc.lower() == request.host.lower()
        )
    except ValueError:
        return False


@app.before_request
def protect_state_changes_from_cross_origin():
    if request.path.startswith("/api/") and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin")
        if origin and not _is_allowed_origin(origin):
            return jsonify({"error": "Cross-origin changes are not allowed."}), 403


@app.after_request
def set_security_headers(response):
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; "
        "font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "microphone=(self)"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


def _cleanup_temp_files():
    cutoff = time.time() - config.TEMP_FILE_MAX_AGE_SECONDS
    upload_cutoff = time.time() - config.UPLOAD_FILE_MAX_AGE_SECONDS
    for directory, modified_before in ((config.TEMP_DIR, cutoff), (config.UPLOAD_DIR, upload_cutoff)):
        for path in directory.iterdir():
            if path.is_file() and path.stat().st_mtime < modified_before:
                path.unlink(missing_ok=True)


def _json_body() -> dict:
    data = request.get_json(silent=False)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object.")
    return data


def _upload_path(file_id: str) -> Path:
    return owned_path(config.UPLOAD_DIR, file_id, set(ALLOWED_MEDIA))


def _normalize_segments(data: dict) -> list[dict]:
    segments = data.get("segments")
    if not isinstance(segments, list) or len(segments) > 10000:
        raise ValueError("Provide a subtitle segment list (maximum 10,000 entries).")
    normalized = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            raise ValueError(f"Subtitle segment {index + 1} is invalid.")
        text = str(segment.get("text", ""))
        start, end = float(segment.get("start", 0)), float(segment.get("end", 0))
        if (len(text) > 5000 or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end < start or end > 24 * 60 * 60):
            raise ValueError(f"Subtitle segment {index + 1} has invalid text or timestamps.")
        normalized.append({**segment, "start": start, "end": end, "text": text})
    return normalized


def _set_text_variant(segments: list[dict], variant: str) -> list[dict]:
    if variant not in {"original", "corrected"}:
        raise ValueError("Subtitle variant must be original or corrected.")
    result = []
    for segment in segments:
        item = dict(segment)
        if variant == "original":
            item["text"] = str(item.get("original_text", item.get("text", "")))
        result.append(item)
    return result


def _queue(kind: str, function, *args, **kwargs):
    try:
        job_id = jobs.submit(kind, function, *args, **kwargs)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 429
    return jsonify({"job_id": job_id, "state": "queued"}), 202


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    import importlib.util
    import shutil

    return jsonify({
        "status": "ok",
        "python": os.sys.version.split()[0],
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "faster_whisper": importlib.util.find_spec("faster_whisper") is not None,
        "edge_tts": importlib.util.find_spec("edge_tts") is not None,
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "queue_worker_count": 1,
        "memory": memory_status(),
    })


@app.get("/api/settings")
def settings():
    return jsonify({
        "max_upload_mb": config.MAX_UPLOAD_MB,
        "max_queued_jobs": config.MAX_QUEUED_JOBS,
        "whisper_default_model": config.WHISPER_MODEL,
        "whisper_compute_type": config.WHISPER_COMPUTE_TYPE,
        "host": config.HOST,
        "port": config.PORT,
        "voices": text_to_voice.SUPPORTED_VOICES,
        "memory": memory_status(),
    })


@app.post("/api/upload")
def upload():
    if "file" not in request.files:
        return jsonify({"error": "Select an audio or video file to upload."}), 400
    uploaded = request.files["file"]
    try:
        original_name, extension = validate_upload(uploaded)
    except FileValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    file_id = new_id()
    destination = config.UPLOAD_DIR / f"{file_id}{extension}"
    uploaded.save(destination)
    if not destination.is_file() or destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        return jsonify({"error": "The uploaded file was empty or could not be saved."}), 400
    return jsonify({
        "file_id": file_id,
        "filename": original_name,
        "kind": ALLOWED_MEDIA[extension],
        "size": destination.stat().st_size,
        "url": f"/api/uploads/{file_id}/download",
    }), 201


@app.get("/api/uploads/<file_id>/download")
def download_upload(file_id):
    try:
        path = _upload_path(file_id)
    except FileValidationError as exc:
        return jsonify({"error": str(exc)}), 404
    return send_file(path, as_attachment=False, download_name=path.name)


@app.post("/api/transcribe")
def start_transcription():
    data = _json_body()
    try:
        path = _upload_path(str(data.get("file_id", "")))
    except FileValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    language = str(data.get("language", "auto"))
    if language != "auto" and not re.fullmatch(r"[a-z]{2,3}", language):
        return jsonify({"error": "Use a supported two- or three-letter Whisper language code."}), 400
    model_name = str(data.get("model", config.WHISPER_MODEL))
    if model_name not in {"tiny", "base", "small", "medium"}:
        return jsonify({"error": "Select a supported Whisper model size."}), 400
    return _queue(
        "transcription", transcribe, str(path),
        None if language == "auto" else language,
        model_name, bool(data.get("correct", True)),
        bool(data.get("word_timestamps", True)),
    )


@app.post("/api/voice/transcribe")
def start_voice_transcription():
    data = _json_body()
    try:
        path = _upload_path(str(data.get("file_id", "")))
    except FileValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    language = str(data.get("language", "auto"))
    if language not in {"auto", "hi", "en", "ur"}:
        return jsonify({"error": "Choose automatic detection, Hindi, English, or Urdu."}), 400
    model_name = str(data.get("model", config.WHISPER_MODEL))
    if model_name not in {"tiny", "base", "small"}:
        return jsonify({"error": "Select tiny, base, or small model."}), 400
    return _queue("voice transcription", transcribe_voice, str(path),
                  None if language == "auto" else language, model_name,
                  bool(data.get("correct", True)))


@app.post("/api/subtitles/correct")
def correct_subtitles():
    data = _json_body()
    segments = _normalize_segments(data)
    enabled = bool(data.get("enabled", True))
    corrected = correction_engine.correct_segments(segments, enabled=enabled)
    return jsonify({"segments": corrected, "enabled": enabled})


@app.post("/api/subtitles/export")
def export_subtitles():
    data = _json_body()
    segments = _normalize_segments(data)
    segments = subtitle_export.format_segments_for_lines(
        segments,
        int(data.get("max_words", 7)),
        int(data.get("max_lines", 2)),
    )
    segments = _set_text_variant(segments, str(data.get("variant", "corrected")))
    file_format = str(data.get("format", "srt")).lower()
    content = subtitle_export.export_subtitles(
        segments, file_format, word_level=bool(data.get("word_timestamps", False))
    )
    file_id = new_id()
    destination = output_path(file_id, f".{file_format}")
    destination.write_text(content, encoding="utf-8")
    return jsonify({
        "file_id": file_id,
        "filename": destination.name,
        "download_url": f"/api/files/{file_id}",
    }), 201


@app.post("/api/subtitles/parse")
def parse_subtitles():
    data = _json_body()
    content = data.get("content", "")
    if not isinstance(content, str) or len(content) > 2_000_000:
        raise ValueError("Subtitle content is invalid or too large.")
    return jsonify({"segments": subtitle_export.parse_srt(content)})


@app.post("/api/media/info")
def media_information():
    data = _json_body()
    path = _upload_path(str(data.get("file_id", "")))
    return jsonify(video_tools.get_info(str(path)))


@app.post("/api/audio/extract")
def extract_audio():
    data = _json_body()
    path = _upload_path(str(data.get("file_id", "")))
    output_format = str(data.get("format", "mp3")).lower()
    bitrate = str(data.get("bitrate", "192k"))
    return _queue("audio extraction", audio_tools.extract_audio, str(path), output_format, bitrate)


@app.post("/api/audio/convert")
def convert_audio():
    data = _json_body()
    path = _upload_path(str(data.get("file_id", "")))
    output_format = str(data.get("format", "mp3")).lower()
    bitrate = str(data.get("bitrate", "192k"))
    return _queue("audio conversion", audio_tools.convert_audio, str(path), output_format, bitrate)


@app.post("/api/audio/trim")
def trim_audio():
    data = _json_body()
    path = _upload_path(str(data.get("file_id", "")))
    try:
        start = float(data.get("start"))
        end = float(data.get("end"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Enter numeric trim start and end times in seconds.") from exc
    output_format = str(data.get("format", "")).lower() or None
    return _queue("audio trim", audio_tools.trim_audio, str(path), start, end, output_format)


@app.post("/api/video/render")
def render_video():
    data = _json_body()
    path = _upload_path(str(data.get("file_id", "")))
    layers = data.get("layers", [])
    if not isinstance(layers, list):
        raise ValueError("Text layers must be a list.")
    segments = _normalize_segments({"segments": data.get("segments", [])})
    burn_subtitles = bool(data.get("burn_subtitles", False))
    caption_style = data.get("caption_style", {})
    if not isinstance(caption_style, dict):
        return jsonify({"error": "Caption style must be an object."}), 400
    if burn_subtitles and not segments:
        raise ValueError("Add subtitle segments before enabling burned-in subtitles.")
    if burn_subtitles:
        segments = subtitle_export.format_segments_for_lines(
            segments,
            int(caption_style.get("max_words", 7)),
            int(caption_style.get("max_lines", 2)),
        )
    return _queue("video render", video_tools.render_video, str(path), layers, segments,
                  burn_subtitles, caption_style)


@app.post("/api/tts")
def start_tts():
    data = _json_body()
    text = str(data.get("text", ""))
    voice = str(data.get("voice", ""))
    rate = str(data.get("rate", "+0%"))
    pitch = str(data.get("pitch", "+0Hz"))
    return _queue("text to speech", text_to_voice.synthesize, text, voice, rate, pitch)


@app.get("/api/jobs")
def list_jobs():
    return jsonify({"jobs": jobs.list()})


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    item = jobs.get(job_id)
    if item is None:
        return jsonify({"error": "Job not found."}), 404
    return jsonify(item)


@app.post("/api/jobs/<job_id>/cancel")
def cancel_job(job_id):
    if jobs.cancel(job_id):
        return jsonify({"state": "cancelled"})
    return jsonify({"error": "Only jobs that have not started can be cancelled."}), 409


@app.get("/api/history")
def history():
    return jsonify({"files": video_tools.list_generated_files(), "jobs": jobs.list()})


@app.get("/api/files/<file_id>")
def download_file(file_id):
    try:
        path = owned_path(config.OUTPUT_DIR, file_id, ALLOWED_OUTPUTS)
    except FileValidationError as exc:
        return jsonify({"error": str(exc)}), 404
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return send_file(path, as_attachment=True, download_name=path.name, mimetype=mimetype)


@app.get("/api/files/<file_id>/preview")
def preview_file(file_id):
    try:
        path = owned_path(config.OUTPUT_DIR, file_id, ALLOWED_OUTPUTS)
    except FileValidationError as exc:
        return jsonify({"error": str(exc)}), 404
    mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return send_file(path, as_attachment=False, download_name=path.name, mimetype=mimetype)


@app.delete("/api/files/<file_id>")
def delete_file(file_id):
    try:
        path = owned_path(config.OUTPUT_DIR, file_id, ALLOWED_OUTPUTS)
    except FileValidationError as exc:
        return jsonify({"error": str(exc)}), 404
    path.unlink()
    return jsonify({"deleted": True})


@app.errorhandler(RequestEntityTooLarge)
def request_too_large(_error):
    return jsonify({"error": f"Upload exceeds the configured {config.MAX_UPLOAD_MB} MB limit."}), 413


@app.errorhandler(HTTPException)
def http_error(error):
    if request.path.startswith("/api/"):
        return jsonify({"error": error.description}), error.code
    return error


@app.errorhandler(Exception)
def unexpected_error(error):
    if isinstance(error, ValueError):
        return jsonify({"error": str(error)}), 400
    LOGGER.exception("Unhandled request error")
    message = str(error) if app.debug else "An unexpected server error occurred. Check logs/app.log."
    return jsonify({"error": message}), 500


def _shutdown(*_args):
    jobs.shutdown()
    raise SystemExit(0)


def _start_cloudflare_tunnel():
    if config.CLOUDFLARE_MODE == "off":
        return None
    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise RuntimeError(
            "Cloudflare Tunnel is enabled, but cloudflared was not found. "
            "Install cloudflared or set CMS_CLOUDFLARE_MODE=off."
        )
    local_url = f"http://127.0.0.1:{config.PORT}"
    LOGGER.warning(
        "Starting a public Cloudflare Quick Tunnel to %s. Anyone with its URL can access this app.",
        local_url,
    )
    return subprocess.Popen(
        [cloudflared, "tunnel", "--url", local_url, "--no-autoupdate"],
    )


def _stop_cloudflare_tunnel(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


_cleanup_temp_files()
signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)


if __name__ == "__main__":
    LOGGER.info("Caption Media Studio listening at http://%s:%s", config.HOST, config.PORT)
    with open(config.LOG_DIR / "server.lock", "a+", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            LOGGER.error("Another Caption Media Studio server already holds the startup lock.")
            raise SystemExit(1) from exc
        tunnel_process = None
        try:
            tunnel_process = _start_cloudflare_tunnel()
            app.run(host=config.HOST, port=config.PORT, threaded=True, use_reloader=False)
        finally:
            _stop_cloudflare_tunnel(tunnel_process)
            jobs.shutdown()
