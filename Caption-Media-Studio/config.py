"""Runtime configuration for Caption Media Studio."""

import os
import ipaddress
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
TEMP_DIR = DATA_DIR / "temp"
LOG_DIR = DATA_DIR / "logs"
DATABASE_PATH = LOG_DIR / "jobs.sqlite3"

HOST = os.environ.get("CMS_HOST", "127.0.0.1")
if HOST != "localhost":
    try:
        if not ipaddress.ip_address(HOST).is_loopback:
            raise RuntimeError("Caption Media Studio can only bind to a loopback address.")
    except ValueError as exc:
        raise RuntimeError("CMS_HOST must be localhost or a loopback IP address.") from exc
PORT = int(os.environ.get("CMS_PORT", "5001"))
_cloudflare_mode = os.environ.get("CMS_CLOUDFLARE_MODE", "off").strip().lower()
if _cloudflare_mode not in {"off", "quick"}:
    raise RuntimeError("CMS_CLOUDFLARE_MODE must be 'off' or 'quick'.")
CLOUDFLARE_MODE = _cloudflare_mode
MAX_UPLOAD_MB = int(os.environ.get("CMS_MAX_UPLOAD_MB", "1024"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
MAX_QUEUED_JOBS = int(os.environ.get("CMS_MAX_QUEUED_JOBS", "10"))
JOB_TIMEOUT_SECONDS = int(os.environ.get("CMS_JOB_TIMEOUT_SECONDS", "7200"))
TEMP_FILE_MAX_AGE_SECONDS = int(os.environ.get("CMS_TEMP_FILE_MAX_AGE_SECONDS", "86400"))
UPLOAD_FILE_MAX_AGE_SECONDS = int(os.environ.get("CMS_UPLOAD_FILE_MAX_AGE_SECONDS", "604800"))

WHISPER_MODEL = os.environ.get("CMS_WHISPER_MODEL", "tiny")
WHISPER_DEVICE = os.environ.get("CMS_WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.environ.get("CMS_WHISPER_COMPUTE_TYPE", "int8")
WHISPER_CPU_THREADS = max(1, int(os.environ.get("CMS_WHISPER_CPU_THREADS", "2")))

for directory in (UPLOAD_DIR, OUTPUT_DIR, TEMP_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)
