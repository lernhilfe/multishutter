"""Single-owner camera service. All USB operations pass through one control queue."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import threading
import time
from uuid import uuid4

from .calibration import decode_target, summarize_measurements
from .camera_settings import plan_settings, image_format, image_format_plan
from .capture import run_capture
from .config import CameraSpec, load_rig
from .rig_management import validate_registry
from .media import make_preview
from .processing import RenderQueue, validate_processing
from .runtime import discover_cameras
from .storage import rig_lock, utc_now, write_manifest
from .workers import CameraWorker

DEFAULT_OPTIONS = {
    "auto_reconnect": True, "disconnect_policy": "lockout", "reconnect_interval_s": 3.0,
    "health_interval_s": 2.0, "health_timeout_s": 4.0, "connection_timeout_s": 45.0,
    "reapply_settings": True, "invalidate_calibration_on_disconnect": True,
    "compensation_enabled": False, "delays_ms": {},
}


class ServiceError(RuntimeError):
    pass


def validate_options(options, ids):
    result = {**DEFAULT_OPTIONS, **options}
    if set(options) - set(DEFAULT_OPTIONS):
        raise ValueError("Unbekannte Systemeinstellung")
    for name in ("auto_reconnect", "reapply_settings", "invalidate_calibration_on_disconnect", "compensation_enabled"):
        if type(result[name]) is not bool:
            raise ValueError(f"{name} muss ein Wahrheitswert sein")
    if result["disconnect_policy"] not in {"lockout", "warn"}:
        raise ValueError("Trennungsverhalten muss lockout oder warn sein")
    for key, low, high in (("reconnect_interval_s", 1, 60), ("health_interval_s", 1, 30),
                           ("health_timeout_s", .5, 15), ("connection_timeout_s", 5, 90)):
        value = result[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} muss zwischen {low} und {high} liegen")
    if not isinstance(result["delays_ms"], dict) or set(result["delays_ms"]) - set(ids):
        raise ValueError("Unbekannte Kamera in Verzögerungswerten")
    for value in result["delays_ms"].values():
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1000:
            raise ValueError("Auslöseverzögerungen müssen zwischen 0 und 1000 ms liegen")
    return result


class RigService:
    def __init__(self, rig_path, state_dir, output, lock_path=None, worker_factory=CameraWorker,
                 discovery=discover_cameras):
        self.rig = load_rig(Path(rig_path))
        self.state_dir, self.output = Path(state_dir), Path(output)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "jobs").mkdir(exist_ok=True)
        if os.environ.get("HOME", "").startswith(str(self.state_dir)):
            Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock_path, self.worker_factory, self.discovery = lock_path, worker_factory, discovery
        self.mutex = threading.RLock()
        self.stop_event, self.cancel_event = threading.Event(), threading.Event()
        self.commands, self.workers = queue.Queue(), {}
        self.retry_at, self.forced_offline = {}, set()
        saved = {}
        path = self.state_dir / "settings.json"
        if path.exists():
            saved = json.loads(path.read_text())
        self.registry = validate_registry(saved.get("registry", [
            {**asdict(spec), "enabled": True} for spec in self.rig.cameras]), self.rig.backend)
        if self.rig.backend == "simulated":
            for row in self.registry:
                row["model"] = row["model"] or "SIMULATED"
                row["port"] = row["port"] or f"simulated:{row['id']}"
                row["serial"] = row["serial"] or f"simulated:{row['port']}"
        self.rig = replace(self.rig, cameras=tuple(CameraSpec(**{k: v for k, v in row.items() if k != "enabled"})
                                                 for row in self.registry if row["enabled"]))
        self.rows = {row["id"]: {**row, "state": "waiting" if row["enabled"] else "disabled",
                                 "error": None, "info": {}, "settings": {},
                                 "ever_connected": False, "last_seen": None} for row in self.registry}
        self.available_cameras, self.discovered_at = [], None
        self.options = validate_options(saved.get("options", {}), self.rows)
        self.desired = saved.get("desired", {})
        self.calibration = saved.get("calibration", {"valid": False, "source": None})
        self.capture_format = saved.get('capture_format', 'jpeg')
        if self.capture_format not in {'jpeg', 'raw', 'raw+jpeg'}:
            raise ValueError('Ungültiges gespeichertes Aufnahmeformat')
        self.processing = validate_processing(saved.get('processing', {}))
        self.renderer = RenderQueue(self.output, self.event)
        self.events, self.jobs = [], {}
        self.busy, self.active_job, self.fatal = False, None, None
        self.lockout = bool(saved.get("lockout", False))
        self.thread = None
        self.target = {"seen": 0, "refresh_ms": 16.67, "visible": False}
        self.last_scan = 0

    def event(self, message, level="info"):
        with self.mutex:
            self.events.append({"at": utc_now(), "level": level, "message": str(message)})
            self.events = self.events[-200:]

    def _settings_document(self, **overrides):
        return {"schema_version": 3, "options": self.options, "desired": self.desired,
                "calibration": self.calibration, "lockout": self.lockout, "registry": self.registry,
                "capture_format": self.capture_format, "processing": self.processing, **overrides}

    def persist(self):
        with self.mutex:
            write_manifest(self.state_dir / "settings.json", self._settings_document())

    def _format_matches(self, camera_id):
        inventory = self.rows[camera_id]['settings']
        if image_format(inventory.get('imagequality', {}).get('value')) != self.capture_format:
            return False
        remote = inventory.get('pcsaveimgformat')
        expected = {'jpeg': 'JPEG Only', 'raw': 'RAW Only', 'raw+jpeg': 'RAW & JPEG'}[self.capture_format]
        return not remote or remote.get('value') == expected

    def snapshot(self):
        with self.mutex:
            ready = sum(row["state"] == "ready" for row in self.rows.values())
            format_consistent = bool(self.rig.cameras) and all(self.rows[spec.id]['state'] == 'ready' and
                                self._format_matches(spec.id) for spec in self.rig.cameras)
            return deepcopy({"backend": self.rig.backend,
                             "cameras": [self.rows[spec.id] for spec in self.rig.cameras],
                             "registered_cameras": list(self.rows.values()),
                             "available_cameras": self.available_cameras, "discovered_at": self.discovered_at,
                             "ready": ready, "total": len(self.rig.cameras), "busy": self.busy,
                             "lockout": self.lockout, "fatal": self.fatal,
                             "options": self.options, "calibration": self.calibration,
                             "capture_format": self.capture_format, "format_consistent": format_consistent,
                             "format_mismatches": [spec.id for spec in self.rig.cameras if spec.id in self.workers and not self._format_matches(spec.id)],
                             "processing": self.processing,
                             "events": self.events[-80:], "jobs": list(self.jobs.values())[-20:],
                             "active_job": self.active_job,
                             "target_ready": self.target["visible"] and time.monotonic() - self.target["seen"] < 3,
                             "capture_allowed": not self.busy and not self.fatal and ready > 0 and
                                 all(self._format_matches(spec.id) for spec in self.rig.cameras if spec.id in self.workers) and
                                 (self.options["disconnect_policy"] == "warn" or
                                  (ready == len(self.rig.cameras) and not self.lockout))})

    def start(self):
        self.renderer.start()
        self.thread = threading.Thread(target=self._run, name="camera-owner", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.cancel_event.set()
        if self.thread:
            self.thread.join(55)
        for worker in list(self.workers.values()):
            worker.close()
        self.renderer.stop()

    def submit(self, operation, payload=None):
        with self.mutex:
            if self.fatal:
                raise ServiceError(self.fatal)
            if self.busy:
                raise ServiceError("Ein Kameraauftrag läuft bereits")
            job_id = uuid4().hex
            self.jobs[job_id] = {"id": job_id, "operation": operation, "status": "queued",
                                 "created_at": utc_now(), "progress": "Wird vorbereitet", "result": None}
            self.jobs = dict(list(self.jobs.items())[-30:])
            self.cancel_event.clear()
            self.busy, self.active_job = True, job_id
            self.commands.put((job_id, operation, payload or {}))
            return deepcopy(self.jobs[job_id])

    def _run(self):
        try:
            with rig_lock(self.lock_path):
                self.event("Kameradienst gestartet. Exklusiver USB-Zugriff aktiv.")
                self._scan(force=True)
                while not self.stop_event.is_set():
                    try:
                        job_id, operation, payload = self.commands.get(timeout=.2)
                    except queue.Empty:
                        if time.monotonic() - self.last_scan >= self.options["health_interval_s"]:
                            self._scan()
                        continue
                    with self.mutex:
                        self.jobs[job_id].update(status="running", started_at=utc_now())
                    try:
                        result = self._execute(operation, payload, job_id)
                        with self.mutex:
                            failed = isinstance(result, dict) and result.get("ok") is False
                            self.jobs[job_id].update(status="failed" if failed else "complete", result=result)
                            if failed:
                                self.jobs[job_id]["error"] = result.get("error") or "Einstellungen nicht vollständig synchronisiert; Rücksetzprotokoll prüfen"
                    except Exception as error:
                        with self.mutex:
                            self.jobs[job_id].update(status="failed", error=str(error))
                        self.event(str(error), "error")
                    finally:
                        with self.mutex:
                            self.jobs[job_id]["finished_at"] = utc_now()
                            write_manifest(self.state_dir / "jobs" / f"{job_id}.json", self.jobs[job_id])
                            self.busy, self.active_job = False, None
                        self._scan()
        except Exception as error:
            with self.mutex:
                self.fatal = str(error)
            self.event(f"Kameradienst gestoppt: {error}", "error")
        finally:
            for worker in list(self.workers.values()):
                worker.close()
            self.workers.clear()

    def _lose(self, camera_id, reason):
        worker = self.workers.pop(camera_id, None)
        if worker:
            worker.close()
        with self.mutex:
            row = self.rows[camera_id]
            if row["ever_connected"] and row["state"] != "disconnected":
                self.lockout = True
                if self.options["invalidate_calibration_on_disconnect"]:
                    self.calibration["valid"] = False
                self.event(f"{camera_id}: Verbindung unterbrochen. {reason}", "warning")
                self.persist()
            row.update(state="disconnected", error=str(reason))
        self.retry_at[camera_id] = time.monotonic() + self.options["reconnect_interval_s"]

    def _call(self, worker, operation, **fields):
        try:
            return worker.call(operation, **fields)
        except Exception as error:
            return {"ok": False, "error": str(error)}

    def _scan(self, force=False):
        self.last_scan = time.monotonic()
        try:
            detected = self.discovery(self.rig.backend)
        except Exception as error:
            self.event(f"USB-Erkennung fehlgeschlagen: {error}", "error")
            with self.mutex:
                self.lockout = True
                self.persist()
            return
        present = {(row["model"], row["port"]) for row in detected}
        for camera_id, worker in list(self.workers.items()):
            if camera_id in self.forced_offline or (self.rig.backend != "simulated" and
                    (worker.spec.model, worker.spec.port) not in present):
                self._lose(camera_id, "USB-Gerät nicht mehr vorhanden")
        live = list(self.workers.items())
        if live:
            def probe(item):
                camera_id, worker = item
                previous = worker.timeout
                worker.timeout = self.options["health_timeout_s"]
                try:
                    return camera_id, self._call(worker, "health")
                finally:
                    worker.timeout = previous
            with ThreadPoolExecutor(max_workers=len(live)) as pool:
                results = list(pool.map(probe, live))
            for camera_id, result in results:
                if not result["ok"]:
                    self._lose(camera_id, result["error"])
                else:
                    with self.mutex:
                        inventory = result["info"].pop("settings", None)
                        if inventory is not None:
                            self._update_inventory(camera_id, inventory)
                        self.rows[camera_id]["last_seen"] = utc_now()
                        self.rows[camera_id]["health"] = result["info"]
        for spec in self.rig.cameras:
            if spec.id in self.workers or spec.id in self.forced_offline:
                continue
            if not force and (not self.options["auto_reconnect"] or time.monotonic() < self.retry_at.get(spec.id, 0)):
                continue
            used = {worker.spec.port for worker in self.workers.values()}
            candidates = [row for row in detected if row["model"] == spec.model and row["port"] not in used]
            if self.rig.backend == "simulated":
                candidates = [{"model": spec.model, "port": spec.port or f"simulated:{spec.id}"}]
            if not candidates:
                with self.mutex:
                    self.rows[spec.id].update(state="disconnected", error="Über USB nicht gefunden. Einschalten, PC Remote und USB-Weiterleitung an WSL prüfen.")
                self.retry_at[spec.id] = time.monotonic() + self.options["reconnect_interval_s"]
                continue
            if len(candidates) > 1 and not spec.serial:
                with self.mutex:
                    self.rows[spec.id].update(state="error", error="Mehrere gleiche Modelle: Seriennummer konfigurieren")
                continue
            with self.mutex:
                self.rows[spec.id]["state"] = "connecting"
            connected = False
            for candidate in candidates:
                resolved = replace(spec, port=candidate["port"])
                worker = self.worker_factory(replace(self.rig, command_timeout=self.options["connection_timeout_s"]), resolved)
                result = self._call(worker, "connect")
                if result["ok"]:
                    prepared = self._call(worker, "prepare")
                    inventory = self._call(worker, "settings") if prepared["ok"] else prepared
                    if not inventory["ok"]:
                        result = inventory
                    else:
                        try:
                            values = {**(self.desired.get(spec.id, {}) if self.options['reapply_settings'] else {}),
                                      **image_format_plan(inventory['settings'], self.capture_format)}
                            restored = self._call(worker, 'set_settings', values=values) if values else None
                            if restored and not restored['ok']:
                                result = restored
                            elif restored:
                                inventory['settings'] = restored['result']['settings']
                        except ValueError as error:
                            result = {'ok': False, 'error': str(error)}
                if result["ok"]:
                    self.workers[spec.id] = worker
                    self._update_inventory(spec.id, inventory["settings"])
                    with self.mutex:
                        self.rows[spec.id].update(state="ready", error=None, info=result["info"],
                                                  port=resolved.port, ever_connected=True, last_seen=utc_now())
                    self.event(f"{spec.id}: verbunden und geprüft")
                    connected = True
                    break
                worker.close()
                with self.mutex:
                    self.rows[spec.id].update(state="error", error=result["error"])
            if not connected:
                self.retry_at[spec.id] = time.monotonic() + self.options["reconnect_interval_s"]

    def _update_inventory(self, camera_id, inventory):
        quality = str(inventory.get("imagequality", {}).get("value", ""))
        remote = str(inventory.get("pcsaveimgformat", {}).get("value", ""))
        expected = 2 if "RAW+JPEG" in quality and remote not in {"JPEG Only", "RAW Only"} else 1
        worker = self.workers[camera_id]
        worker.spec = replace(worker.spec, files_per_take=expected)
        with self.mutex:
            old = {key: row["value"] for key, row in self.rows[camera_id]["settings"].items()}
            new = {key: row["value"] for key, row in inventory.items()}
            if old and old != new and self.calibration.get("valid"):
                self.calibration["valid"] = False
                self.event(f"{camera_id}: Kameraeinstellungen verändert; Zeitkorrektur pausiert", "warning")
                self.persist()
            self.rows[camera_id]["settings"] = inventory
            self.rows[camera_id]["files_per_take"] = expected

    def fingerprint(self):
        snapshot = [{"id": spec.id, "model": spec.model, "serial": spec.serial,
                     "settings": {key: row["value"] for key, row in self.rows[spec.id]["settings"].items()}}
                    for spec in self.rig.cameras]
        return hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()

    def _replace_registry(self, records):
        records = validate_registry(records, self.rig.backend)
        ids = {row["id"] for row in records}
        active = {row["id"] for row in records if row["enabled"]}
        # Commit the entire roster and its dependent settings together before
        # closing any connection. A failed disk write leaves the live rig intact.
        options = {**self.options, "compensation_enabled": False,
                   "delays_ms": {key: value for key, value in self.options["delays_ms"].items() if key in ids}}
        desired = {key: value for key, value in self.desired.items() if key in ids}
        calibration = {"valid": False, "source": None}
        rows = {}
        for record in records:
            previous = deepcopy(self.rows.get(record["id"], {}))
            rows[record["id"]] = {"error": None, "info": {}, "settings": {}, "ever_connected": False,
                                  "last_seen": None, **previous, **record}
            if not record["enabled"]:
                rows[record["id"]].update(state="disabled", error=None)
            elif record["id"] not in self.workers:
                rows[record["id"]].update(state="waiting", error=None)
        with self.mutex:
            write_manifest(self.state_dir / "settings.json", self._settings_document(
                registry=records, options=options, desired=desired, calibration=calibration, lockout=True))
            retiring = [self.workers.pop(key) for key in list(self.workers) if key not in active]
            self.registry, self.rows = records, rows
            self.options, self.desired, self.calibration = options, desired, calibration
            self.lockout = True
            self.retry_at = {key: value for key, value in self.retry_at.items() if key in ids}
            self.forced_offline.intersection_update(ids)
            self.rig = replace(self.rig, cameras=tuple(CameraSpec(**{k: v for k, v in row.items() if k != "enabled"})
                                                     for row in records if row["enabled"]))
            for spec in self.rig.cameras:
                if spec.id in self.workers:
                    self.workers[spec.id].spec = replace(self.workers[spec.id].spec, position=spec.position)
            self.available_cameras = []
            self.discovered_at = None
        for worker in retiring:
            worker.close()
        self.event("Kamera-Rig geändert. Reihenfolge prüfen und Aufnahme bewusst freigeben; Zeitkorrektur ist deaktiviert.")

    def _manage_rig(self, operation, payload):
        records = deepcopy(self.registry)
        if operation == "rig_order":
            order = payload["camera_ids"]
            if len(order) != len(records) or len(set(order)) != len(order) or set(order) != set(self.rows):
                raise ValueError("Die Reihenfolge muss jede eingetragene Kamera genau einmal enthalten")
            by_id = {row["id"]: row for row in records}
            records = [{**by_id[key], "position": position} for position, key in enumerate(order)]
        else:
            camera_id = payload["camera_id"]
            if camera_id not in self.rows:
                raise ValueError("Unbekannte Kamera")
            if operation == "rig_remove":
                records = [row for row in records if row["id"] != camera_id]
                records = [{**row, "position": position} for position, row in enumerate(records)]
            else:
                if type(payload["enabled"]) is not bool:
                    raise ValueError("Aktivierungsstatus muss ein Wahrheitswert sein")
                for row in records:
                    if row["id"] == camera_id:
                        row["enabled"] = payload["enabled"]
        if records != self.registry:
            self._replace_registry(records)
            if operation == "rig_enable" and payload["enabled"]:
                self._scan(force=True)
        return {"registry": self.registry, "active": len(self.rig.cameras)}

    def _discover_available(self):
        devices = self.discovery(self.rig.backend)
        available = []
        for device in devices:
            owned = next((key for key, worker in self.workers.items()
                          if (worker.spec.model, worker.spec.port) == (device["model"], device["port"])), None)
            available.append({"model": device["model"], "port": device["port"], "registered_id": owned})
        with self.mutex:
            self.available_cameras, self.discovered_at = available, utc_now()
        return {"cameras": available}

    def _pair_camera(self, payload):
        camera_id, model, port = payload["camera_id"], payload["model"], payload["port"]
        if camera_id in self.rows:
            raise ValueError("Diese Kamera-ID ist bereits vergeben")
        spec = CameraSpec(camera_id, max((row["position"] for row in self.registry), default=-1) + 1,
                          port=port, model=model)
        validate_registry([*self.registry, {**asdict(spec), "enabled": True}], self.rig.backend)
        if not any((row["model"], row["port"]) == (model, port) for row in self.discovery(self.rig.backend)):
            raise ServiceError("Kamera nicht mehr verfügbar. USB-Geräte erneut suchen.")
        if any(worker.spec.port == port for worker in self.workers.values()):
            raise ValueError("Diese Kamera ist bereits gekoppelt")
        worker = self.worker_factory(replace(self.rig, command_timeout=self.options["connection_timeout_s"]), spec)
        transferred = False
        try:
            result = self._call(worker, "connect")
            if not result["ok"]:
                raise ServiceError(result["error"])
            serial = str(result["info"].get("serial", "")).strip()
            if self.rig.backend == "simulated":
                serial = serial or f"simulated:{port}"
            elif not serial:
                raise ServiceError("Kamera meldet keine Seriennummer; eine eindeutige Kopplung ist nicht möglich")
            if any(row["model"] == model and (row["serial"] == serial or
                   (self.rig.backend == "simulated" and row["port"] == port)) for row in self.registry):
                raise ValueError("Diese Kamera ist bereits eingetragen; bestehenden Eintrag aktivieren oder neu verbinden")
            prepared = self._call(worker, "prepare")
            inventory = self._call(worker, "settings") if prepared["ok"] else prepared
            if not inventory["ok"]:
                raise ServiceError(inventory["error"])
            values = image_format_plan(inventory['settings'], self.capture_format)
            if values:
                applied = self._call(worker, 'set_settings', values=values)
                if not applied['ok']:
                    raise ServiceError(applied['error'])
                inventory['settings'] = applied['result']['settings']
            spec = replace(spec, serial=serial)
            self._replace_registry([*self.registry, {**asdict(spec), "enabled": True}])
            worker.spec = spec
            self.workers[camera_id] = worker
            transferred = True
            self._update_inventory(camera_id, inventory["settings"])
            with self.mutex:
                self.rows[camera_id].update(state="ready", info={**result["info"], "serial": serial},
                                           ever_connected=True, last_seen=utc_now())
            self.event(f"{camera_id}: Kamera gekoppelt und rechts im Rig angefügt")
            return {"camera_id": camera_id, "serial": serial}
        finally:
            if not transferred:
                worker.close()

    def _execute(self, operation, payload, job_id):
        if operation == 'capture_format':
            return self._apply_settings({}, family=payload['format'])
        if operation == 'processing_options':
            updated = validate_processing({**self.processing, **payload})
            with self.mutex:
                write_manifest(self.state_dir / 'settings.json', self._settings_document(processing=updated))
                self.processing = updated
            return updated
        if operation == 'process':
            return self.renderer.submit(payload['session_id'], self.processing)
        if operation == 'process_all':
            accepted, skipped = [], []
            paths = sorted(self.output.glob('*/manifest.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]
            for path in paths:
                try:
                    existing = path.with_name('processing.json')
                    if existing.exists() and json.loads(existing.read_text()).get('status') in {'queued', 'running', 'complete'}:
                        continue
                    self.renderer.submit(path.parent.name, self.processing)
                    accepted.append(path.parent.name)
                except (ValueError, OSError, KeyError) as error:
                    skipped.append({'session_id': path.parent.name, 'reason': str(error)})
            return {'accepted': accepted, 'skipped': skipped}
        if operation == "rig_discover":
            return self._discover_available()
        if operation == "rig_pair":
            return self._pair_camera(payload)
        if operation in {"rig_enable", "rig_remove", "rig_order"}:
            return self._manage_rig(operation, payload)
        if operation == "reconnect":
            active = [spec.id for spec in self.rig.cameras]
            explicit = payload.get("camera_ids", [])
            targets = explicit or active
            if len(set(targets)) != len(targets) or set(targets) - set(active):
                raise ValueError("Kamera ist unbekannt oder deaktiviert")
            for camera_id in explicit:
                self._lose(camera_id, "Manuelle Neuverbindung")
            self._scan(force=True)
            missing = [key for key in targets if key not in self.workers]
            message = None
            if missing:
                details = "; ".join(f"{key}: {self.rows[key]['error'] or 'nicht bereit'}" for key in missing)
                message = f"Verbindung unvollständig ({len(self.workers)}/{len(active)} aktiv). {details}"
                self.event(message, "warning")
            elif not targets:
                message = "Keine aktive Kamera eingetragen. Kameraverwaltung öffnen und eine Kamera aktivieren oder koppeln."
            else:
                self.event(f"Verbindungen geprüft: {len(self.workers)}/{len(active)} aktive Kameras bereit")
            return {"ok": bool(targets) and not missing, "ready": len(self.workers), "total": len(active),
                    "failed_camera_ids": missing, "error": message}
        if operation == "acknowledge":
            self._scan()
            if not self.rig.cameras or len(self.workers) != len(self.rig.cameras):
                raise ServiceError("Mindestens eine aktive Kamera und alle aktiven Verbindungen müssen bereit sein")
            with self.mutex:
                self.lockout = False
                self.persist()
            self.event("Verbindungen geprüft; Aufnahmesperre aufgehoben")
            return {"lockout": False}
        if operation == "options":
            options = validate_options({**self.options, **payload}, self.rows)
            if "delays_ms" in payload:
                with self.mutex:
                    self.calibration = {"valid": True, "source": "manual", "fingerprint": self.fingerprint(),
                                        "created_at": utc_now()}
            with self.mutex:
                self.options = options
            self.persist()
            self.event("Systemeinstellungen gespeichert")
            return options
        if operation == "settings":
            return self._apply_settings(payload)
        if operation == "read_settings":
            self._scan()
            return {"ready": len(self.workers)}
        if operation == "capture" or operation == "calibration":
            return self._capture(payload, job_id, calibration=operation == "calibration")
        if operation == "analyze":
            return self._analyze(payload)
        if operation == "apply_calibration":
            report = self.calibration.get("proposal")
            if not report or self.calibration.get("fingerprint") != self.fingerprint():
                raise ServiceError("Messung fehlt oder Kameraeinstellungen wurden seitdem verändert")
            with self.mutex:
                self.options["delays_ms"] = {key: row["delay_ms"] for key, row in report["cameras"].items()}
                self.options["compensation_enabled"] = True
                self.calibration.update(valid=True, source="optical")
            self.persist()
            self.event("Optische Verzögerungskorrektur aktiviert")
            return self.calibration
        if operation == "simulate_disconnect" and self.rig.backend == "simulated":
            camera_id = payload["camera_id"]
            if camera_id not in self.rows:
                raise ValueError("Unbekannte Kamera")
            if payload["offline"]:
                self.forced_offline.add(camera_id)
            else:
                self.forced_offline.discard(camera_id)
            self._scan(force=True)
            return {"camera_id": camera_id, "offline": payload["offline"]}
        raise ValueError("Unbekannter Auftrag")

    def _apply_settings(self, payload, family=None):
        active = [spec.id for spec in self.rig.cameras]
        ids, values = payload.get("camera_ids") or active, payload.get('values', {})
        if family is None and set(values) & {'imagequality', 'pcsaveimgformat'}:
            raise ValueError('Aufnahmeformat im gemeinsamen Formatbereich für alle aktiven Kameras einstellen')
        if not ids or len(set(ids)) != len(ids) or set(ids) - set(active):
            raise ValueError("Kameraauswahl ist ungültig")
        inventories, originals, requests = {}, {}, {}
        for camera_id in ids:
            if camera_id not in self.workers:
                raise ServiceError(f"{camera_id}: nicht verbunden; keine Kameraeinstellung geändert")
            result = self._call(self.workers[camera_id], "settings")
            if not result["ok"]:
                raise ServiceError(result["error"])
            inventories[camera_id] = result["settings"]
            requests[camera_id] = image_format_plan(result['settings'], family) if family else values
            plan = plan_settings(result["settings"], requests[camera_id]) if requests[camera_id] else {}
            originals[camera_id] = {key: result["settings"][key]["value"] for key in plan}
        def apply(camera_id):
            return camera_id, self._call(self.workers[camera_id], "set_settings", values=requests[camera_id]) if requests[camera_id] else {
                'ok': True, 'result': {'applied': {}, 'settings': inventories[camera_id]}}
        with ThreadPoolExecutor(max_workers=len(ids)) as pool:
            results = dict(pool.map(apply, ids))
        if not all(result["ok"] for result in results.values()):
            rollback = {key: self._call(self.workers[key], "restore_settings", values=originals[key])
                        if originals[key] else {'ok': True} for key in ids}
            for key in ids:
                restored = self._call(self.workers[key], "settings")
                if restored["ok"]:
                    self._update_inventory(key, restored["settings"])
            # Rollback is best-effort; expose every failure instead of claiming atomicity.
            self.event("Gemeinsame Kameraeinstellung fehlgeschlagen; Rücksetzen angefordert", "error")
            return {"ok": False, "results": results, "rollback": rollback}
        with self.mutex:
            for key, result in results.items():
                self._update_inventory(key, result["result"]["settings"])
                self.desired[key] = {**self.desired.get(key, {}), **result["result"]["applied"]}
            self.calibration["valid"] = False
            if family:
                self.capture_format = family
        self.persist()
        self.event(f"Einstellungen für {len(ids)} Kameras parallel gesetzt und rückgelesen")
        return {"ok": True, "results": results}

    def _capture(self, payload, job_id, calibration=False):
        self._scan()
        missing = [spec.id for spec in self.rig.cameras if spec.id not in self.workers]
        if self.options["disconnect_policy"] == "lockout" and (missing or self.lockout):
            raise ServiceError("Aufnahme gesperrt: Verbindungen prüfen und Sperre bestätigen")
        if missing and (not payload.get("allow_partial") or calibration):
            raise ServiceError("Kameras fehlen. Teilaufnahme im Warnmodus ausdrücklich bestätigen")
        if not self.workers:
            raise ServiceError("Keine Kamera aufnahmebereit")
        mismatched = [spec.id for spec in self.rig.cameras if spec.id in self.workers and not self._format_matches(spec.id)]
        if mismatched:
            raise ServiceError('Aufnahmeformat abweichend: ' + ', '.join(mismatched) + '. Gemeinsames Format in den Kameraeinstellungen anwenden.')
        if calibration and (self.rig.backend == "simulated" or not self.snapshot()["target_ready"]):
            raise ServiceError("Für die optische Messung das Testmuster sichtbar öffnen und alle Kameras darauf ausrichten")
        selected = [self.workers[spec.id] for spec in self.rig.cameras if spec.id in self.workers]
        rig = replace(self.rig, cameras=tuple(worker.spec for worker in selected))
        valid = self.calibration.get("valid") and self.calibration.get("fingerprint") == self.fingerprint()
        delays = {worker.spec.id: self.options["delays_ms"].get(worker.spec.id, 0) for worker in selected} \
            if self.options["compensation_enabled"] and valid and not calibration else {}
        initial_ids = [worker.spec.id for worker in selected]
        def guard():
            if self.stop_event.is_set() or self.cancel_event.is_set():
                raise InterruptedError("Auftrag vor der Auslösung gestoppt")
            if self.rig.backend != "simulated":
                present = {(row["model"], row["port"]) for row in self.discovery(self.rig.backend)}
                lost = [w.spec.id for w in selected if (w.spec.model, w.spec.port) not in present]
                if lost:
                    for camera_id in lost:
                        self._lose(camera_id, "USB-Trennung während des Countdowns")
                    raise ServiceError("USB-Verbindung vor Auslösung verloren; dieser Take wurde nicht ausgelöst")
            if calibration and not self.snapshot()["target_ready"]:
                raise ServiceError("Testmuster ist nicht mehr sichtbar oder meldet sich nicht")
        def progress(message):
            match = re.match(r"Take (\d+)/(\d+)(.*)", message)
            if message.startswith("Session:"):
                message = "Neue Aufnahmesitzung vorbereitet"
            elif match:
                number, total, suffix = match.groups()
                suffix = suffix.replace("downloading", "Bilder werden übertragen").replace("incomplete", "unvollständig").replace("complete", "vollständig")
                message = f"Aufnahme {number} von {total}{suffix}"
            with self.mutex:
                self.jobs[job_id]["progress"] = message
            self.event(message)
        path, manifest = run_capture(rig, self.output, takes=payload.get("takes", 5 if calibration else 1),
                                     countdown=payload.get("countdown", 3), interval=payload.get("interval", 1),
                                     log=progress, shared_workers=selected, delays_ms=delays,
                                     before_trigger=guard, cancel=self.cancel_event,
                                     manifest_extra={"requested_camera_ids": [spec.id for spec in self.rig.cameras],
                                                     "capture_format": self.capture_format,
                                                     "disabled_cameras": [row["id"] for row in self.registry if not row["enabled"]],
                                                     "excluded_cameras": missing,
                                                     "calibration_capture": calibration, "settings_fingerprint": self.fingerprint()})
        for take in manifest["takes"]:
            for entry in take["cameras"].values():
                for record in entry["files"]:
                    if record["status"] == "complete":
                        try:
                            make_preview(record["path"])
                        except Exception as error:
                            self.event(f"Vorschau nicht verfügbar: {error}", "warning")
        if manifest["status"] != "complete":
            raise ServiceError(f"Aufnahme {manifest['status']}: {manifest.get('error') or 'Bilder fehlen'}; Sitzung {manifest['session_id']}")
        self.event(f"Sitzung {manifest['session_id'][:8]} vollständig: {len(manifest['takes'])} Takes")
        if not calibration and self.processing['auto_enabled']:
            try:
                self.renderer.submit(manifest['session_id'], self.processing)
            except (ValueError, OSError) as error:
                self.event(f'Automatische Verarbeitung nicht gestartet: {error}', 'warning')
        return {"session_id": manifest["session_id"], "status": manifest["status"], "camera_ids": initial_ids,
                "takes": len(manifest["takes"]), "calibration": calibration}

    def _analyze(self, payload):
        session_id = payload["session_id"]
        if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
            raise ValueError("Ungültige Sitzung")
        path = self.output / session_id / "manifest.json"
        manifest = json.loads(path.read_text())
        if not manifest.get("calibration_capture") or manifest["status"] != "complete":
            raise ValueError("Eine vollständige optische Messsitzung auswählen")
        if manifest.get("settings_fingerprint") != self.fingerprint():
            raise ServiceError("Kameraeinstellungen wurden seit der Messung verändert")
        ids = [spec.id for spec in self.rig.cameras]
        rows, rejected = [], []
        for take in manifest["takes"]:
            decoded = {}
            try:
                for camera_id in ids:
                    files = take["cameras"][camera_id]["files"]
                    candidates = sorted(files, key=lambda row: Path(row["path"]).suffix.lower() not in {".jpg", ".jpeg", ".png"})
                    source = Path(candidates[0]["path"]).resolve()
                    if not source.is_relative_to(self.output.resolve()):
                        raise ValueError("Messdatei liegt außerhalb des Aufnahmeordners")
                    decoded[camera_id] = decode_target(source)
                rows.append(decoded)
            except Exception as error:
                rejected.append({"take": take["id"], "reason": str(error)})
        report = summarize_measurements(rows, ids, self.target["refresh_ms"])
        report.update(session_id=session_id, rejected=rejected)
        with self.mutex:
            self.calibration = {"valid": False, "source": "proposal", "proposal": report,
                                "fingerprint": self.fingerprint(), "created_at": utc_now()}
        self.persist()
        self.event("Optische Messung ausgewertet; Korrektur wartet auf Übernahme")
        return report
