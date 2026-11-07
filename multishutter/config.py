"""Explicit rig configuration; USB enumeration order is never camera identity."""

from dataclasses import dataclass
import math
from pathlib import Path
import re
import tomllib


@dataclass(frozen=True)
class CameraSpec:
    id: str
    position: int
    port: str = ""
    model: str = ""
    serial: str = ""
    files_per_take: int = 1


@dataclass(frozen=True)
class Rig:
    backend: str
    cameras: tuple[CameraSpec, ...]
    event_timeout: float = 20.0
    settle_seconds: float = 0.5
    command_timeout: float = 45.0
    download_parallelism: int = 2
    download_retries: int = 1
    minimum_free_mb: int = 512


def load_rig(path: Path) -> Rig:
    with path.open("rb") as stream:
        data = tomllib.load(stream)
    unknown = set(data) - {"backend", "capture", "cameras"}
    if unknown:
        raise ValueError(f"Unknown rig fields: {sorted(unknown)}")
    backend = data.get("backend", "gphoto2")
    if backend not in {"gphoto2", "simulated"}:
        raise ValueError("backend must be 'gphoto2' or 'simulated'")
    raw_cameras = data.get("cameras", [])
    if not raw_cameras:
        raise ValueError("Configure at least one [[cameras]] entry")
    cameras = []
    for row in raw_cameras:
        camera = CameraSpec(**row)
        if not isinstance(camera.id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", camera.id):
            raise ValueError("Camera IDs must be safe names of 1–64 letters, digits, '-' or '_'")
        if type(camera.position) is not int or camera.position < 0:
            raise ValueError("Camera position must be a nonnegative integer")
        if type(camera.files_per_take) is not int or camera.files_per_take not in {1, 2}:
            raise ValueError("files_per_take must be 1 (JPEG/RAW) or 2 (RAW+JPEG)")
        if not all(isinstance(value, str) for value in (camera.port, camera.model, camera.serial)):
            raise ValueError("port, model and serial must be strings")
        if backend == "gphoto2" and (not re.fullmatch(r"usb:\d+,\d+", camera.port) or not camera.model):
            raise ValueError("Real cameras require an explicit usb:BUS,DEVICE port and exact model")
        cameras.append(camera)
    for attribute in ("id", "position") + (("port",) if backend == "gphoto2" else ()):
        values = [getattr(camera, attribute) for camera in cameras]
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate camera {attribute}")
    capture = data.get("capture", {})
    rig = Rig(backend=backend, cameras=tuple(sorted(cameras, key=lambda c: c.position)), **capture)
    for name in ("event_timeout", "settle_seconds", "command_timeout"):
        value = getattr(rig, name)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be a finite positive number")
    if rig.event_timeout + rig.settle_seconds + 5 >= rig.command_timeout:
        raise ValueError("command_timeout must exceed event_timeout + settle_seconds + 5")
    for name, minimum in (("download_parallelism", 1), ("download_retries", 0), ("minimum_free_mb", 0)):
        value = getattr(rig, name)
        if type(value) is not int or value < minimum:
            raise ValueError(f"{name} must be an integer >= {minimum}")
    if rig.download_retries > 5:
        raise ValueError("download_retries must be <= 5")
    return rig
