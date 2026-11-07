"""Backend boundary. Hardware bindings are imported only inside camera workers."""

from dataclasses import asdict
import hashlib
import os
from pathlib import Path, PurePosixPath
import struct
import time
from typing import Protocol
import zlib

from .config import CameraSpec
from .storage import sync_directory
from .runtime import autodetect, load_gphoto
from .camera_settings import CONTROL_KEYS, plan_settings


class CameraBackend(Protocol):
    def connect(self) -> dict: ...
    def prepare(self) -> dict: ...
    def trigger(self) -> None: ...
    def collect(self, expected: int, timeout: float, settle: float) -> dict: ...
    def save(self, asset: dict, destination: Path) -> None: ...
    def close(self) -> None: ...
    def health(self) -> dict: ...
    def settings(self) -> dict: ...
    def set_settings(self, values: dict, enforce_manual=True) -> dict: ...


def safe_filename(name: str) -> str:
    if not name or name in {".", ".."} or PurePosixPath(name).name != name or "\\" in name or "\x00" in name:
        raise ValueError(f"Unsafe camera filename: {name!r}")
    return name


def save_asset(backend: CameraBackend, asset: dict, destination: Path) -> dict:
    """A completed manifest entry is published only after an fsynced file rename."""
    safe_filename(asset["name"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    try:
        backend.save(asset, temporary)
        size = temporary.stat().st_size
        if size <= 0:
            raise OSError("Camera returned an empty file")
        digest = hashlib.sha256()
        with temporary.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        sync_directory(destination.parent)
        return {"path": str(destination), "size": size, "sha256": digest.hexdigest()}
    finally:
        temporary.unlink(missing_ok=True)


class GPhotoBackend:
    def __init__(self, spec: CameraSpec):
        self.spec = spec
        self.gp = None
        self.camera = None
        self.opened = False

    def connect(self) -> dict:
        gp = load_gphoto()
        self.gp = gp
        detected = dict((port, model) for model, port in autodetect(gp))
        if detected.get(self.spec.port) != self.spec.model:
            raise RuntimeError(f"Expected {self.spec.model!r} at {self.spec.port}; run discover and verify the rig mapping")
        ports = gp.PortInfoList()
        ports.load()
        self.camera = gp.Camera()
        self.camera.set_port_info(ports[ports.lookup_path(self.spec.port)])
        self.camera.init()
        self.opened = True
        abilities = self.camera.get_abilities()
        if not abilities.operations & gp.GP_OPERATION_TRIGGER_CAPTURE:
            raise RuntimeError("Backend does not advertise asynchronous trigger capture; no automatic fallback")
        serial = ""
        try:
            serial = str(self.camera.get_single_config("serialnumber").get_value()).strip()
        except gp.GPhoto2Error:
            pass
        if self.spec.serial and serial != self.spec.serial:
            raise RuntimeError(f"Serial mismatch: expected {self.spec.serial!r}, received {serial!r}")
        return {"model": self.spec.model, "port": self.spec.port, "serial": serial,
                "identity_verified": bool(self.spec.serial), "trigger_capture": True}

    def prepare(self) -> dict:
        # Old queued events must not become assets of the next take. Never delete files.
        deadline = time.monotonic() + 3
        discarded = 0
        while time.monotonic() < deadline:
            kind, _ = self.camera.wait_for_event(100)
            if kind == self.gp.GP_EVENT_TIMEOUT:
                return {"stale_events": discarded}
            discarded += 1
        raise RuntimeError("Camera event queue is not quiet; capture was not started")

    def health(self) -> dict:
        if not self.opened:
            raise RuntimeError("Camera is not connected")
        # Sony sends property updates as events, including physical mode-dial changes.
        # Pump these before reading values so startup placeholders cannot remain in the UI.
        unassigned_files = 0
        for _ in range(24):
            kind, _data = self.camera.wait_for_event(20)
            if kind == self.gp.GP_EVENT_TIMEOUT:
                break
            if kind == self.gp.GP_EVENT_FILE_ADDED:
                unassigned_files += 1
        try:
            battery = self.camera.get_single_config("batterylevel").get_value()
        except self.gp.GPhoto2Error as error:
            if getattr(error, "code", None) != -2:
                raise
            battery = None
        return {"battery": battery, "model": self.spec.model, "port": self.spec.port,
                "settings": self.settings(), "unassigned_files": unassigned_files}

    def settings(self) -> dict:
        result = {}
        for key in CONTROL_KEYS:
            try:
                widget = self.camera.get_single_config(key)
                row = {"value": widget.get_value(), "readonly": bool(widget.get_readonly()),
                       "label": widget.get_label()}
                kind = widget.get_type()
                if kind in (self.gp.GP_WIDGET_MENU, self.gp.GP_WIDGET_RADIO):
                    row["choices"] = list(widget.get_choices())
                elif kind == self.gp.GP_WIDGET_RANGE:
                    row["range"] = list(widget.get_range())
                result[key] = row
            except self.gp.GPhoto2Error:
                continue
        return result

    def _write_setting(self, key, value):
        widget = self.camera.get_single_config(key)
        widget.set_value(value)
        self.camera.set_single_config(key, widget)
        # Sony property writes complete asynchronously. Reading immediately may
        # return the previous cached value until the property event is handled.
        deadline = time.monotonic() + 2.5
        while True:
            actual = self.camera.get_single_config(key).get_value()
            if str(actual) == str(value):
                return
            if time.monotonic() >= deadline:
                raise RuntimeError(f"{key}: camera reported {actual!r}, expected {value!r}")
            self.camera.wait_for_event(100)

    def set_settings(self, values: dict, enforce_manual=True) -> dict:
        before = self.settings()
        plan = plan_settings(before, values, enforce_manual)
        changed = []
        try:
            for key, value in plan.items():
                changed.append(key)
                self._write_setting(key, value)
        except Exception as error:
            rollback_errors = []
            for key in reversed(changed):
                try:
                    self._write_setting(key, before[key]["value"])
                except Exception as rollback_error:
                    rollback_errors.append(f"{key}: {rollback_error}")
            raise RuntimeError(f"Setting failed: {error}; rollback errors: {rollback_errors}") from error
        return {"before": {key: before[key]["value"] for key in plan},
                "applied": plan, "settings": self.settings()}

    def trigger(self) -> None:
        self.camera.trigger_capture()

    def collect(self, expected: int, timeout: float, settle: float) -> dict:
        files = {}
        deadline = time.monotonic() + timeout
        quiet_until = None
        try:
            while True:
                now = time.monotonic()
                if quiet_until is not None and now >= quiet_until:
                    break
                if now >= deadline:
                    break
                remaining = min(deadline, quiet_until or deadline) - now
                kind, data = self.camera.wait_for_event(max(1, min(100, int(remaining * 1000))))
                if kind == self.gp.GP_EVENT_FILE_ADDED:
                    name = safe_filename(data.name)
                    key = (data.folder, name)
                    if key not in files:
                        files[key] = {"folder": data.folder, "name": name}
                        if len(files) >= expected:
                            quiet_until = time.monotonic() + settle
            ok = len(files) == expected
            return {"ok": ok, "assets": list(files.values()),
                    "error": None if ok else f"Expected {expected} files, observed {len(files)}; take is incomplete/ambiguous"}
        except Exception as error:
            return {"ok": False, "assets": list(files.values()), "error": str(error)}

    def save(self, asset: dict, destination: Path) -> None:
        image = self.camera.file_get(asset["folder"], asset["name"], self.gp.GP_FILE_TYPE_NORMAL)
        image.save(str(destination))

    def close(self) -> None:
        if self.opened:
            self.camera.exit()
            self.opened = False


def _png() -> bytes:
    """Small valid image fixture; simulated files are deliberately labelled PNG."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    pixels = b"".join(b"\x00" + b"\x40\x90\xc0" * 16 for _ in range(16))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


class SimulatedBackend:
    def __init__(self, spec: CameraSpec):
        self.spec = spec
        self.sequence = 0
        options = {
            "expprogram": ["M", "A", "S", "P"], "iso": ["100", "200", "400", "800", "1600", "3200"],
            "shutterspeed": ["1/30", "1/60", "1/125", "1/250", "1/500", "1/1000"],
            "f-number": ["2.8", "4", "5.6", "8", "11", "16"],
            "whitebalance": ["Automatic", "Daylight", "Choose Color Temperature"],
            "focusmode": ["Manual", "AF-C"], "capturemode": ["Single Shot"],
            "imagequality": ["Fine", "RAW", "RAW+JPEG (Fine)"],
        }
        self.controls = {key: {"value": values[0], "choices": values, "readonly": False, "label": key}
                         for key, values in options.items()}
        self.controls["colortemperature"] = {"value": 5600, "range": [2500, 9900, 100], "readonly": False}

    def connect(self) -> dict:
        return {**asdict(self.spec), "simulated": True, "trigger_capture": True}

    def prepare(self) -> dict:
        return {"stale_events": 0}

    def health(self) -> dict:
        return {"battery": "100%", "simulated": True}

    def settings(self) -> dict:
        from copy import deepcopy
        return deepcopy(self.controls)

    def set_settings(self, values: dict, enforce_manual=True) -> dict:
        plan = plan_settings(self.controls, values, enforce_manual)
        before = {key: self.controls[key]["value"] for key in plan}
        for key, value in plan.items():
            self.controls[key]["value"] = value
        return {"before": before, "applied": plan, "settings": self.settings()}

    def trigger(self) -> None:
        self.sequence += 1

    def collect(self, expected: int, timeout: float, settle: float) -> dict:
        return {"ok": True, "error": None, "assets": [
            {"folder": "/simulated", "name": f"SIM_{self.sequence:04d}_{index}.png"}
            for index in range(expected)]}

    def save(self, asset: dict, destination: Path) -> None:
        destination.write_bytes(_png())

    def close(self) -> None:
        pass


def create_backend(name: str, spec: CameraSpec) -> CameraBackend:
    if name == "gphoto2":
        return GPhotoBackend(spec)
    if name == "simulated":
        return SimulatedBackend(spec)
    raise ValueError(f"Unknown backend: {name}")
