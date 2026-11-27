# Caption Media Studio

A localhost-only, mobile-friendly Flask workspace for media transcription, subtitle editing/export, text overlays, speech recording, audio conversion and file history. The project is kept separate from the repository root in `Caption-Media-Studio/`.

## What is implemented

- One responsive dashboard with Caption Studio, Text Animation Studio, Voice-to-Text, Audio Tools, Video Tools, Export History and Settings.
- Local audio/video uploads with extension/MIME checks, random storage names, progress indication and a configurable upload limit.
- One background worker, persistent SQLite job status, restart recovery for interrupted jobs and safe cancellation of jobs that have not started.
- Lazy Faster-Whisper loading with model reuse, CPU thread configuration, compute-type capability checks and a memory-availability guard.
- Editable speech segments, original/corrected comparison, conservative offline English/Hinglish/Devanagari corrections, timing edits and undo.
- SRT, VTT and TXT exports, optional word-level exports, styled ASS subtitle burn-in and karaoke highlighting when matching word timings are available.
- Layered text overlays, timing, drag positioning, a simple timeline and FFmpeg rendering for fade, slide, pop/zoom/scale, bounce, typewriter, word/letter reveal, karaoke, shake, pulse and glow presets.
- Browser microphone recording where supported, audio upload, video audio extraction, audio conversion/trimming, media information and optional Edge-TTS MP3 generation.
- Output download/delete history, temporary-file cleanup, server health, local-only binding, logs and single-server startup locking.

The browser preview approximates FFmpeg output. Installed fonts, FFmpeg build options and font rendering can differ. Gradient text, rounded text boxes, line spacing and rotation are preview-only with the current renderer; those options render as solid, unrotated text with square backgrounds and default subtitle line spacing. Positive text-layer letter spacing is approximated with thin-space glyphs. FFmpeg must include the filters/codecs used by a particular operation (notably `drawtext`, `subtitles`/libass and `libx264`).

## Termux setup (Android)

The target device should be checked first. Do not upgrade all Termux packages. Install only the required system packages:

```bash
pkg install python ffmpeg
```

Copy this project folder to the device, then from inside `Caption-Media-Studio/`:

```bash
python --version
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

The core dashboard requires Flask. Faster-Whisper/CTranslate2 and Edge-TTS are optional because Python 3.14 and Android/aarch64 wheel availability varies. Check wheel compatibility before installing the transcription dependencies:

```bash
python -m pip install --dry-run --only-binary=:all: faster-whisper
```

If that check reports that a compatible wheel is unavailable, do not force a source build on the phone without checking CTranslate2's Termux support. Audio/video tools work once FFmpeg is installed; transcription displays a clear error until a compatible Faster-Whisper install is available. To install optional dependencies after verifying compatibility:

```bash
python -m pip install -r requirements-optional.txt
```

No paid API is needed. Edge-TTS is optional, uses the internet and sends its entered text to the Edge-TTS service; local transcription does not use it.

## Start and use

Start the app from the project directory with the virtual environment active:

```bash
python app.py
```

Open `http://127.0.0.1:5001` in the device browser. The app binds to loopback only; changing `CMS_HOST` to a non-loopback address is rejected. A lock prevents a second server instance. The Settings page and `/api/health` show available system tools and Python packages.

### Optional Cloudflare Quick Tunnel

Install `cloudflared` separately, then start the app and a temporary Cloudflare Quick Tunnel together:

```bash
CMS_CLOUDFLARE_MODE=quick python app.py
```

`cloudflared` prints the temporary public URL in the same terminal. Open that URL to use the frontend; Flask continues to serve both the frontend and API on loopback. Press `Ctrl-C` to stop the app and tunnel together. Without the environment variable, the tunnel stays off and the existing `python app.py` startup is unchanged. The tunnel URL changes when restarted.

**Security:** A Quick Tunnel is publicly reachable by anyone who gets its URL; this app does not have user login. Do not use it for private media. For a persistent or private deployment, use a named Cloudflare Tunnel with Cloudflare Access configured rather than a Quick Tunnel.

### Optional settings

Set environment variables before starting the process to customize the upload/job limits and CPU inference options:

```bash
export CMS_MAX_UPLOAD_MB=512
export CMS_MAX_QUEUED_JOBS=10
export CMS_WHISPER_MODEL=tiny
export CMS_WHISPER_COMPUTE_TYPE=int8
export CMS_WHISPER_CPU_THREADS=2
# Optionally start a temporary public Cloudflare Quick Tunnel too:
# export CMS_CLOUDFLARE_MODE=quick
python app.py
```

The selected compute type is checked against the installed CTranslate2 CPU capabilities; the app fails the transcription job with an explicit message rather than silently switching types. The model is loaded only on first transcription. Available-memory estimates are conservative and do not replace device-specific benchmarking; close other apps or select a smaller model when prompted.

## Troubleshooting and recovery

- **Port already in use / another app instance:** Stop the existing process in its Termux session before starting another. `logs/server.lock` prevents this app from starting twice.
- **FFmpeg unavailable:** Install the Termux `ffmpeg` package. Check that the build has the required `drawtext`, `subtitles`/libass and `libx264` capabilities for rendering.
- **Faster-Whisper/CTranslate2 install fails:** Check Python, Android ABI and wheel support first. This project intentionally does not force a large native source build or silently switch recognition engines.
- **Memory warning:** Select `tiny` or `base`, close memory-heavy apps and retry. The app deliberately refuses a model when available memory is below its estimated requirement plus a safety reserve.
- **Unsupported compute type:** Set `CMS_WHISPER_COMPUTE_TYPE` to one reported by the installed CTranslate2 build; the app does not silently lower/alter the setting.
- **Job failed / interrupted:** Inspect the friendly error in the job history and detailed logs in `logs/app.log`. On restart, previously queued/running jobs are marked interrupted instead of being presented as completed. Incomplete media outputs are written under `temp/` and are not exposed in export history.
- **Edge-TTS failed:** Check internet access and the selected voice. This feature is online-only.
- **Restart:** Stop with `Ctrl-C`, then run `python app.py` again. The server shuts down the worker cleanly; SQLite keeps completed job records.

## Storage and privacy

- `uploads/`: original media files. Old uploads are removed on startup after the configured seven-day retention period.
- `outputs/`: generated media, subtitle and transcript downloads. Delete files from Export History when no longer needed.
- `temp/`: temporary render and subtitle files, cleaned at startup after 24 hours.
- `logs/`: application log, server lock and SQLite job database.

Files are stored locally and served by the localhost app. The interface loads no remote fonts or scripts. Keep the server on loopback; there is no authentication layer for public-network use.

## Development checks

From this directory:

```bash
python -m unittest discover -s tests -v
node --check static/js/app.js
python -m compileall -q .
```
