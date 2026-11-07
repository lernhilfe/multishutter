"""Host diagnostics and a shared, non-triggering discovery path."""

import importlib
import platform
import sys


def load_gphoto():
    if sys.platform == "win32":
        raise RuntimeError("USB capture on Windows uses WSL2. Run Start-MultiShutter.cmd; see WINDOWS.md.")
    try:
        return importlib.import_module("gphoto2")
    except (ImportError, OSError) as error:
        raise RuntimeError("gphoto2 is unavailable in this Python runtime. On Debian/Ubuntu install python3-gphoto2 "
                           "and use /usr/bin/python3. Windows: run Setup-Windows.cmd. See WINDOWS.md.") from error


def autodetect(gp):
    detect = getattr(gp.Camera, "autodetect", None)
    if detect is not None:
        return detect()
    # Ubuntu 24.04 ships python-gphoto2 1.9, before Camera.autodetect().
    return gp.check_result(gp.gp_camera_autodetect())


def discover_cameras(backend="gphoto2") -> list[dict]:
    if backend == "simulated":
        return [{"id": name, "model": "SIMULATED", "position": index, "simulated": True,
                 "port": f"simulated:{name}"} for index, name in enumerate(("left", "center", "right"))]
    gp = load_gphoto()
    try:
        return [{"model": model, "port": port} for model, port in autodetect(gp)]
    except gp.GPhoto2Error as error:
        raise RuntimeError(f"Camera discovery failed: {error}") from error


def diagnose(backend="gphoto2") -> dict:
    system = platform.system()
    release = platform.release()
    report = {"system": system, "release": release, "machine": platform.machine(),
              "python": sys.version.split()[0], "executable": sys.executable,
              "wsl": system == "Linux" and "microsoft" in release.lower(),
              "backend": backend, "ready": backend == "simulated", "notes": [],
              "gphoto2_available": False, "libgphoto2_version": None}
    try:
        gp = load_gphoto()
        report["gphoto2_available"] = True
        report["libgphoto2_version"] = list(gp.gp_library_version(0))
        if backend == "gphoto2":
            report["ready"] = True
    except (RuntimeError, AttributeError) as error:
        report["notes"].append(str(error))
    report["notes"].append("No shutter release or USB camera connection is performed by doctor.")
    if report["wsl"]:
        report["notes"].append("Forward each Sony camera from Windows using usbipd; see WINDOWS.md.")
    return report
