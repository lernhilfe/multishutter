"""Regressions for delayed Sony property acknowledgements."""
import unittest
from unittest.mock import patch
from multishutter.backends import GPhotoBackend
from multishutter.config import CameraSpec


class Widget:
    def __init__(self, value): self.value = value
    def get_value(self): return self.value
    def set_value(self, value): self.value = value


class DelayedCamera:
    def __init__(self): self.value, self.pending, self.events = '100', None, 0
    def get_single_config(self, key): return Widget(self.value)
    def set_single_config(self, key, widget): self.pending = widget.get_value()
    def wait_for_event(self, timeout):
        self.events += 1
        if self.events == 2: self.value = self.pending
        return 0, None


class SonySettingsTests(unittest.TestCase):
    def test_write_waits_for_property_event_before_accepting_value(self):
        backend = GPhotoBackend(CameraSpec('sony', 0))
        backend.camera = DelayedCamera()
        backend._write_setting('iso', '800')
        self.assertEqual(backend.camera.value, '800')
        self.assertEqual(backend.camera.events, 2)

    def test_ignored_write_is_not_reported_as_success(self):
        backend = GPhotoBackend(CameraSpec('sony', 0))
        backend.camera = DelayedCamera()
        with patch('multishutter.backends.time.monotonic', side_effect=[0, 3]):
            with self.assertRaisesRegex(RuntimeError, 'expected'):
                backend._write_setting('iso', '800')
