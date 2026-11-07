from pathlib import Path
import errno
import tempfile
import unittest
from unittest.mock import patch

from multishutter.config import load_rig
from multishutter.console import run_console
from multishutter.rig_setup import configure_rig
from multishutter.storage import sync_directory
from multishutter.runtime import autodetect
from types import SimpleNamespace


class PCWorkflowTests(unittest.TestCase):
    def test_ubuntu_24_04_legacy_discovery_binding(self):
        cameras = [("Sony Alpha", "usb:001,002")]
        gp = SimpleNamespace(Camera=object, gp_camera_autodetect=lambda: (1, cameras),
                             check_result=lambda result: result[1])
        self.assertEqual(list(autodetect(gp)), cameras)

    def test_selected_order_and_existing_configuration_are_preserved(self):
        cameras = [
            {"model": "Sony A", "port": "usb:001,002"},
            {"model": "Sony B", "port": "usb:001,003"},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.toml"
            path.write_text("original configuration", encoding="utf-8")
            result = configure_rig(cameras, "gphoto2", path,
                                   read=lambda _: "2,1", emit=lambda _: None)
            rig = load_rig(path)
            self.assertEqual([c.model for c in rig.cameras], ["Sony B", "Sony A"])
            self.assertEqual(Path(result["backup"]).read_text(), "original configuration")

    def test_duplicate_selection_leaves_existing_configuration_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rig.toml"
            path.write_text("original", encoding="utf-8")
            with self.assertRaises(ValueError):
                configure_rig([{"model": "Sony A", "port": "usb:001,002"}],
                              "gphoto2", path, read=lambda _: "1,1", emit=lambda _: None)
            self.assertEqual(path.read_text(), "original")

    def test_console_survives_failed_command_and_does_not_retry_capture(self):
        commands = iter(["capture --takes 1", "discover", "quit"])
        executed = []

        def execute(args):
            executed.append(args)
            return 1 if args[0] == "capture" else 0

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            code = run_console(root / "rig.toml", root / "photos", read=lambda _: next(commands),
                               execute=execute, emit=lambda _: None)
        self.assertEqual(code, 0)
        self.assertEqual([args[0] for args in executed], ["capture", "discover"])

    @unittest.skipUnless(__import__("os").name == "posix", "WSL directory sync")
    def test_drvfs_unsupported_directory_sync_is_tolerated_but_io_failure_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("multishutter.storage.os.fsync", side_effect=OSError(errno.EINVAL, "unsupported")):
                sync_directory(Path(directory))
            with patch("multishutter.storage.os.fsync", side_effect=OSError(errno.EIO, "disk failure")):
                with self.assertRaises(OSError):
                    sync_directory(Path(directory))
