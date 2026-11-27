"""SRT, WebVTT and plain-text subtitle serialization."""

import re
import unicodedata


def _timestamp(seconds: float, separator: str) -> str:
    milliseconds = max(0, round(float(seconds) * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02}{separator}{millis:03}"


def _comparison_words(text: str) -> list[str]:
    tokens = []
    for token in str(text).split():
        normalized = "".join(
            character.casefold() for character in token
            if character.isalnum() or unicodedata.category(character).startswith("M")
        )
        if normalized:
            tokens.append(normalized)
    return tokens


def _word_timing_matches(segment: dict) -> bool:
    words = segment.get("words") or []
    if not words:
        return False
    word_text = " ".join(str(word.get("word", "")).strip() for word in words)
    return _comparison_words(word_text) == _comparison_words(segment.get("text", ""))


def format_segments_for_lines(
    segments: list[dict], max_words: int = 7, max_lines: int = 2,
) -> list[dict]:
    """Wrap all dialogue, splitting long cues without dropping words."""
    max_words = max(1, min(20, int(max_words)))
    max_lines = max(1, min(4, int(max_lines)))
    words_per_cue = max_words * max_lines
    formatted = []
    for segment in segments:
        text_words = str(segment.get("text", "")).split()
        original_words = str(segment.get("original_text", segment.get("text", ""))).split()
        total_words = max(len(text_words), len(original_words))
        if total_words == 0:
            continue
        start, end = float(segment.get("start", 0)), float(segment.get("end", 0))
        word_entries = segment.get("words") or []
        timing_words = [str(word.get("word", "")).strip() for word in word_entries]
        timing_matches = (
            _comparison_words(" ".join(timing_words)) == _comparison_words(" ".join(text_words))
            or _comparison_words(" ".join(timing_words)) == _comparison_words(" ".join(original_words))
        )
        for offset in range(0, total_words, words_per_cue):
            current = text_words[offset:offset + words_per_cue]
            original = original_words[offset:offset + words_per_cue]
            item = dict(segment)
            item["text"] = "\n".join(
                " ".join(current[index:index + max_words])
                for index in range(0, len(current), max_words)
            )
            item["original_text"] = "\n".join(
                " ".join(original[index:index + max_words])
                for index in range(0, len(original), max_words)
            )
            item["words"] = word_entries[offset:offset + words_per_cue] if timing_matches else []
            if timing_matches and item["words"]:
                item["start"] = max(start, float(item["words"][0].get("start", start)))
                item["end"] = min(end, float(item["words"][-1].get("end", end)))
            else:
                item_start = start + (end - start) * offset / total_words
                item_end = start + (end - start) * min(total_words, offset + words_per_cue) / total_words
                item["start"], item["end"] = item_start, item_end
            formatted.append(item)
    return formatted


def _entries(segments: list[dict], word_level: bool = False):
    for segment in segments:
        words = segment.get("words") or []
        if word_level and _word_timing_matches(segment):
            for word in words:
                text = str(word.get("word", "")).strip()
                if text:
                    yield float(word.get("start", segment.get("start", 0))), \
                        float(word.get("end", segment.get("end", 0))), text
        else:
            text = str(segment.get("text", "")).strip()
            if text:
                yield float(segment.get("start", 0)), float(segment.get("end", 0)), text


def export_subtitles(segments: list[dict], file_format: str, word_level: bool = False) -> str:
    file_format = file_format.lower()
    entries = list(_entries(segments, word_level))
    if file_format == "txt":
        return "\n".join(text for _, _, text in entries) + ("\n" if entries else "")
    if file_format not in {"srt", "vtt"}:
        raise ValueError("Format must be SRT, VTT, or TXT.")
    separator = "," if file_format == "srt" else "."
    blocks = []
    for index, (start, end, text) in enumerate(entries, start=1):
        start = max(0.0, start)
        end = max(start, end)
        if file_format == "srt":
            blocks.append(f"{index}\n{_timestamp(start, separator)} --> {_timestamp(end, separator)}\n{text}")
        else:
            blocks.append(f"{_timestamp(start, separator)} --> {_timestamp(end, separator)}\n{text}")
    body = "\n\n".join(blocks)
    return f"WEBVTT\n\n{body}\n" if file_format == "vtt" else f"{body}\n"


def _ass_time(seconds: float) -> str:
    value = max(0, round(float(seconds) * 100))
    hours, remainder = divmod(value, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{centiseconds:02}"


def _ass_color(value: str, opacity: int = 100) -> str:
    value = value.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Invalid subtitle color.")
    alpha = round((100 - max(0, min(100, opacity))) * 255 / 100)
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{blue.upper()}{green.upper()}{red.upper()}&"


def export_ass(segments: list[dict], style: dict | None = None) -> str:
    """Create a styled ASS subtitle file for FFmpeg's libass subtitle filter."""
    style = style or {}
    font = str(style.get("font", "Arial")).strip()
    if not re.fullmatch(r"[\w -]{1,60}", font):
        raise ValueError("Invalid subtitle font name.")
    font_size = max(8, min(96, int(style.get("font_size", 32))))
    font_color = _ass_color(str(style.get("font_color", "#ffffff")))
    highlight_color = _ass_color(str(style.get("highlight_color", "#a891ff")))
    opacity = max(0, min(100, int(style.get("background_opacity", 0))))
    back_color = _ass_color(str(style.get("background_color", "#10111a")), opacity)
    outline = max(0, min(8, int(style.get("outline", 2))))
    shadow = max(0, min(12, int(style.get("shadow", 2))))
    bold = -1 if style.get("bold") else 0
    italic = -1 if style.get("italic") else 0
    alignment = str(style.get("alignment", "center"))
    position = str(style.get("position", "bottom"))
    align_code = {
        ("left", "bottom"): 1, ("center", "bottom"): 2, ("right", "bottom"): 3,
        ("left", "center"): 4, ("center", "center"): 5, ("right", "center"): 6,
        ("left", "top"): 7, ("center", "top"): 8, ("right", "top"): 9,
    }.get((alignment, position), 2)
    background = opacity > 0
    border_style = 3 if background else 1
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{highlight_color},{font_color},&HCC000000&,{back_color},{bold},{italic},0,0,100,100,0,0,{border_style},{outline},{shadow},{align_code},40,40,45,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dialogues = []
    highlight = bool(style.get("active_word_highlight", False))
    for segment in segments:
        start = float(segment.get("start", 0))
        end = float(segment.get("end", start))
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        if end <= start:
            end = start + 0.1
        words = []
        if highlight:
            words = segment.get("words") or []
            if not _word_timing_matches(segment):
                words = []
        if words:
            lines = []
            offset = 0
            for line in text.splitlines() or [text]:
                pieces = []
                for _line_word in line.split():
                    if offset >= len(words):
                        break
                    word = words[offset]
                    offset += 1
                    word_text = str(word.get("word", "")).strip()
                    if not word_text:
                        continue
                    word_start = max(start, float(word.get("start", start)))
                    word_end = min(end, float(word.get("end", end)))
                    duration = max(1, round((word_end - word_start) * 100))
                    escaped_word = word_text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
                    pieces.append(f"{{\\kf{duration}}}{escaped_word}")
                if pieces:
                    lines.append(" ".join(pieces))
            text = r"\N".join(lines) if lines else text
        else:
            text = text.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")
            text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", r"\N")
            text = "{\\1c" + font_color + "}" + text
        dialogues.append(
            f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Default,,0,0,0,,{text}"
        )
    return header + "\n".join(dialogues) + "\n"


def parse_srt(content: str) -> list[dict]:
    parts = re.split(r"\n\s*\n", content.strip())
    segments = []
    pattern = re.compile(
        r"(?:(\d+)\s*\n)?(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
        r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n(.*)", re.S
    )

    def to_seconds(timestamp):
        hours, minutes, remainder = timestamp.split(":")
        seconds, milliseconds = re.split(r"[:,.]", remainder)
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000

    for part in parts:
        match = pattern.fullmatch(part.strip())
        if match:
            segments.append({
                "start": to_seconds(match.group(2)),
                "end": to_seconds(match.group(3)),
                "text": match.group(4).strip().replace("\n", " "),
            })
    return segments
