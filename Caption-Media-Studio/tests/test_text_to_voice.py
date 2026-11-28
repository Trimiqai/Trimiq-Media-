import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import config
from modules import text_to_voice


class NeuralVoiceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.old_cache = text_to_voice._VOICE_CACHE
        self.old_cache_time = text_to_voice._VOICE_CACHE_TIME

    def tearDown(self):
        text_to_voice._VOICE_CACHE = self.old_cache
        text_to_voice._VOICE_CACHE_TIME = self.old_cache_time

    def test_only_verified_neural_voices_are_listed(self):
        voices = [
            {"ShortName": "en-US-AriaNeural", "Locale": "en-US", "Name": "Aria"},
            {"ShortName": "en-US-OldVoice", "Locale": "en-US", "Name": "Old"},
            {"ShortName": "en-US-CloudNeural", "Locale": "en-US", "Name": "Cloud",
             "VoiceType": "Cloud"},
            {"ShortName": "en-US-Explicit", "Locale": "en-US", "Name": "Explicit",
             "VoiceType": "Neural"},
            {"ShortName": "en-US-DeprecatedNeural", "Locale": "en-US",
             "Name": "Deprecated Neural", "Status": "Deprecated"},
        ]
        text_to_voice._VOICE_CACHE = voices
        text_to_voice._VOICE_CACHE_TIME = 100
        with patch.object(text_to_voice.time, "monotonic", return_value=101), \
                patch.object(text_to_voice, "_fetch_edge_voices") as fetch:
            catalog = text_to_voice.list_neural_voices()
        self.assertEqual(
            [voice["short_name"] for voice in catalog["voices"]],
            ["en-US-AriaNeural", "en-US-Explicit"],
        )
        self.assertEqual(catalog["languages"], [{"locale": "en-US", "language": "en"}])
        fetch.assert_not_called()

    def test_failed_refresh_uses_cached_neural_catalog_and_marks_it_stale(self):
        text_to_voice._VOICE_CACHE = [{
            "ShortName": "hi-IN-SwaraNeural",
            "Locale": "hi-IN",
            "Name": "Swara",
        }]

        async def fail_fetch():
            raise OSError("offline")

        with patch.object(text_to_voice, "_fetch_edge_voices", side_effect=fail_fetch):
            catalog = text_to_voice.list_neural_voices(force_refresh=True)
        self.assertTrue(catalog["stale"])
        self.assertIn("cached", catalog["message"])
        self.assertEqual(catalog["voices"][0]["short_name"], "hi-IN-SwaraNeural")

    def test_voice_selection_never_crosses_requested_language(self):
        voices = [
            {"short_name": "en-US-AriaNeural", "locale": "en-US", "language": "en"},
            {"short_name": "hi-IN-SwaraNeural", "locale": "hi-IN", "language": "hi"},
        ]
        with self.assertRaisesRegex(ValueError, "does not match"):
            text_to_voice._voice_candidates(
                voices, "en-US-AriaNeural", "hi-IN", "नमस्ते"
            )
        with self.assertRaisesRegex(RuntimeError, "No verified Neural voice"):
            text_to_voice._voice_candidates(voices, "auto", "ur-PK", "السلام علیکم")

    def test_auto_language_does_not_substitute_english_for_unsupported_script(self):
        voices = [
            {"short_name": "en-US-AriaNeural", "locale": "en-US", "language": "en"},
        ]
        self.assertEqual(text_to_voice._automatic_language("नमस्ते", {"en"}), "hi")
        with self.assertRaisesRegex(RuntimeError, "language/locale 'hi'"):
            text_to_voice._voice_candidates(voices, "auto", "auto", "नमस्ते")

    def test_synthesis_options_are_validated_before_network_or_catalog_access(self):
        context = Mock()
        with patch.object(text_to_voice, "list_neural_voices") as catalog:
            with self.assertRaisesRegex(ValueError, "1-5000"):
                text_to_voice.synthesize(context, "  ")
            with self.assertRaisesRegex(ValueError, "Rate"):
                text_to_voice.synthesize(context, "Hello", rate="+60%")
            with self.assertRaisesRegex(ValueError, "Volume"):
                text_to_voice.synthesize(context, "Hello", volume="+60%")
            with self.assertRaisesRegex(ValueError, "Pitch"):
                text_to_voice.synthesize(context, "Hello", pitch="+60Hz")
        catalog.assert_not_called()
        context.progress.assert_not_called()

    def test_unavailable_preferred_voice_falls_back_only_to_neural_voice(self):
        voices = [
            {"short_name": "en-US-AriaNeural", "display_name": "Aria", "locale": "en-US",
             "language": "en"},
            {"short_name": "en-US-GuyNeural", "display_name": "Guy", "locale": "en-US",
             "language": "en"},
        ]
        attempted = []

        class FakeCommunicate:
            def __init__(self, _text, voice, **_kwargs):
                self.voice = voice
                attempted.append(voice)

            async def save(self, path):
                if self.voice == "en-US-AriaNeural":
                    raise RuntimeError("Voice not found")
                Path(path).write_bytes(b"ID3" + b"\x00" * 32)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "out").mkdir()
            (root / "temp").mkdir()
            with (
                patch.object(text_to_voice, "list_neural_voices", return_value={
                    "voices": voices, "stale": False, "message": "ok",
                }),
                patch.object(config, "OUTPUT_DIR", root / "out"),
                patch.object(config, "TEMP_DIR", root / "temp"),
                patch.dict(sys.modules, {"edge_tts": SimpleNamespace(Communicate=FakeCommunicate)}),
            ):
                result = text_to_voice.synthesize(
                    Mock(), "Hello", "en-US-AriaNeural", language="en-US"
                )
            self.assertEqual(attempted, ["en-US-AriaNeural", "en-US-GuyNeural"])
            self.assertEqual(result["voice"], "en-US-GuyNeural")
            self.assertTrue(result["voice_fallback"])
            self.assertTrue((root / "out" / result["filename"]).is_file())
            self.assertEqual(list((root / "temp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
