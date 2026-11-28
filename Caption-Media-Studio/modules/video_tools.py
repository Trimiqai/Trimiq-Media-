"""Video metadata and subtitle burn-in helpers."""

import json
import math
from pathlib import Path

import config
from modules.ffmpeg_tools import media_info, require_encoder, run_ffmpeg
from modules.file_store import new_id
from modules.subtitle_export import export_ass
from modules.text_animation import build_drawtext_filters


def get_info(media_path: str) -> dict:
    return media_info(media_path)


def _validate_android_mp4(source: dict, output: dict) -> dict:
    if not output.get("size", 0):
        raise RuntimeError("The rendered MP4 is empty.")
    if "mp4" not in str(output.get("format", "")).split(","):
        raise RuntimeError("The rendered file is not an MP4 container.")

    source_video = next((stream for stream in source["streams"] if stream["type"] == "video"), None)
    output_video = next((stream for stream in output["streams"] if stream["type"] == "video"), None)
    if source_video is None or output_video is None:
        raise RuntimeError("The source or rendered file has no video stream.")
    if output_video.get("codec") != "h264":
        raise RuntimeError("The MP4 video stream is not H.264.")
    if output_video.get("pixel_format") != "yuv420p":
        raise RuntimeError("The MP4 video pixel format is not yuv420p.")
    if not output_video.get("width") or not output_video.get("height"):
        raise RuntimeError("The rendered MP4 has invalid video dimensions.")

    source_audio = [stream for stream in source["streams"] if stream["type"] == "audio"]
    output_audio = [stream for stream in output["streams"] if stream["type"] == "audio"]
    if source_audio and (not output_audio or any(stream.get("codec") != "aac" for stream in output_audio)):
        raise RuntimeError("The source audio was not preserved as AAC in the rendered MP4.")
    if not source_audio and output_audio:
        raise RuntimeError("The rendered MP4 unexpectedly contains an audio stream.")

    source_duration = float(source.get("duration") or 0)
    output_duration = float(output.get("duration") or 0)
    if not math.isfinite(output_duration) or output_duration <= 0:
        raise RuntimeError("ffprobe could not verify the rendered MP4 duration.")
    if source_duration > 0 and abs(source_duration - output_duration) > max(0.5, source_duration * 0.001):
        raise RuntimeError("The rendered MP4 duration differs unexpectedly from the source.")

    if source_audio and output_audio:
        source_video_start = source_video.get("start_time")
        source_audio_start = source_audio[0].get("start_time")
        output_video_start = output_video.get("start_time")
        output_audio_start = output_audio[0].get("start_time")
        if all(value is not None for value in (
            source_video_start, source_audio_start, output_video_start, output_audio_start,
        )):
            source_offset = source_audio_start - source_video_start
            output_offset = output_audio_start - output_video_start
            if abs(source_offset - output_offset) > 0.15:
                raise RuntimeError("The audio/video start-time offset changed during rendering.")

    return {
        "video_codec": output_video["codec"],
        "pixel_format": output_video["pixel_format"],
        "width": output_video["width"],
        "height": output_video["height"],
        "audio_codec": output_audio[0]["codec"] if output_audio else None,
        "duration": output_duration,
        "size": output["size"],
        "container": output["format"],
    }


def render_video(context, input_path: str, layers: list[dict],
                 subtitle_segments: list[dict] | None = None,
                 burn_subtitles: bool = False,
                 caption_style: dict | None = None) -> dict:
    output_id = new_id()
    output_path = config.OUTPUT_DIR / f"{output_id}.mp4"
    temporary_path = config.TEMP_DIR / f"{output_id}.mp4"
    filters = []
    try:
        source_info = media_info(input_path)
        if not source_info.get("video"):
            raise ValueError("Video export requires a source file containing a video stream.")
        source_video = next(stream for stream in source_info["streams"] if stream["type"] == "video")
        source_audio = [stream for stream in source_info["streams"] if stream["type"] == "audio"]
        if burn_subtitles and subtitle_segments:
            ass_path = config.TEMP_DIR / f"{output_id}.ass"
            ass_path.write_text(export_ass(subtitle_segments, caption_style), encoding="utf-8")
            escaped = str(ass_path).replace("\\", "\\\\").replace(":", "\\:")
            filters.append(f"subtitles=filename='{escaped}'")
        filters.extend(build_drawtext_filters(layers, output_id))
        encode_video = bool(filters) or source_video.get("codec") != "h264" or source_video.get("pixel_format") != "yuv420p"
        encode_audio = bool(source_audio) and any(stream.get("codec") != "aac" for stream in source_audio)
        if encode_video:
            require_encoder("libx264")
        if encode_audio:
            require_encoder("aac")
        arguments = ["-i", input_path]
        if filters:
            arguments.extend(["-vf", ",".join(filters)])
        arguments.extend(["-map", "0:v:0", "-map", "0:a?"])
        if encode_video:
            arguments.extend([
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
            ])
        else:
            arguments.extend(["-c:v", "copy"])
        if source_audio:
            if encode_audio:
                arguments.extend(["-c:a", "aac", "-b:a", "192k"])
            else:
                arguments.extend(["-c:a", "copy"])
        arguments.extend(["-movflags", "+faststart", str(temporary_path)])
        context.progress(10, "Rendering an Android-compatible MP4")
        run_ffmpeg(arguments)
        if not temporary_path.is_file() or temporary_path.stat().st_size == 0:
            raise RuntimeError("FFmpeg did not produce a valid rendered video.")
        context.progress(90, "Verifying MP4 streams and playback metadata")
        output_info = media_info(temporary_path)
        verified = _validate_android_mp4(source_info, output_info)
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
        for path in config.TEMP_DIR.glob(f"{output_id}*"):
            if path.is_file():
                path.unlink(missing_ok=True)
    return {
        "file_id": output_id,
        "filename": output_path.name,
        "format": "mp4",
        "verified": verified,
    }


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
