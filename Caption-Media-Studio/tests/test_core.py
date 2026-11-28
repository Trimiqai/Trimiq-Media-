import io
import hashlib
import json
import tempfile
import uuid
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from werkzeug.datastructures import FileStorage

import config
from modules.correction_engine import correct_segments, correct_text
from modules.file_store import FileValidationError, owned_path, validate_upload
from modules.subtitle_export import export_ass, export_subtitles, format_segments_for_lines, parse_srt
from modules.text_animation import build_drawtext_filters
from modules import ffmpeg_tools, video_tools, whisper_cpp
from modules.memory_guard import ensure_model_memory


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


class WhisperCppTests(unittest.TestCase):
    def test_parses_segment_timestamps_and_detected_language(self):
        segments, language = whisper_cpp.parse_transcription({
            "result": {"language": "hi"},
            "transcription": [{
                "offsets": {"from": 1250, "to": 2400},
                "text": " नमस्ते ",
            }],
        })
        self.assertEqual(language, "hi")
        self.assertEqual(segments, [{
            "start": 1.25,
            "end": 2.4,
            "text": "नमस्ते",
            "original_text": "नमस्ते",
            "words": [],
        }])

    def test_rejects_missing_transcription_data(self):
        with self.assertRaises(ValueError):
            whisper_cpp.parse_transcription({})

    def test_model_checksum_is_verified_and_existing_model_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ggml-tiny.bin"
            content = b"lmgg" + b"verified tiny model test bytes"
            path.write_bytes(content)
            with (
                patch.object(whisper_cpp, "MODEL_PATH", path),
                patch.object(whisper_cpp, "TINY_MODEL_MIN_BYTES", 4),
                patch.object(whisper_cpp, "TINY_MODEL_MAX_BYTES", 1024),
                patch.object(whisper_cpp, "TINY_MODEL_SHA1", hashlib.sha1(content).hexdigest()),
                patch.object(whisper_cpp.urllib.request, "urlopen") as urlopen,
            ):
                self.assertTrue(whisper_cpp._model_is_valid())
                result = whisper_cpp.download_tiny_model(Mock())
                self.assertTrue(result["already_present"])
                urlopen.assert_not_called()
                path.write_bytes(content + b"tampered")
                self.assertFalse(whisper_cpp._model_is_valid())

    def test_model_download_verifies_checksum_before_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ggml-tiny.bin"
            model_bytes = b"lmgg" + b"verified downloaded model"
            content = model_bytes

            class Response:
                headers = {"Content-Length": str(len(content))}

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return None

                def read(self, _size):
                    nonlocal content
                    result, content = content, b""
                    return result

            with (
                patch.object(whisper_cpp, "MODEL_PATH", path),
                patch.object(whisper_cpp, "TINY_MODEL_MIN_BYTES", 4),
                patch.object(whisper_cpp, "TINY_MODEL_MAX_BYTES", 1024),
                patch.object(whisper_cpp, "TINY_MODEL_SHA1", hashlib.sha1(model_bytes).hexdigest()),
                patch.object(whisper_cpp.urllib.request, "urlopen", return_value=Response()),
            ):
                result = whisper_cpp.download_tiny_model(Mock())
                self.assertEqual(result["size"], len(model_bytes))
                self.assertTrue(whisper_cpp._model_is_valid())
                self.assertEqual(list(path.parent.glob("*.part")), [])

    def test_tiny_transcription_uses_cli_and_saves_parsed_segments(self):
        fake_result = {
            "result": {"language": "en"},
            "transcription": [{
                "offsets": {"from": 0, "to": 1000},
                "text": " Hello.",
            }],
        }

        def fake_ffmpeg(arguments):
            Path(arguments[-1]).write_bytes(b"wav")

        def fake_popen(command, **_kwargs):
            output_prefix = Path(command[command.index("-of") + 1])
            Path(f"{output_prefix}.json").write_text(json.dumps(fake_result), encoding="utf-8")
            return Mock(poll=Mock(return_value=0), returncode=0)

        with (
            patch.object(whisper_cpp, "_find_cli", return_value="/usr/bin/whisper-cli"),
            patch.object(whisper_cpp, "_model_is_valid", return_value=True),
            patch.object(whisper_cpp, "run_ffmpeg", side_effect=fake_ffmpeg),
            patch.object(whisper_cpp.subprocess, "Popen", side_effect=fake_popen) as popen,
        ):
            transcript = whisper_cpp.transcribe_tiny(Mock(), "/tmp/source.mp4", correct=False)

        self.assertEqual(transcript["engine"], "whisper.cpp")
        self.assertFalse(transcript["word_timestamps_available"])
        self.assertEqual(transcript["segments"][0]["text"], "Hello.")
        command = popen.call_args.args[0]
        self.assertIn("auto", command)
        self.assertIn("-m", command)
        Path(config.OUTPUT_DIR, f"{transcript['id']}.json").unlink(missing_ok=True)


class MemoryGuardTests(unittest.TestCase):
    def test_low_available_memory_is_advisory_for_tiny_model(self):
        with patch("modules.memory_guard.memory_status", return_value={
            "available_mb": 512,
            "total_mb": 4096,
            "source": "test",
        }):
            status = ensure_model_memory("tiny")
        self.assertEqual(status["available_mb"], 512)
        self.assertIsNotNone(status["warning"])


class AndroidVideoExportTests(unittest.TestCase):
    @staticmethod
    def media(codec="h264", pixel_format="yuv420p", audio_codec=None, duration=10):
        streams = [{
            "type": "video",
            "codec": codec,
            "pixel_format": pixel_format,
            "width": 1920,
            "height": 1080,
            "start_time": 0.0,
            "duration": float(duration),
        }]
        if audio_codec:
            streams.append({
                "type": "audio",
                "codec": audio_codec,
                "start_time": 0.0,
                "duration": float(duration),
            })
        return {
            "format": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": float(duration),
            "size": 5000,
            "video": True,
            "audio": bool(audio_codec),
            "streams": streams,
        }

    def _render_with_probe_results(self, source, output):
        def fake_ffmpeg(arguments):
            Path(arguments[-1]).write_bytes(b"verified test output")

        with (
            patch.object(video_tools, "media_info", side_effect=[source, output]),
            patch.object(video_tools, "require_encoder") as require_encoder,
            patch.object(video_tools, "run_ffmpeg", side_effect=fake_ffmpeg) as run_ffmpeg,
        ):
            result = video_tools.render_video(Mock(), "/uploads/source.mov", [], [], False, {})
        self.addCleanup(lambda: Path(config.OUTPUT_DIR, result["filename"]).unlink(missing_ok=True))
        return result, require_encoder, run_ffmpeg

    def test_export_reencodes_android_codecs_and_converts_source_audio_to_aac(self):
        result, require_encoder, run_ffmpeg = self._render_with_probe_results(
            self.media(codec="hevc", audio_codec="opus"),
            self.media(codec="h264", audio_codec="aac", duration=10.1),
        )
        command = run_ffmpeg.call_args.args[0]
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("+faststart", command)
        self.assertEqual(command[command.index("-c:a") + 1], "aac")
        self.assertEqual(
            [call.args[0] for call in require_encoder.call_args_list],
            ["libx264", "aac"],
        )
        self.assertEqual(result["verified"]["audio_codec"], "aac")
        self.assertTrue(result["verified"]["width"])

    def test_export_without_audio_remuxes_compatible_video_without_fake_audio(self):
        result, require_encoder, run_ffmpeg = self._render_with_probe_results(
            self.media(audio_codec=None),
            self.media(audio_codec=None),
        )
        command = run_ffmpeg.call_args.args[0]
        self.assertIn("copy", command)
        self.assertNotIn("-c:a", command)
        require_encoder.assert_not_called()
        self.assertIsNone(result["verified"]["audio_codec"])

    def test_invalid_render_is_not_published_to_outputs(self):
        source = self.media()
        output = self.media(pixel_format="yuv444p")
        before = set(Path(config.OUTPUT_DIR).glob("*.mp4"))
        with (
            patch.object(video_tools, "media_info", side_effect=[source, output]),
            patch.object(video_tools, "run_ffmpeg", side_effect=lambda arguments: Path(arguments[-1]).write_bytes(b"bad")),
        ):
            with self.assertRaisesRegex(RuntimeError, "yuv420p"):
                video_tools.render_video(Mock(), "/uploads/source.mp4", [], [], False, {})
        self.assertEqual(set(Path(config.OUTPUT_DIR).glob("*.mp4")), before)

    def test_ffmpeg_encoder_detection_requires_requested_encoder(self):
        ffmpeg_tools._available_encoders.cache_clear()
        encoder_output = (
            "Encoders:\n"
            " V....D libx264 H.264 / AVC / MPEG-4 AVC\n"
            " A..... aac AAC (Advanced Audio Coding)\n"
        )
        with (
            patch.object(ffmpeg_tools, "require_ffmpeg", return_value="/usr/bin/ffmpeg"),
            patch.object(ffmpeg_tools.subprocess, "run", return_value=Mock(
                returncode=0,
                stdout=encoder_output,
                stderr="",
            )),
        ):
            ffmpeg_tools.require_encoder("libx264")
            ffmpeg_tools.require_encoder("aac")
            with self.assertRaisesRegex(ffmpeg_tools.MediaToolError, "does not provide"):
                ffmpeg_tools.require_encoder("missing_encoder")
        ffmpeg_tools._available_encoders.cache_clear()


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
