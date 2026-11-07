import importlib
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class ApiTests(unittest.TestCase):
    def test_validation_defaults_and_persistent_events(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict("os.environ", {"DATA_DIR": directory}):
            module = importlib.import_module("transc_service.app")
            from transc_service.config import ServerConfig
            from transc_service.pipeline.manager import JobManager
            from transc_service.stores.local import LocalObjectStore
            from transc_service.stores.sqlite import SqliteMetadataStore
            meta = SqliteMetadataStore(directory + "/meta.db")
            objects = LocalObjectStore(directory + "/objects")
            manager = JobManager(meta, objects)
            # No workers/network needed to exercise the API contract.
            async def start():
                await meta.init()
            with patch.object(manager, "start", start), patch.multiple(module,
                cfg=ServerConfig(data_dir=directory, default_model="small", default_max_duration_s=123),
                meta=meta, objects=objects, manager=manager,
            ), TestClient(module.app) as client:
                for url in ["http://youtu.be/x", "https://example.com", "https://youtube.com:bad/watch"]:
                    self.assertEqual(client.post("/jobs", json={"youtube_url": url}).status_code, 400)
                self.assertEqual(client.get("/jobs?limit=-1").status_code, 422)
                with patch('transc_service.model_health._cached_file', return_value=None):
                    health = client.get('/health/models')
                self.assertEqual(health.status_code, 200)
                self.assertEqual(len(health.json()['models']), 3)
                self.assertEqual(client.get('/healthz').json(), {'ok': True})
                response = client.post("/jobs", json={"youtube_url": "https://youtu.be/x",
                    "config": {"yt_dlp": {"allow_mix": True}}})
                self.assertEqual(response.status_code, 202)
                job = response.json()
                self.assertEqual(job["config"]["transcription"]["model"], "small")
                self.assertEqual(job["config"]["yt_dlp"]["max_duration_s"], 123)
                self.assertTrue(job["config"]["yt_dlp"]["allow_mix"])
                self.assertEqual(client.get(f"/jobs/{job['id']}/events").status_code, 404)
                self.assertEqual(client.post(f"/jobs/{job['id']}/cancel").json()["status"], "cancelled")
                retried = client.post(f"/jobs/{job['id']}/retry")
                self.assertEqual(retried.status_code, 202)
                self.assertEqual(retried.json()['retry_count'], 1)
                self.assertEqual(client.post(f"/jobs/{job['id']}/retry").status_code, 409)
                self.assertEqual(client.post('/jobs/missing/retry').status_code, 404)
                self.assertEqual(client.delete(f"/jobs/{job['id']}").status_code, 204)
                self.assertEqual(client.get(f"/jobs/{job['id']}").status_code, 404)
