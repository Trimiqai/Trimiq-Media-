"""Validated text-layer options and FFmpeg drawtext generation."""

import re
import math
import unicodedata
from pathlib import Path

import config

SAFE_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
SAFE_FONT = re.compile(r"^[\w -]{1,60}$")
PRESET_NAMES = {
    "none", "fade", "fade_in", "fade_out", "fade_in_out",
    "slide_left", "slide_right", "slide_up", "slide_down",
    "pop", "zoom", "zoom_out", "bounce", "typewriter", "word_by_word",
    "letter_by_letter", "karaoke", "shake", "pulse", "glow", "scale",
}


def _drawtext_path(path: Path) -> str:
    return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _tracked_text(text: str, amount: int) -> str:
    if amount <= 0:
        return text
    clusters = []
    for character in text:
        if clusters and (
            unicodedata.category(character).startswith("M")
            or character == "\u200d"
            or clusters[-1].endswith("\u200d")
        ):
            clusters[-1] += character
        else:
            clusters.append(character)
    spacer = "\u200a" * max(1, round(amount / 3))
    return spacer.join(clusters)


def _validate_layer(layer: dict, index: int):
    if not isinstance(layer, dict):
        raise ValueError(f"Text layer {index + 1} must be an object.")
    text = str(layer.get("text", ""))
    if not text.strip() or len(text) > 500:
        raise ValueError(f"Text layer {index + 1} must contain 1-500 characters.")
    start = float(layer.get("start", 0))
    end = float(layer.get("end", 10))
    duration = float(layer.get("animation_duration", 0.5))
    if not all(map(math.isfinite, (start, end, duration))) or start < 0 or end <= start or end > 24 * 60 * 60 or not 0.05 <= duration <= 10:
        raise ValueError(f"Text layer {index + 1} has invalid timing.")
    x, y = float(layer.get("x", 0.5)), float(layer.get("y", 0.5))
    if not all(map(math.isfinite, (x, y))) or not 0 <= x <= 1 or not 0 <= y <= 1:
        raise ValueError(f"Text layer {index + 1} position must be between 0 and 1.")
    color = str(layer.get("color", "#ffffff"))
    if not SAFE_COLOR.fullmatch(color):
        raise ValueError(f"Text layer {index + 1} has an invalid text color.")
    highlight_color = str(layer.get("highlight_color", "#ffe36e"))
    if not SAFE_COLOR.fullmatch(highlight_color):
        raise ValueError(f"Text layer {index + 1} has an invalid highlight color.")
    font = str(layer.get("font", "Sans"))
    if not SAFE_FONT.fullmatch(font):
        raise ValueError(f"Text layer {index + 1} has an invalid font name.")
    animation = str(layer.get("animation", "none"))
    if animation not in PRESET_NAMES:
        raise ValueError(f"Text layer {index + 1} uses an unsupported animation.")
    opacity = float(layer.get("opacity", 100))
    if not math.isfinite(opacity) or not 0 <= opacity <= 100:
        raise ValueError(f"Text layer {index + 1} opacity must be between 0 and 100.")
    return text, start, end, duration, x, y, color, highlight_color, font, animation, opacity


def _text_filter(text: str, text_path: Path, *, font: str, fontsize: int,
                 color: str, x_expr: str, y_expr: str, start: float, end: float,
                 alpha: str, outline: int, bold: bool, background: bool,
                 italic: bool = False, letter_spacing: int = 0,
                 line_spacing: int = 0, shadow: int = 0,
                 glow: bool = False) -> str:
    text_path.write_text(_tracked_text(text, letter_spacing), encoding="utf-8")
    font_style = "Bold Italic" if bold and italic else "Bold" if bold else "Italic" if italic else "Regular"
    options = [
        f"textfile='{_drawtext_path(text_path)}'",
        f"font='{font}:style={font_style}'",
        f"fontsize='{fontsize}'",
        f"fontcolor={color}",
        f"x='{x_expr}'",
        f"y='{y_expr}'",
        f"alpha='{alpha}'",
        f"line_spacing={max(-50, min(100, line_spacing))}",
        f"enable='between(t,{start:.3f},{end:.3f})'",
        "expansion=none",
    ]
    if bold:
        outline = max(outline, 1)
    if outline:
        options.extend([f"borderw={outline}", "bordercolor=black"])
    if background:
        options.extend(["box=1", "boxcolor=black@0.55", "boxborderw=10"])
    if shadow or glow:
        shadow_amount = max(0, min(12, shadow))
        options.extend([
            f"shadowcolor={'white' if glow else 'black'}@0.8",
            f"shadowx={0 if glow else shadow_amount}",
            f"shadowy={0 if glow else shadow_amount}",
        ])
    return "drawtext=" + ":".join(options)


def _progressive_text_filters(text: str, *, animation: str, text_path: Path,
                              fontsize: int, color: str, x_expr: str, y_expr: str,
                              start: float, end: float, duration: float, font: str,
                              outline: int, bold: bool, background: bool,
                              italic: bool, letter_spacing: int,
                              line_spacing: int, shadow: int) -> list[str]:
    if len(text) > 120:
        raise ValueError(
            "Typewriter and progressive text animations are limited to 120 characters per layer."
        )
    chunks = []
    if animation == "letter_by_letter" or animation == "typewriter":
        chunks = [text[:index] for index in range(1, len(text) + 1)]
    else:
        words = text.split()
        chunks = [" ".join(words[:index]) for index in range(1, len(words) + 1)]
    reveal_time = min(duration, max(0.1, end - start))
    step = reveal_time / max(1, len(chunks))
    filters = []
    for index, chunk in enumerate(chunks):
        visible_from = start + step * index
        visible_until = start + step * (index + 1)
        if index == len(chunks) - 1:
            visible_until = end
        path = text_path.with_name(f"{text_path.stem}-step-{index}.txt")
        filters.append(_text_filter(
            chunk, path, font=font, fontsize=fontsize, color=color,
            x_expr=x_expr, y_expr=y_expr, start=visible_from, end=visible_until,
            alpha="1", outline=outline, bold=bold, background=background,
            italic=italic, letter_spacing=letter_spacing,
            line_spacing=line_spacing, shadow=shadow,
        ))
    return filters


def _karaoke_filters(text: str, *, text_path: Path, fontsize: int, color: str,
                     highlight_color: str, x: float, y_expr: str, start: float,
                     end: float, font: str, outline: int, bold: bool,
                     italic: bool, letter_spacing: int, line_spacing: int,
                     shadow: int, alignment: str) -> list[str]:
    words = text.split()
    if not words:
        return []
    duration = end - start
    approximate_width = len(text) * fontsize * 0.58
    anchor = {"left": 0.0, "center": 0.5, "right": 1.0}.get(alignment, 0.5)
    prefix_length = 0
    filters = []
    for index, word in enumerate(words):
        word_width = len(word) * fontsize * 0.58
        x_expr = f"(w-{approximate_width:.2f})*{x:.4f}+{prefix_length * fontsize * 0.58:.2f}+{(0.5-anchor)*(approximate_width-word_width):.2f}"
        word_start = start + duration * index / len(words)
        word_end = start + duration * (index + 1) / len(words)
        path = text_path.with_name(f"{text_path.stem}-word-{index}.txt")
        filters.append(_text_filter(
            word, path, font=font, fontsize=fontsize, color=highlight_color,
            x_expr=x_expr, y_expr=y_expr, start=word_start, end=word_end,
            alpha="1", outline=outline, bold=bold, background=False,
            italic=italic, letter_spacing=letter_spacing,
            line_spacing=line_spacing, shadow=shadow,
        ))
        prefix_length += len(word) + 1
    return filters


def build_drawtext_filters(layers: list[dict], job_id: str) -> list[str]:
    if not isinstance(layers, list) or len(layers) > 20:
        raise ValueError("A maximum of 20 text layers can be rendered at once.")
    filters = []
    for index, layer in enumerate(layers):
        (text, start, end, duration, x, y, color, highlight_color, font,
         animation, opacity) = _validate_layer(layer, index)
        duration = min(duration, end - start)
        text_path = config.TEMP_DIR / f"{job_id}-layer-{index}.txt"
        fontsize = max(8, min(220, int(layer.get("font_size", 48))))
        alignment = str(layer.get("alignment", "center"))
        if alignment not in {"left", "center", "right"}:
            raise ValueError(f"Text layer {index + 1} has invalid alignment.")
        anchor = {"left": 0, "center": .5, "right": 1}[alignment]
        x_expr = f"w*{x:.4f}-text_w*{anchor:.2f}"
        y_expr = f"h*{y:.4f}-text_h*0.5"
        alpha = f"{opacity / 100:.3f}"
        outline = max(0, min(12, int(layer.get("outline", 2))))
        bold = bool(layer.get("bold"))
        italic = bool(layer.get("italic"))
        background = bool(layer.get("background"))
        letter_spacing = int(layer.get("letter_spacing", 0))
        line_spacing = round((float(layer.get("line_spacing", 1.2)) - 1) * fontsize)
        shadow = max(0, min(12, int(layer.get("shadow", 4))))
        glow = bool(layer.get("glow"))
        if not 0 <= letter_spacing <= 30 or not .8 <= float(layer.get("line_spacing", 1.2)) <= 2:
            raise ValueError(f"Text layer {index + 1} has invalid spacing values.")
        anim_start = start
        anim_end = min(end, start + duration)

        if animation in {"typewriter", "letter_by_letter", "word_by_word"}:
            filters.extend(_progressive_text_filters(
                text, animation=animation, text_path=text_path, fontsize=fontsize,
                color=color, x_expr=x_expr, y_expr=y_expr, start=start, end=end,
                duration=duration, font=font, outline=outline, bold=bold,
                background=background, italic=italic,
                letter_spacing=letter_spacing, line_spacing=line_spacing,
                shadow=shadow,
            ))
            continue
        if animation == "karaoke":
            filters.append(_text_filter(
                text, text_path, font=font, fontsize=fontsize, color=color,
                x_expr=x_expr, y_expr=y_expr, start=start, end=end, alpha=alpha,
                outline=outline, bold=bold, background=background,
                italic=italic, letter_spacing=letter_spacing,
                line_spacing=line_spacing, shadow=shadow,
            ))
            filters.extend(_karaoke_filters(
                text, text_path=text_path, fontsize=fontsize, color=color,
                highlight_color=highlight_color, x=x, y_expr=y_expr, start=start,
                end=end, font=font, outline=outline, bold=bold,
                italic=italic, letter_spacing=letter_spacing,
                line_spacing=line_spacing, shadow=shadow, alignment=alignment,
            ))
            continue

        if animation in {"fade", "fade_in_out"}:
            fade_in_end = min(end, start + duration)
            fade_out_start = max(fade_in_end, end - duration)
            alpha = (
                f"if(lt(t\\,{anim_start:.3f})\\,0\\,"
                f"if(lt(t\\,{fade_in_end:.3f})\\,(t-{anim_start:.3f})/{duration:.3f}*{opacity / 100:.3f}\\,"
                f"if(lt(t\\,{fade_out_start:.3f})\\,{opacity / 100:.3f}\\,"
                f"if(lt(t\\,{end:.3f})\\,(1-(t-{fade_out_start:.3f})/{max(.001, end-fade_out_start):.3f})*{opacity / 100:.3f}\\,0))))"
            )
        elif animation == "fade_in":
            alpha = (
                f"if(lt(t\\,{anim_start:.3f})\\,0\\,"
                f"if(lt(t\\,{anim_end:.3f})\\,(t-{anim_start:.3f})/{duration:.3f}*{opacity / 100:.3f}\\,{opacity / 100:.3f}))"
            )
        elif animation == "fade_out":
            fade_from = max(start, end - duration)
            alpha = (
                f"if(lt(t\\,{fade_from:.3f})\\,{alpha}\\,"
                f"if(lt(t\\,{end:.3f})\\,(1-(t-{fade_from:.3f})/{duration:.3f})*{opacity / 100:.3f}\\,0))"
            )
        if animation == "slide_left":
            x_expr = f"if(lt(t\\,{anim_end:.3f})\\,w-(t-{start:.3f})/{duration:.3f}*(w-{x_expr})\\,{x_expr})"
        elif animation == "slide_right":
            x_expr = f"if(lt(t\\,{anim_end:.3f})\\,-text_w+(t-{start:.3f})/{duration:.3f}*({x_expr}+text_w)\\,{x_expr})"
        elif animation == "slide_up":
            y_expr = f"if(lt(t\\,{anim_end:.3f})\\,h-(t-{start:.3f})/{duration:.3f}*(h-{y_expr})\\,{y_expr})"
        elif animation == "slide_down":
            y_expr = f"if(lt(t\\,{anim_end:.3f})\\,-text_h+(t-{start:.3f})/{duration:.3f}*({y_expr}+text_h)\\,{y_expr})"
        elif animation == "bounce":
            y_expr = (
                f"{y_expr}-abs(sin((t-{start:.3f})*12))*25*"
                f"if(lt(t\\,{anim_end:.3f})\\,1-(t-{start:.3f})/{duration:.3f}\\,0)"
            )
        elif animation == "shake":
            x_expr = f"{x_expr}+4*sin((t-{start:.3f})*35)"

        size_expr = str(fontsize)
        if animation in {"zoom", "scale", "pop"}:
            size_expr = (
                f"if(lt(t\\,{start:.3f})\\,{max(8, fontsize // 4)}\\,"
                f"if(lt(t\\,{anim_end:.3f})\\,{fontsize}*(0.25+0.75*(t-{start:.3f})/{duration:.3f})\\,{fontsize}))"
            )
        elif animation == "zoom_out":
            size_expr = (
                f"if(lt(t\\,{start:.3f})\\,{fontsize}\\,"
                f"if(lt(t\\,{anim_end:.3f})\\,{fontsize}*(1-0.75*(t-{start:.3f})/{duration:.3f})\\,{max(8, fontsize // 4)}))"
            )
        elif animation == "pulse":
            size_expr = f"{fontsize}*(1+0.08*sin(6*t))"

        filters.append(_text_filter(
            text, text_path, font=font, fontsize=size_expr, color=color,
            x_expr=x_expr, y_expr=y_expr, start=start, end=end, alpha=alpha,
            outline=outline, bold=bold, background=background,
            italic=italic, letter_spacing=letter_spacing,
            line_spacing=line_spacing, shadow=shadow,
            glow=glow or animation == "glow",
        ))
    return filters
