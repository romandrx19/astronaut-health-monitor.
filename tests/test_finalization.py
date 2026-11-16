"""Regresiones concretas de coordinación, seguridad y ciclo de vida de escritorio."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import urlopen

from desktop import run_window
from persistence.sqlite import SQLiteRepository
from runtime_paths import AlreadyRunning, ProcessLock, configure_runtime
from simulator import set_simulation_running, simulate_due
from test_data_integration import load_application


class FinalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_application()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "test.sqlite3"
        self.app = self.module.create_app({"TESTING": True, "ARES_DB_PATH": self.path, "ARES_SEED_DEMO": False})
        self.client = self.app.test_client()
        self.repo = SQLiteRepository(self.path)
        self.now = datetime.now(timezone.utc)

    def test_global_simulation_starts_paused_and_keeps_reading_available(self):
        self.assertFalse(self.client.get("/api/simulation").json["running"])
        self.assertEqual(self.client.post("/api/simulate", json={"automatic": True}).json["created"], 0)
        self.assertEqual(self.client.get("/api/astronauts/1/history").json, [])

    def test_concurrent_tabs_generate_one_batch_per_interval(self):
        set_simulation_running(True, self.repo)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: simulate_due(automatic=True, repository=SQLiteRepository(self.path), now=self.now), range(8)))
        self.assertEqual(sum(result["created"] for result in results), 3)
        self.assertEqual(simulate_due(automatic=True, repository=self.repo, now=self.now + timedelta(seconds=5))["created"], 3)
        with closing(sqlite3.connect(self.path)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM measurements").fetchone()[0], 6)

    def test_separate_processes_share_the_same_clock_and_pause_persists(self):
        set_simulation_running(True, self.repo)
        script = "import sys,json; from simulator import simulate_due; from persistence.sqlite import SQLiteRepository; print(json.dumps(simulate_due(automatic=True,repository=SQLiteRepository(sys.argv[1]))))"
        workers = [subprocess.Popen([sys.executable, "-c", script, str(self.path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = []
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=30)
            self.assertEqual(worker.returncode, 0, stderr)
            results.append(json.loads(stdout))
        self.assertEqual(sum(item["created"] for item in results), 3)
        other = self.app.test_client()
        other.post("/api/simulation", json={"running": False})
        self.assertFalse(self.client.get("/api/simulation").json["running"])
        restarted = self.module.create_app({"ARES_DB_PATH": self.path, "ARES_SEED_DEMO": False})
        self.assertFalse(restarted.test_client().get("/api/simulation").json["running"])

    def test_explicit_manual_generation_obeys_cooldown_without_resuming(self):
        self.assertEqual(self.client.post("/api/simulate").json["created"], 3)
        self.assertEqual(self.client.post("/api/simulate").json["created"], 0)
        self.assertFalse(self.client.get("/api/simulation").json["running"])

    def test_pause_does_not_clear_history(self):
        self.client.post("/api/simulation", json={"running": True})
        self.client.post("/api/simulate", json={"automatic": True})
        before = self.client.get("/api/astronauts/1/history").json
        self.client.post("/api/simulation", json={"running": False})
        self.assertEqual(self.client.post("/api/simulate", json={"automatic": True}).json["created"], 0)
        self.assertEqual(before, self.client.get("/api/astronauts/1/history").json)

    def test_simulation_rejects_invalid_controls(self):
        for payload in ({}, {"running": "yes"}, {"running": True, "extra": 1}):
            self.assertEqual(self.client.post("/api/simulation", json=payload).status_code, 400)
        self.assertEqual(self.client.post("/api/simulation", data="x").status_code, 415)
        for payload in ({"automatic": "yes"}, {"extra": 1}, None):
            self.assertEqual(self.client.post("/api/simulate", json=payload, content_type="application/json").status_code, 400)

    def test_cross_site_remote_and_untrusted_host_writes_are_rejected(self):
        for headers in ({"Origin": "https://external.example"}, {"Sec-Fetch-Site": "cross-site"}, {"Host": "external.example"}):
            self.assertEqual(self.client.post("/api/simulate", headers=headers).status_code, 403)
        self.assertEqual(self.client.post("/api/simulate", environ_overrides={"REMOTE_ADDR": "192.0.2.8"}).status_code, 403)
        self.assertEqual(self.repo.get_history(1), [])
        self.assertEqual(self.client.post("/api/simulate", headers={"Origin": "http://localhost"}).status_code, 201)

    def test_safe_errors_and_headers(self):
        self.app.config["PROPAGATE_EXCEPTIONS"] = False
        with patch.object(self.repo.__class__, "list_astronauts", side_effect=RuntimeError("secret-test-value")):
            response = self.client.get("/api/astronauts")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret-test-value", response.get_data(as_text=True))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_native_server_is_closed_when_window_returns_or_fails(self):
        class Webview:
            screens = [SimpleNamespace(width=1280, height=800)]
            def create_window(inner, title, url, **options):
                inner.url = url
                self.assertEqual(options["height"], 720)
                with urlopen(url + "/api/health", timeout=5) as response:
                    self.assertEqual(response.status, 200)
            def start(inner, **options):
                self.assertFalse(options["debug"])
        webview = Webview()
        run_window(self.app, webview)
        with self.assertRaises(URLError):
            urlopen(webview.url, timeout=1)
        with patch.object(webview, "create_window", side_effect=RuntimeError("window-failed")):
            with self.assertRaisesRegex(RuntimeError, "window-failed"):
                run_window(self.app, webview)

    def test_process_lock_prevents_duplicate_launchers(self):
        path = self.root / "instance.lock"
        with ProcessLock(path):
            with self.assertRaises(AlreadyRunning):
                with ProcessLock(path):
                    self.fail("No debe adquirir un segundo bloqueo")
        with ProcessLock(path):
            pass

    def test_frozen_database_is_persistent_and_never_overwritten(self):
        seed = self.root / "bundle/data/nasa/bootstrap.sqlite3"
        seed.parent.mkdir(parents=True)
        SQLiteRepository(seed).initialize("all")
        data = self.root / "persistent"
        with patch.dict(os.environ, {"ARES_DATA_DIR": str(data)}, clear=True), patch("runtime_paths.resource_root", return_value=self.root / "bundle"):
            path = configure_runtime()
            self.assertEqual(path, data / "health.sqlite3")
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("UPDATE astronauts SET name='Persistente' WHERE id=1")
                connection.commit()
            self.assertEqual(configure_runtime(), path)
            with closing(sqlite3.connect(path)) as connection:
                self.assertEqual(connection.execute("SELECT name FROM astronauts WHERE id=1").fetchone()[0], "Persistente")
