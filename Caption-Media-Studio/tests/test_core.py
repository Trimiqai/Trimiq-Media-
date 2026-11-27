import io
import uuid
import unittest
from pathlib import Path

from werkzeug.datastructures import FileStorage

import config
from modules.correction_engine import correct_segments, correct_text
from modules.file_store import FileValidationError, owned_path, validate_upload
from modules.subtitle_export import export_ass, export_subtitles, format_segments_for_lines, parse_srt
from modules.text_animation import build_drawtext_filters


class CorrectionTests(unittest.TestCase):
    def test_corrects_common_errors_without_transliterating_hinglish(self):
        self.assertEqual(correct_text("teh game dont stop"), "The game don't stop.")
        self.assertEqual(correct_text("Bhai ye game hai"), "Bhai ye game hai.")
        self.assertEqual(correct_text("नही जाना"), "नहीं जाना.")
        self.assertEqual(correct_text("teh game", enabled=False), "teh game")

    def test_segment_original_and_timestamps_are_preserved(self):
        result = correct_segments([{"start": 1.25, "end": 2.5, "text": "teh match"}])[0]
        self.assertEqual(result["original_text"], "teh match")
        self.assertEqual(result["text"], "The match.")
        self.assertEqual((result["start"], result["end"]), (1.25, 2.5))


class SubtitleTests(unittest.TestCase):
    def setUp(self):
        self.segments = [{
            "start": 1.25,
            "end": 2.5,
            "text": "Hello world",
            "original_text": "Hello world",
            "words": [
                {"start": 1.25, "end": 1.8, "word": "Hello"},
                {"start": 1.8, "end": 2.5, "word": "world"},
            ],
        }]

    def test_srt_vtt_txt_and_word_level_exports(self):
        srt = export_subtitles(self.segments, "srt")
        self.assertIn("00:00:01,250 --> 00:00:02,500", srt)
        self.assertIn("Hello world", srt)
        vtt = export_subtitles(self.segments, "vtt", word_level=True)
        self.assertTrue(vtt.startswith("WEBVTT\n\n"))
        self.assertIn("00:00:01.250 --> 00:00:01.800", vtt)
        self.assertEqual(export_subtitles(self.segments, "txt"), "Hello world\n")
        edited = [{**self.segments[0], "text": "Good morning"}]
        self.assertIn(
            "Good morning",
            export_subtitles(edited, "srt", word_level=True),
        )
        self.assertNotIn(
            "Hello",
            export_subtitles(edited, "srt", word_level=True),
        )
        self.assertEqual(len(parse_srt(srt)), 1)

    def test_ass_karaoke_and_style_are_validated(self):
        ass = export_ass(self.segments, {
            "font": "Arial",
            "font_size": 30,
            "font_color": "#ffffff",
            "highlight_color": "#a891ff",
            "active_word_highlight": True,
        })
        self.assertIn("[Events]", ass)
        self.assertIn(r"{\kf55}Hello", ass)
        with self.assertRaises(ValueError):
            export_ass(self.segments, {"font": "Arial,Name:Injected"})

    def test_line_limits_split_long_cues_without_dropping_dialogue(self):
        source = [{
            "start": 0,
            "end": 9,
            "text": "one two three four five six seven eight nine",
            "original_text": "one two three four five six seven eight nine",
        }]
        result = format_segments_for_lines(source, max_words=2, max_lines=2)
        self.assertEqual(len(result), 3)
        self.assertEqual(
            " ".join(item["text"].replace("\n", " ") for item in result),
            source[0]["text"],
        )
        self.assertEqual((result[0]["start"], result[-1]["end"]), (0, 9))
        self.assertTrue(all(len(item["text"].splitlines()) <= 2 for item in result))


class FileValidationTests(unittest.TestCase):
    def test_validates_extension_mime_and_magic_bytes(self):
        valid = FileStorage(
            stream=io.BytesIO(b"ID3" + b"\x00" * 10),
            filename="voice.mp3",
            content_type="audio/mpeg",
        )
        self.assertEqual(validate_upload(valid), ("voice.mp3", ".mp3"))
        invalid_mime = FileStorage(
            stream=io.BytesIO(b"ID3" + b"\x00" * 10),
            filename="voice.mp3",
            content_type="text/plain",
        )
        with self.assertRaises(FileValidationError):
            validate_upload(invalid_mime)
        invalid_content = FileStorage(
            stream=io.BytesIO(b"not audio"),
            filename="voice.mp3",
            content_type="audio/mpeg",
        )
        with self.assertRaises(FileValidationError):
            validate_upload(invalid_content)

    def test_storage_ids_cannot_escape_directory(self):
        with self.assertRaises(FileValidationError):
            owned_path(config.OUTPUT_DIR, "../secrets")


class TextAnimationTests(unittest.TestCase):
    def test_all_presets_produce_filters_and_keep_user_text_out_of_filter_syntax(self):
        presets = [
            "none", "fade", "slide_left", "slide_right", "slide_up", "slide_down",
            "pop", "zoom", "zoom_out", "fade_in", "fade_out", "fade_in_out",
            "bounce", "typewriter", "word_by_word",
            "letter_by_letter", "karaoke", "shake", "pulse", "glow", "scale",
        ]
        job_id = uuid.uuid4().hex
        try:
            for preset in presets:
                with self.subTest(preset=preset):
                    filters = build_drawtext_filters([{
                        "text": "Hello: 'world'",
                        "animation": preset,
                        "font": "Sans",
                        "start": 0,
                        "end": 5,
                        "animation_duration": .5,
                    }], job_id)
                    self.assertTrue(filters)
                    self.assertTrue(all(item.startswith("drawtext=") for item in filters))
                    self.assertNotIn("Hello: 'world'", ",".join(filters))
        finally:
            for path in config.TEMP_DIR.glob(f"{job_id}*"):
                path.unlink(missing_ok=True)

    def test_rejects_invalid_font_and_unbounded_layers(self):
        with self.assertRaises(ValueError):
            build_drawtext_filters([{"text": "ok", "font": "bad:font"}], uuid.uuid4().hex)
        with self.assertRaises(ValueError):
            build_drawtext_filters([{}] * 21, uuid.uuid4().hex)


if __name__ == "__main__":
    unittest.main()
