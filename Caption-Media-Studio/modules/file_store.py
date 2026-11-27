"""Safe access helpers for project-owned files."""

import mimetypes
import re
import uuid
from pathlib import Path

import config


ALLOWED_MEDIA = {
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".webm": "media",
    ".avi": "video",
    ".m4v": "video",
    ".3gp": "video",
    ".3g2": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".ogg": "audio",
    ".opus": "audio",
    ".flac": "audio",
    ".wma": "audio",
}
ALLOWED_OUTPUTS = {".srt", ".vtt", ".txt", ".json", ".mp3", ".wav", ".m4a",
                   ".aac", ".opus", ".flac", ".mp4"}
SAFE_ID = re.compile(r"^[0-9a-f]{32}$")


class FileValidationError(ValueError):
    """Raised when an upload or file identifier is invalid."""


def _has_media_signature(file_storage, extension: str) -> bool:
    stream = file_storage.stream
    try:
        position = stream.tell()
        header = stream.read(16)
        stream.seek(position)
    except (AttributeError, OSError):
        return False
    if not header:
        return False
    if extension in {".mp4", ".mov", ".m4a", ".m4v", ".3gp", ".3g2"}:
        return len(header) >= 8 and header[4:8] == b"ftyp"
    if extension in {".mkv", ".webm"}:
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if extension == ".avi":
        return header.startswith(b"RIFF") and header[8:12] == b"AVI "
    if extension == ".wav":
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if extension == ".flac":
        return header.startswith(b"fLaC")
    if extension in {".ogg", ".opus"}:
        return header.startswith(b"OggS")
    if extension == ".wma":
        return header.startswith(bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c"))
    if extension == ".mp3":
        return header.startswith(b"ID3") or (len(header) > 1 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)
    if extension == ".aac":
        return header.startswith(b"ID3") or (len(header) > 1 and header[0] == 0xFF and header[1] & 0xF6 == 0xF0)
    return False


def new_id() -> str:
    return uuid.uuid4().hex


def validate_upload(file_storage) -> tuple[str, str]:
    original_name = Path((file_storage.filename or "").replace("\\", "/")).name
    extension = Path(original_name).suffix.lower()
    media_kind = ALLOWED_MEDIA.get(extension)
    if not original_name or not media_kind:
        raise FileValidationError("Unsupported file extension. Upload a supported audio or video file.")

    content_type = (file_storage.mimetype or "").lower()
    guessed_type = mimetypes.guess_type(original_name)[0] or ""
    expected_prefix = f"{media_kind}/"
    accepted_content_type = (
        media_kind == "media" and (content_type.startswith("video/") or content_type.startswith("audio/"))
    ) or content_type.startswith(expected_prefix)
    if content_type and content_type != "application/octet-stream" and not accepted_content_type:
        raise FileValidationError(f"The uploaded MIME type does not match the {media_kind} file extension.")
    if guessed_type and media_kind != "media" and not guessed_type.startswith(expected_prefix):
        raise FileValidationError("The file extension is not recognized as a supported media type.")
    if not _has_media_signature(file_storage, extension):
        raise FileValidationError("The file content does not match a supported media format.")

    return original_name, extension


def owned_path(directory: Path, file_id: str, extensions: set[str] | None = None) -> Path:
    if not SAFE_ID.fullmatch(file_id or ""):
        raise FileValidationError("Invalid file identifier.")
    candidates = list(directory.glob(f"{file_id}.*"))
    if extensions is not None:
        candidates = [path for path in candidates if path.suffix.lower() in extensions]
    if len(candidates) != 1 or not candidates[0].is_file():
        raise FileValidationError("File was not found.")
    resolved = candidates[0].resolve()
    if resolved.parent != directory.resolve():
        raise FileValidationError("File path is outside the project storage directory.")
    return resolved


def output_path(file_id: str, extension: str) -> Path:
    if not SAFE_ID.fullmatch(file_id or "") or extension.lower() not in ALLOWED_OUTPUTS:
        raise FileValidationError("Invalid output identifier or type.")
    return config.OUTPUT_DIR / f"{file_id}{extension.lower()}"
