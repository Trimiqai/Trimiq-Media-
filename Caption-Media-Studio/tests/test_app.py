import io
import unittest

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


if __name__ == "__main__":
    unittest.main()
