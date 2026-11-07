"""One persistent process and one outstanding command per physical camera."""

import multiprocessing
from pathlib import Path
import time

from .backends import create_backend, save_asset
from .config import CameraSpec, Rig


def camera_worker(connection, backend_name, spec, factory):
    backend = None
    try:
        backend = factory(backend_name, spec)
        while True:
            request = connection.recv()
            operation = request["operation"]
            try:
                if operation == "connect":
                    response = {"ok": True, "info": backend.connect()}
                elif operation == "prepare":
                    response = {"ok": True, "info": backend.prepare()}
                elif operation == "health":
                    response = {"ok": True, "info": backend.health()}
                elif operation == "settings":
                    response = {"ok": True, "settings": backend.settings()}
                elif operation == "set_settings":
                    response = {"ok": True, "result": backend.set_settings(request["values"])}
                elif operation == "restore_settings":
                    response = {"ok": True, "result": backend.set_settings(request["values"], enforce_manual=False)}
                elif operation == "capture":
                    # monotonic() shares a clock across processes on supported hosts.
                    time.sleep(max(0, request["target"] - time.monotonic()))
                    started = time.monotonic_ns()
                    timing = {"trigger_started_ns": started,
                              "scheduled_delay_ms": request.get("delay_ms", 0),
                              "command_lateness_ms": (started / 1e9 - request["target"]) * 1000}
                    try:
                        backend.trigger()
                    except Exception as error:
                        # A failed/timeout response does not prove no exposure happened.
                        observed = backend.collect(request.get("expected", spec.files_per_take), request["event_timeout"], request["settle"])
                        response = {**observed, "ok": False, "trigger_state": "unknown",
                                    "error": f"Trigger response uncertain: {error}", **timing}
                    else:
                        ended = time.monotonic_ns()
                        response = {**backend.collect(request.get("expected", spec.files_per_take), request["event_timeout"], request["settle"]),
                                    **timing, "trigger_returned_ns": ended,
                                    "trigger_state": "acknowledged"}
                elif operation == "download":
                    response = {"ok": True, "file": save_asset(backend, request["asset"], Path(request["destination"]))}
                elif operation == "close":
                    break
                else:
                    raise ValueError(f"Unknown worker operation {operation}")
            except Exception as error:
                response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
            connection.send(response)
    except (EOFError, BrokenPipeError):
        pass
    finally:
        if backend is not None:
            try:
                backend.close()
            except Exception:
                pass
        connection.close()


class CameraWorker:
    def __init__(self, rig: Rig, spec: CameraSpec, factory=create_backend):
        self.spec = spec
        self.timeout = rig.command_timeout
        context = multiprocessing.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=camera_worker,
                                       args=(child, rig.backend, spec, factory), daemon=True)
        self.pending = False
        self.deadline = 0.0
        self.process.start()
        child.close()

    def send(self, operation: str, **fields) -> None:
        if self.pending:
            raise RuntimeError("Camera already has an outstanding command")
        if not self.process.is_alive():
            raise RuntimeError(f"Camera worker {self.spec.id} is not running")
        self.connection.send({"operation": operation, **fields})
        self.pending = True
        self.deadline = time.monotonic() + self.timeout

    def receive(self) -> dict:
        if not self.pending:
            raise RuntimeError("No camera command is pending")
        try:
            remaining = max(0, self.deadline - time.monotonic())
            if not self.connection.poll(remaining):
                self.stop()
                raise TimeoutError(f"Camera worker {self.spec.id} timed out; do not repeat capture automatically")
            return self.connection.recv()
        except EOFError as error:
            raise RuntimeError(f"Camera worker {self.spec.id} exited; capture outcome may be unknown") from error
        finally:
            self.pending = False

    def call(self, operation: str, **fields) -> dict:
        self.send(operation, **fields)
        return self.receive()

    def stop(self) -> None:
        if self.process.is_alive():
            self.process.terminate()
            self.process.join(2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(2)

    def close(self) -> None:
        if self.process.is_alive() and not self.pending:
            try:
                self.connection.send({"operation": "close"})
                self.process.join(2)
            except (BrokenPipeError, OSError):
                pass
        self.stop()
        self.connection.close()
