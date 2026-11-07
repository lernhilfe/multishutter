"""Thin CLI; orchestration stays usable by a future HTTP service."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

from . import __version__
from .capture import _broadcast, run_capture
from .config import load_rig
from .storage import rig_lock
from .workers import CameraWorker
from .console import run_console
from .rig_setup import configure_rig
from .runtime import diagnose, discover_cameras


def inspect_session(path: Path, verify: bool) -> tuple[dict, bool]:
    path = path / "manifest.json" if path.is_dir() else path
    manifest = json.loads(path.read_text(encoding="utf-8"))
    report = {"session_id": manifest["session_id"], "status": manifest["status"],
              "simulated": manifest["simulated"], "takes": len(manifest["takes"]),
              "requested_takes": manifest["requested_takes"], "manifest": str(path.resolve()),
              "error": manifest.get("error"), "verification_errors": []}
    if verify:
        for take in manifest["takes"]:
            for entry in take["cameras"].values():
                for record in entry.get("files", []):
                    if record["status"] != "complete":
                        continue
                    try:
                        # Hashes are checked independently of reported file length.
                        digest = hashlib.sha256()
                        with Path(record["path"]).open("rb") as stream:
                            for block in iter(lambda: stream.read(1024 * 1024), b""):
                                digest.update(block)
                        if digest.hexdigest() != record["sha256"]:
                            raise ValueError("SHA-256 mismatch")
                    except (OSError, ValueError) as error:
                        report["verification_errors"].append({"path": record["path"], "error": str(error)})
    return report, report["status"] == "complete" and not report["verification_errors"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="multishutter", description="Foreground multi-camera capture for PC / Raspberry Pi")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    discover = commands.add_parser("discover", help="List detected models and current USB ports (no capture)")
    discover.add_argument("--backend", choices=("gphoto2", "simulated"), default="gphoto2")
    doctor = commands.add_parser("doctor", help="Check platform and dependencies without opening cameras")
    doctor.add_argument("--backend", choices=("gphoto2", "simulated"), default="gphoto2")
    configure = commands.add_parser("configure", help="Discover and explicitly map camera order")
    configure.add_argument("--backend", choices=("gphoto2", "simulated"), default="gphoto2")
    configure.add_argument("--output", type=Path, default=Path("config/rig.local.toml"))
    console = commands.add_parser("console", help="Run an interactive foreground process until quit")
    console.add_argument("--config", type=Path, default=Path("config/rig.local.toml"))
    console.add_argument("--output", type=Path, default=Path("captures"))
    console.add_argument("--backend", choices=("gphoto2", "simulated"), default="gphoto2")
    check = commands.add_parser("check", help="Validate the rig and open all cameras without triggering")
    check.add_argument("--config", type=Path, required=True)
    capture = commands.add_parser("capture", help="Capture one or more coordinated takes")
    capture.add_argument("--config", type=Path, required=True)
    capture.add_argument("--output", type=Path, default=Path("captures"))
    capture.add_argument("--takes", type=int, default=1)
    capture.add_argument("--countdown", type=float, default=0, help="Seconds before the first take")
    capture.add_argument("--interval", type=float, default=0, help="Pause AFTER transfer/preparation before each following take")
    status = commands.add_parser("status", help="Read a saved session; optionally verify local files")
    status.add_argument("session", type=Path)
    status.add_argument("--verify", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "console":
            return run_console(args.config, args.output, args.backend)
        if args.command == "doctor":
            report = diagnose(args.backend)
            print(json.dumps(report, indent=2))
            return 0 if report["ready"] else 2
        if args.command == "configure":
            with rig_lock():
                result = configure_rig(discover_cameras(args.backend), args.backend, args.output)
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "discover":
            with rig_lock():
                cameras = discover_cameras(args.backend)
                print(json.dumps(cameras, indent=2))
                return 0
        if args.command == "status":
            report, ok = inspect_session(args.session, args.verify)
            print(json.dumps(report, indent=2))
            return 0 if ok else 1
        rig = load_rig(args.config)
        if args.command == "check":
            with rig_lock():
                workers = []
                try:
                    for spec in rig.cameras:
                        workers.append(CameraWorker(rig, spec))
                    results = _broadcast(workers, "connect")
                    print(json.dumps({"backend": rig.backend, "cameras": results}, indent=2))
                    return 0 if all(item["ok"] for item in results.values()) else 1
                finally:
                    for worker in workers:
                        worker.close()
        path, manifest = run_capture(rig, args.output, args.takes, args.countdown, args.interval,
                                     log=lambda message: print(message, file=sys.stderr))
        print(json.dumps({"session_id": manifest["session_id"], "status": manifest["status"],
                          "simulated": manifest["simulated"], "manifest": str(path)}, indent=2))
        return 0 if manifest["status"] == "complete" else (130 if manifest["status"] == "interrupted" else 1)
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, TypeError, RuntimeError, KeyError) as error:
        print(f"multishutter: {error}", file=sys.stderr)
        return 2
