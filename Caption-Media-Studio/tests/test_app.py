import io
import unittest
from unittest.mock import patch

import app as app_module


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=True)
        self.client = app_module.app.test_client()

    def test_dashboard_and_health_endpoint(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["queue_worker_count"], 1)
        self.assertIn("whisper_cpp_cli", response.json)
        self.assertIn("whisper_cpp_tiny_model", response.json)

    def test_whisper_cpp_model_status_endpoint(self):
        response = self.client.get("/api/whisper-cpp/model")
        self.assertEqual(response.status_code, 200)
        self.assertIn("model_valid", response.json)
        self.assertIn("cli_available", response.json)
        self.assertIn("message", response.json)

    def test_tts_voice_catalog_uses_dynamic_neural_catalog(self):
        catalog = {
            "voices": [{"short_name": "hi-IN-SwaraNeural"}],
            "languages": [{"locale": "hi-IN", "language": "hi"}],
            "stale": False,
            "message": "1 Neural voice available.",
        }
        with patch.object(app_module.text_to_voice, "list_neural_voices", return_value=catalog) as listing:
            response = self.client.get("/api/tts/voices?refresh=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, catalog)
        listing.assert_called_once_with(force_refresh=True)

    def test_tts_voice_catalog_reports_network_failure(self):
        with patch.object(
            app_module.text_to_voice,
            "list_neural_voices",
            side_effect=RuntimeError("Could not retrieve voice list."),
        ):
            response = self.client.get("/api/tts/voices")
        self.assertEqual(response.status_code, 503)
        self.assertIn("Could not retrieve", response.json["error"])
        self.assertEqual(response.json["voices"], [])

    def test_tts_route_queues_neural_voice_options_and_rejects_bad_locale(self):
        with patch.object(app_module, "_queue", return_value=({"job_id": "test"}, 202)) as queue:
            response = self.client.post("/api/tts", json={
                "text": "Hello",
                "voice": "en-US-AriaNeural",
                "language": "en-US",
                "rate": "+0%",
                "pitch": "+0Hz",
                "volume": "+10%",
            })
        self.assertEqual(response.status_code, 202)
        self.assertIs(queue.call_args.args[1], app_module.text_to_voice.synthesize)
        self.assertEqual(queue.call_args.args[2:], (
            "Hello", "en-US-AriaNeural", "+0%", "+0Hz", "en-US", "+10%",
        ))

        rejected = self.client.post("/api/tts", json={
            "text": "Hello",
            "language": "../../other",
        })
        self.assertEqual(rejected.status_code, 400)

    def test_whisper_cpp_model_download_is_queued(self):
        with patch.object(app_module, "_queue", return_value=({"job_id": "test"}, 202)) as queue:
            response = self.client.post("/api/whisper-cpp/model/download")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(queue.call_args.args[0], "Whisper.cpp Tiny model download")
        self.assertIs(queue.call_args.args[1], app_module.whisper_cpp.download_tiny_model)

    def test_video_render_accepts_export_without_layers_or_subtitles(self):
        uploaded = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"\x00\x00\x00\x18ftypisom" + b"\x00" * 8), "clip.mp4", "video/mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(uploaded.status_code, 201)
        file_id = uploaded.json["file_id"]
        try:
            with patch.object(app_module, "_queue", return_value=({"job_id": "test"}, 202)) as queue:
                response = self.client.post("/api/video/render", json={
                    "file_id": file_id,
                    "layers": [],
                    "segments": [],
                    "burn_subtitles": False,
                })
            self.assertEqual(response.status_code, 202)
            self.assertEqual(queue.call_args.args[0], "video render")
            self.assertEqual(queue.call_args.args[3], [])
            self.assertEqual(queue.call_args.args[4], [])
            self.assertFalse(queue.call_args.args[5])
        finally:
            app_module.config.UPLOAD_DIR.joinpath(f"{file_id}.mp4").unlink(missing_ok=True)

    def test_cross_origin_mutations_are_rejected(self):
        response = self.client.post(
            "/api/subtitles/correct",
            json={"segments": [], "enabled": True},
            headers={"Origin": "https://example.invalid"},
        )
        self.assertEqual(response.status_code, 403)

    def test_same_origin_tunnel_mutations_are_allowed(self):
        response = self.client.post(
            "/api/subtitles/correct",
            json={"segments": [], "enabled": True},
            base_url="https://captions.example.com",
            headers={"Origin": "https://captions.example.com"},
        )
        self.assertNotEqual(response.status_code, 403)

    def test_upload_and_exported_file_download(self):
        response = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"ID3" + b"\x00" * 10), "clip.mp3", "audio/mpeg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        uploaded_id = response.json["file_id"]
        try:
            download = self.client.get(f"/api/uploads/{uploaded_id}/download")
            self.assertEqual(download.status_code, 200)
            download.close()
        finally:
            app_module.config.UPLOAD_DIR.joinpath(f"{uploaded_id}.mp3").unlink(missing_ok=True)

        export = self.client.post(
            "/api/subtitles/export",
            json={
                "segments": [{"start": 0, "end": 1, "text": "Hello", "original_text": "Hello"}],
                "format": "srt",
                "variant": "corrected",
            },
        )
        self.assertEqual(export.status_code, 201)
        file_id = export.json["file_id"]
        try:
            downloaded = self.client.get(export.json["download_url"])
            self.assertEqual(downloaded.status_code, 200)
            self.assertIn(b"Hello", downloaded.data)
            downloaded.close()
        finally:
            app_module.config.OUTPUT_DIR.joinpath(f"{file_id}.srt").unlink(missing_ok=True)

    def test_voice_route_accepts_other_language_codes_for_whisper_cpp(self):
        response = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"ID3" + b"\x00" * 10), "clip.mp3", "audio/mpeg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 201)
        file_id = response.json["file_id"]
        try:
            with patch.object(app_module, "_queue", return_value=({"job_id": "test"}, 202)) as queue:
                transcription = self.client.post("/api/voice/transcribe", json={
                    "file_id": file_id,
                    "language": "sw",
                    "model": "tiny",
                    "engine": "whisper.cpp",
                })
            self.assertEqual(transcription.status_code, 202)
            self.assertEqual(queue.call_args.args[0], "voice transcription")
            self.assertIs(queue.call_args.args[1], app_module.transcribe_voice)
            self.assertEqual(queue.call_args.args[-1], "whisper.cpp")
        finally:
            app_module.config.UPLOAD_DIR.joinpath(f"{file_id}.mp3").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
