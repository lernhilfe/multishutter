from pathlib import Path
from contextlib import redirect_stderr
import io
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from multishutter.backends import GPhotoBackend, save_asset
from multishutter.config import CameraSpec
from multishutter.cli import main


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value


class EventCamera:
    def __init__(self, clock, events):
        self.clock = clock
        self.events = list(events)

    def wait_for_event(self, milliseconds):
        if self.events:
            self.clock.value += 0.001
            event = self.events.pop(0)
            if isinstance(event, Exception):
                raise event
            return event
        self.clock.value += milliseconds / 1000
        return 0, None


def file_event(name, folder="/store"):
    return 1, SimpleNamespace(folder=folder, name=name)


class GPhotoEventsTests(unittest.TestCase):
    def test_discovery_driver_error_becomes_clean_cli_error(self):
        class DriverError(Exception):
            pass

        class BrokenCamera:
            @staticmethod
            def autodetect():
                raise DriverError("USB permission denied")

        module = SimpleNamespace(Camera=BrokenCamera, GPhoto2Error=DriverError)
        stderr = io.StringIO()
        with patch.dict("sys.modules", {"gphoto2": module}), redirect_stderr(stderr):
            self.assertEqual(main(["discover"]), 2)
        self.assertIn("USB permission denied", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def backend(self, clock, events):
        backend = GPhotoBackend(CameraSpec("center", 0))
        backend.gp = SimpleNamespace(GP_EVENT_TIMEOUT=0, GP_EVENT_FILE_ADDED=1)
        backend.camera = EventCamera(clock, events)
        return backend

    def collect(self, events, expected=1):
        clock = Clock()
        backend = self.backend(clock, events)
        with patch("multishutter.backends.time.monotonic", clock.monotonic):
            return backend.collect(expected, timeout=0.05, settle=0.01)

    def test_duplicate_events_are_not_counted_as_two_files(self):
        result = self.collect([file_event("one.JPG"), file_event("one.JPG")], expected=2)
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["assets"]), 1)

    def test_raw_and_jpeg_are_both_collected(self):
        result = self.collect([file_event("one.ARW"), file_event("one.JPG")], expected=2)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["assets"]), 2)

    def test_extra_image_makes_take_ambiguous(self):
        result = self.collect([file_event("one.JPG"), file_event("two.JPG")])
        self.assertFalse(result["ok"])
        self.assertEqual(len(result["assets"]), 2)

    def test_file_refs_survive_event_connection_error(self):
        result = self.collect([file_event("one.ARW"), OSError("USB disconnected")], expected=2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["assets"][0]["name"], "one.ARW")
        self.assertIn("USB disconnected", result["error"])

    def test_prepare_drains_events_without_deleting_camera_files(self):
        clock = Clock()
        backend = self.backend(clock, [file_event("old.JPG"), (2, "capture complete")])
        with patch("multishutter.backends.time.monotonic", clock.monotonic):
            self.assertEqual(backend.prepare()["stale_events"], 2)

    def test_unsafe_event_filename_is_rejected(self):
        result = self.collect([file_event("../escape.JPG")])
        self.assertFalse(result["ok"])
        self.assertFalse(result["assets"])

    def test_empty_transfer_never_publishes_completed_file(self):
        backend = SimpleNamespace(save=lambda asset, path: path.write_bytes(b""))
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "photo.JPG"
            with self.assertRaisesRegex(OSError, "empty"):
                save_asset(backend, {"name": "photo.JPG"}, destination)
            self.assertFalse(destination.exists())
            self.assertFalse(destination.with_suffix(".JPG.part").exists())


if __name__ == "__main__":
    unittest.main()
