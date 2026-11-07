import hashlib
import json
from pathlib import Path
import tempfile
import time
import unittest

from multishutter.backends import SimulatedBackend
from multishutter.capture import run_capture
from multishutter.cli import inspect_session
from multishutter.config import CameraSpec, Rig, load_rig
from multishutter.storage import rig_lock
from multishutter.workers import CameraWorker


class FaultBackend(SimulatedBackend):
    def __init__(self, spec):
        super().__init__(spec)
        self.download_calls = 0

    def connect(self):
        if self.spec.model == "connect-error":
            raise RuntimeError("camera is unplugged")
        return super().connect()

    def prepare(self):
        if self.spec.model == "hang":
            time.sleep(10)
        return super().prepare()

    def trigger(self):
        super().trigger()
        if self.spec.model == "uncertain-trigger":
            raise TimeoutError("command response lost")

    def collect(self, expected, timeout, settle):
        result = super().collect(expected, timeout, settle)
        result["trigger_count"] = self.sequence
        if self.spec.model == "missing-file":
            result.update(ok=False, error="file event missing", assets=result["assets"][:1])
        if self.spec.model == "unsafe-file":
            result["assets"][0]["name"] = "../../escape.png"
        return result

    def save(self, asset, destination):
        self.download_calls += 1
        if self.spec.model == "flaky-download" and self.download_calls == 1:
            destination.write_bytes(b"partial")
            raise OSError("USB read failed")
        if self.spec.model == "broken-download":
            destination.write_bytes(b"partial")
            raise OSError("camera disconnected")
        super().save(asset, destination)


def fault_factory(name, spec):
    return FaultBackend(spec)


class CaptureTests(unittest.TestCase):
    def test_delays_change_the_scheduled_camera_start_without_retriggering(self):
        path, manifest = self.capture([CameraSpec("fast", 0), CameraSpec("slow", 1)],
                                      delays_ms={"fast": 100, "slow": 0})
        self.assertEqual(manifest["status"], "complete")
        entries = manifest["takes"][0]["cameras"]
        difference = (entries["fast"]["trigger_started_ns"] - entries["slow"]["trigger_started_ns"])/1e6
        self.assertGreater(difference, 60)
        self.assertLess(difference, 160)
        self.assertEqual(entries["fast"]["trigger_count"], 1)

    def test_disconnection_guard_prevents_trigger_after_countdown(self):
        def disconnected():
            raise RuntimeError("USB disconnected before trigger")
        path, manifest = self.capture([CameraSpec("center", 0)], before_trigger=disconnected)
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("USB disconnected", manifest["error"])
        self.assertEqual(manifest["takes"][0]["cameras"], {})
        self.assertFalse(list(path.parent.rglob("*.png")))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def capture(self, cameras, **kwargs):
        rig = Rig("simulated", tuple(cameras), minimum_free_mb=0)
        return run_capture(rig, self.root / "captures", log=lambda _: None,
                           backend_factory=fault_factory, lock_path=self.root / "rig.lock", **kwargs)

    def test_multi_camera_multi_take_keeps_identity_and_hashes(self):
        path, manifest = self.capture([CameraSpec("right", 1), CameraSpec("left", 0)], takes=3)
        self.assertEqual(manifest["status"], "complete")
        self.assertTrue(manifest["simulated"])
        self.assertEqual(len(list(path.parent.rglob("*.png"))), 6)
        for number, take in enumerate(manifest["takes"], 1):
            self.assertEqual(take["status"], "complete")
            self.assertGreaterEqual(take["command_start_spread_ms"], 0)
            for camera_id, entry in take["cameras"].items():
                self.assertEqual(entry["trigger_count"], number)
                record = entry["files"][0]
                self.assertEqual(Path(record["path"]).parent.name, camera_id)
                data = Path(record["path"]).read_bytes()
                self.assertTrue(data.startswith(b"\x89PNG"))
                self.assertEqual(record["sha256"], hashlib.sha256(data).hexdigest())
        self.assertFalse(list(path.parent.rglob("*.part")))
        report, ok = inspect_session(path, True)
        self.assertTrue(ok, report)
        self.assertEqual(json.loads(path.read_text()), manifest)

    def test_flaky_download_retries_without_retrigger(self):
        _, manifest = self.capture([CameraSpec("center", 0, model="flaky-download")])
        entry = manifest["takes"][0]["cameras"]["center"]
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(entry["trigger_count"], 1)
        self.assertEqual(entry["files"][0]["attempts"], 2)
        self.assertNotIn("error", entry["files"][0])

    def test_missing_file_preserves_known_image_and_stops_series(self):
        path, manifest = self.capture([CameraSpec("left", 0, model="missing-file", files_per_take=2),
                                       CameraSpec("right", 1)], takes=3)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(len(manifest["takes"]), 1)
        entries = manifest["takes"][0]["cameras"]
        self.assertEqual(entries["left"]["status"], "incomplete")
        self.assertEqual(entries["left"]["files"][0]["status"], "complete")
        self.assertEqual(entries["right"]["status"], "complete")
        self.assertEqual(len(list(path.parent.rglob("*.png"))), 2)

    def test_failed_preflight_never_creates_take(self):
        _, manifest = self.capture([CameraSpec("center", 0, model="connect-error")])
        self.assertEqual(manifest["status"], "failed")
        self.assertFalse(manifest["takes"])
        self.assertIn("unplugged", manifest["cameras"]["center"]["error"])

    def test_uncertain_trigger_is_never_replayed(self):
        _, manifest = self.capture([CameraSpec("center", 0, model="uncertain-trigger")], takes=3)
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(len(manifest["takes"]), 1)
        entry = manifest["takes"][0]["cameras"]["center"]
        self.assertEqual(entry["trigger_state"], "unknown")
        self.assertEqual(entry["trigger_count"], 1)
        self.assertEqual(entry["files"][0]["status"], "complete")

    def test_permanent_download_failure_cleans_partial_file(self):
        path, manifest = self.capture([CameraSpec("center", 0, model="broken-download")])
        entry = manifest["takes"][0]["cameras"]["center"]
        self.assertEqual(manifest["status"], "incomplete")
        self.assertEqual(entry["trigger_count"], 1)
        self.assertEqual(entry["files"][0]["attempts"], 2)
        self.assertFalse(list(path.parent.rglob("*.part")))
        self.assertFalse(list(path.parent.rglob("*.png")))

    def test_unsafe_camera_filename_cannot_escape_session(self):
        path, manifest = self.capture([CameraSpec("center", 0, model="unsafe-file")])
        self.assertEqual(manifest["status"], "incomplete")
        self.assertIn("Unsafe", manifest["takes"][0]["cameras"]["center"]["files"][0]["error"])
        self.assertFalse(list(self.root.rglob("escape.png")))

    def test_verification_detects_modified_original(self):
        path, manifest = self.capture([CameraSpec("center", 0)])
        file = Path(manifest["takes"][0]["cameras"]["center"]["files"][0]["path"])
        file.write_bytes(b"corrupted")
        report, ok = inspect_session(path, True)
        self.assertFalse(ok)
        self.assertEqual(len(report["verification_errors"]), 1)

    def test_timed_out_worker_is_terminated(self):
        rig = Rig("simulated", (CameraSpec("hung", 0, model="hang"),))
        worker = CameraWorker(rig, rig.cameras[0], fault_factory)
        try:
            self.assertTrue(worker.call("connect")["ok"])
            worker.timeout = 0.1
            worker.send("prepare")
            with self.assertRaises(TimeoutError):
                worker.receive()
            self.assertFalse(worker.process.is_alive())
        finally:
            worker.close()

    def test_lock_prevents_second_owner_and_releases(self):
        path = self.root / "lock"
        with rig_lock(path):
            with self.assertRaisesRegex(RuntimeError, "already owns"):
                with rig_lock(path):
                    self.fail("second process acquired rig lock")
        with rig_lock(path):
            pass

    def test_disk_preflight_and_invalid_count_fail_before_capture(self):
        rig = Rig("simulated", (CameraSpec("center", 0),), minimum_free_mb=10**15)
        with self.assertRaisesRegex(RuntimeError, "free disk"):
            run_capture(rig, self.root, lock_path=self.root / "lock")
        with self.assertRaises(ValueError):
            self.capture([CameraSpec("center", 0)], takes=0)
        with self.assertRaises(ValueError):
            self.capture([CameraSpec("center", 0)], countdown=float("nan"))


class ConfigurationTests(unittest.TestCase):
    def test_examples_load(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(len(load_rig(root / "config/rig.example.toml").cameras), 4)
        self.assertEqual(load_rig(root / "config/rig.simulated.toml").backend, "simulated")

    def test_rejects_ambiguous_or_unsafe_configuration(self):
        rows = [
            'backend="simulated"\n[[cameras]]\nid="../bad"\nposition=0',
            'backend="simulated"\n[[cameras]]\nid="a"\nposition=0\n[[cameras]]\nid="b"\nposition=0',
            'backend="simulated"\n[[cameras]]\nid="a"\nposition=0\n[[cameras]]\nid="a"\nposition=1',
            'backend="gphoto2"\n[[cameras]]\nid="a"\nposition=0',
            'backend="simulated"\n[capture]\ncommand_timeout=1\n[[cameras]]\nid="a"\nposition=0',
            'backend="simulated"\n[capture]\ndownload_parallelism=0\n[[cameras]]\nid="a"\nposition=0',
            'backend="simulated"\n[capture]\nevent_timeout=nan\n[[cameras]]\nid="a"\nposition=0',
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.toml"
            for document in rows:
                with self.subTest(document=document):
                    path.write_text(document)
                    with self.assertRaises(ValueError):
                        load_rig(path)


if __name__ == "__main__":
    unittest.main()
