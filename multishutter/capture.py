"""Capture orchestration, independent of gphoto2 and any future web UI."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import nullcontext
from dataclasses import asdict
import math
from pathlib import Path
import shutil
import time
from uuid import uuid4

from .backends import create_backend, safe_filename
from .config import Rig
from .storage import rig_lock, utc_now, write_manifest
from .workers import CameraWorker


def _safe_call(worker, operation, **fields):
    try:
        return worker.call(operation, **fields)
    except Exception as error:
        return {"ok": False, "error": str(error)}


def _broadcast(workers, operation):
    with ThreadPoolExecutor(max_workers=len(workers)) as pool:
        tasks = {pool.submit(_safe_call, worker, operation): worker.spec.id for worker in workers}
        return {tasks[task]: task.result() for task in as_completed(tasks)}


def _download_camera(worker, entry, folder, retries):
    for index, asset in enumerate(entry.get("assets", []), start=1):
        record = {**asset, "status": "failed", "attempts": 0}
        entry["files"].append(record)
        try:
            name = safe_filename(asset["name"])
            destination = folder / f"{index:02d}_{name}"
            for attempt in range(retries + 1):
                record["attempts"] = attempt + 1
                result = _safe_call(worker, "download", asset=asset, destination=str(destination))
                if result["ok"]:
                    record.update(result["file"], status="complete")
                    record.pop("error", None)
                    break
                record["error"] = result["error"]
        except Exception as error:
            record["error"] = str(error)
    entry["status"] = "complete" if (
        entry.get("ok") and len(entry["files"]) == worker.spec.files_per_take
        and all(record["status"] == "complete" for record in entry["files"])
    ) else "incomplete"
    return entry


def run_capture(rig: Rig, output: Path, takes=1, countdown=0.0, interval=0.0,
                log=print, backend_factory=create_backend, lock_path=None, *,
                shared_workers=None, delays_ms=None, before_trigger=None, cancel=None,
                manifest_extra=None) -> tuple[Path, dict]:
    if type(takes) is not int or takes < 1 or takes > 100:
        raise ValueError("takes must be an integer between 1 and 100")
    if any(not math.isfinite(value) or value < 0 or value > 3600 for value in (countdown, interval)):
        raise ValueError("countdown and interval must be finite values between 0 and 3600 seconds")
    delays_ms = delays_ms or {}
    if any(type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000
           for value in delays_ms.values()):
        raise ValueError("Camera delays must be between 0 and 1000 ms")
    if set(delays_ms) - {spec.id for spec in rig.cameras}:
        raise ValueError("Delay references an unknown camera")
    with (nullcontext() if shared_workers is not None else rig_lock(lock_path)):
        output = output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(output).free < rig.minimum_free_mb * 1024 * 1024:
            raise RuntimeError(f"Need at least {rig.minimum_free_mb} MB free disk space")
        session_id = uuid4().hex
        folder = output / session_id
        folder.mkdir()
        manifest_path = folder / "manifest.json"
        manifest = {"schema_version": 1, "session_id": session_id, "created_at": utc_now(),
                    "backend": rig.backend, "simulated": rig.backend == "simulated",
                    "status": "connecting", "requested_takes": takes,
                    "rig": asdict(rig), "cameras": {}, "takes": []}
        if manifest_extra:
            if set(manifest_extra) & set(manifest):
                raise ValueError("Extra manifest fields cannot replace capture fields")
            manifest.update(manifest_extra)
        manifest["delays_ms"] = delays_ms
        manifest["rig"]["cameras"] = list(manifest["rig"]["cameras"])
        write_manifest(manifest_path, manifest)
        workers = [] if shared_workers is None else list(shared_workers)
        log(f"Session: {folder}")
        if manifest["simulated"]:
            log("SIMULATION: generated fixture images; no physical cameras are used")
        try:
            if shared_workers is None:
                for spec in rig.cameras:
                    workers.append(CameraWorker(rig, spec, backend_factory))
            elif [w.spec.id for w in workers] != [s.id for s in rig.cameras]:
                raise ValueError("Shared workers do not match the requested rig")
            manifest["cameras"] = _broadcast(workers, "connect" if shared_workers is None else "health")
            write_manifest(manifest_path, manifest)
            if not all(result["ok"] for result in manifest["cameras"].values()):
                raise RuntimeError("Camera preflight failed; see manifest.cameras; nothing was triggered")
            for number in range(1, takes + 1):
                if cancel and cancel.is_set():
                    raise InterruptedError("Capture stopped by operator before the next take")
                if shutil.disk_usage(output).free < rig.minimum_free_mb * 1024 * 1024:
                    raise RuntimeError("Free disk space is below the configured minimum")
                take = {"id": f"{number:03d}", "status": "preparing", "cameras": {}}
                manifest["takes"].append(take)
                manifest["status"] = "capturing"
                take["preparation"] = _broadcast(workers, "prepare")
                write_manifest(manifest_path, manifest)
                if not all(result["ok"] for result in take["preparation"].values()):
                    raise RuntimeError("Preparation failed; this take was not triggered")
                delay = countdown if number == 1 else interval
                until = time.monotonic() + delay
                while (remaining := until - time.monotonic()) > 0:
                    if cancel and cancel.is_set():
                        raise InterruptedError("Capture stopped by operator before triggering")
                    log(f"Take {number}/{takes} in {math.ceil(remaining)} s")
                    time.sleep(min(1, remaining))
                if before_trigger:
                    before_trigger()
                # Persist uncertainty BEFORE any trigger command leaves the coordinator.
                take["status"] = "triggering"
                take["requested_at"] = utc_now()
                take["cameras"] = {w.spec.id: {"status": "unknown", "trigger_state": "unknown", "files": []}
                                   for w in workers}
                write_manifest(manifest_path, manifest)
                target = time.monotonic() + 0.25
                take["target_monotonic_ns"] = int(target * 1e9)
                dispatched = []
                for worker in workers:
                    try:
                        delay_ms = delays_ms.get(worker.spec.id, 0)
                        worker.send("capture", target=target + delay_ms / 1000, delay_ms=delay_ms,
                                    expected=worker.spec.files_per_take,
                                    event_timeout=rig.event_timeout, settle=rig.settle_seconds)
                        dispatched.append(worker)
                    except Exception as error:
                        take["cameras"][worker.spec.id].update(ok=False, error=str(error))
                for worker in dispatched:
                    try:
                        result = worker.receive()
                    except Exception as error:
                        result = {"ok": False, "error": str(error), "trigger_state": "unknown", "assets": []}
                    take["cameras"][worker.spec.id].update(result)
                    # Persist file references while all worker connections remain open.
                    write_manifest(manifest_path, manifest)
                take["status"] = "downloading"
                write_manifest(manifest_path, manifest)
                log(f"Take {number}/{takes}: downloading")
                with ThreadPoolExecutor(max_workers=rig.download_parallelism) as pool:
                    tasks = {pool.submit(_download_camera, worker, dict(take["cameras"][worker.spec.id], files=[]),
                                         folder / "takes" / take["id"] / worker.spec.id,
                                         rig.download_retries): worker.spec.id for worker in workers}
                    for task in as_completed(tasks):
                        take["cameras"][tasks[task]] = task.result()
                        write_manifest(manifest_path, manifest)
                take["status"] = "complete" if all(
                    entry["status"] == "complete" for entry in take["cameras"].values()) else "incomplete"
                starts = [entry["trigger_started_ns"] for entry in take["cameras"].values() if "trigger_started_ns" in entry]
                take["command_start_spread_ms"] = (max(starts) - min(starts)) / 1e6 if len(starts) == len(workers) else None
                take["completed_at"] = utc_now()
                write_manifest(manifest_path, manifest)
                log(f"Take {number}/{takes}: {take['status']}")
                if take["status"] != "complete":
                    manifest["status"] = "incomplete"
                    log("Series stopped: incomplete take. Capture will not be retried automatically.")
                    break
            else:
                manifest["status"] = "complete"
        except (KeyboardInterrupt, InterruptedError):
            manifest["status"] = "interrupted"
            manifest["error"] = "Interrupted; in-flight capture results may be unknown. Do not replay automatically."
            log(manifest["error"])
        except Exception as error:
            manifest["status"] = "failed"
            manifest["error"] = f"{type(error).__name__}: {error}"
            log(manifest["error"])
        finally:
            manifest["finished_at"] = utc_now()
            try:
                write_manifest(manifest_path, manifest)
            finally:
                if shared_workers is None:
                    for worker in workers:
                        worker.close()
        return manifest_path, manifest
