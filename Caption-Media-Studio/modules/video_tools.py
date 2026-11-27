"""Video metadata and subtitle burn-in helpers."""

import json
from pathlib import Path

import config
from modules.ffmpeg_tools import media_info, run_ffmpeg
from modules.file_store import new_id
from modules.subtitle_export import export_ass
from modules.text_animation import build_drawtext_filters


def get_info(media_path: str) -> dict:
    return media_info(media_path)


def render_video(context, input_path: str, layers: list[dict],
                 subtitle_segments: list[dict] | None = None,
                 burn_subtitles: bool = False,
                 caption_style: dict | None = None) -> dict:
    output_id = new_id()
    output_path = config.OUTPUT_DIR / f"{output_id}.mp4"
    temporary_path = config.TEMP_DIR / f"{output_id}.mp4"
    filters = []
    try:
        if burn_subtitles and subtitle_segments:
            ass_path = config.TEMP_DIR / f"{output_id}.ass"
            ass_path.write_text(export_ass(subtitle_segments, caption_style), encoding="utf-8")
            escaped = str(ass_path).replace("\\", "\\\\").replace(":", "\\:")
            filters.append(f"subtitles=filename='{escaped}'")
        filters.extend(build_drawtext_filters(layers, output_id))
        arguments = ["-i", input_path]
        if filters:
            arguments.extend(["-vf", ",".join(filters)])
        arguments.extend(["-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
                          "-preset", "ultrafast", "-crf", "23", "-c:a", "copy",
                          "-movflags", "+faststart", str(temporary_path)])
        context.progress(10, "Rendering text and subtitle layers")
        run_ffmpeg(arguments)
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce a valid rendered video.")
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
        for path in config.TEMP_DIR.glob(f"{output_id}*"):
            if path.is_file():
                path.unlink(missing_ok=True)
    return {"file_id": output_id, "filename": output_path.name, "format": "mp4"}


def list_generated_files() -> list[dict]:
    items = []
    for path in config.OUTPUT_DIR.iterdir():
        if not path.is_file():
            continue
        items.append({
            "file_id": path.stem,
            "filename": path.name,
            "extension": path.suffix.lstrip(".").lower(),
            "size": path.stat().st_size,
            "modified": path.stat().st_mtime,
        })
    return sorted(items, key=lambda item: item["modified"], reverse=True)


def save_transcript(segments: list[dict], language: str, model: str) -> dict:
    transcript_id = new_id()
    payload = {"id": transcript_id, "language": language, "model": model, "segments": segments}
    path = config.OUTPUT_DIR / f"{transcript_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"file_id": transcript_id, "filename": path.name}
