"""Local HTTP interface; one owner service, one Uvicorn worker, no USB in requests."""
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .media import make_preview, published_source
from .processing import validate_processing
from .service import RigService, ServiceError, validate_options

STATIC = Path(__file__).parent / "static"


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CaptureBody(Body):
    takes: int = Field(1, ge=1, le=100)
    countdown: float = Field(3, ge=0, le=3600)
    interval: float = Field(1, ge=0, le=3600)
    allow_partial: bool = False


class ReconnectBody(Body):
    camera_ids: list[str] = Field(default_factory=list, max_length=32)


class RigOrderBody(Body):
    camera_ids: list[str] = Field(max_length=32)


class RigCameraBody(Body):
    camera_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class RigEnableBody(RigCameraBody):
    enabled: bool = Field(strict=True)


class RigPairBody(RigCameraBody):
    model: str = Field(min_length=1, max_length=200)
    port: str = Field(min_length=1, max_length=100)


class CameraSettingsBody(Body):
    camera_ids: list[str] = Field(default_factory=list, max_length=32)
    values: dict[str, str | float] = Field(min_length=1, max_length=10)


class FormatBody(Body):
    format: str = Field(pattern=r'^(jpeg|raw|raw\+jpeg)$')


class TargetBody(Body):
    visible: bool
    refresh_ms: float = Field(16.67, ge=5, le=100)


class AnalysisBody(Body):
    session_id: str = Field(pattern="^[a-f0-9]{32}$")


class SimulateBody(Body):
    camera_id: str
    offline: bool


def create_app(service=None):
    service = service or RigService(os.environ.get("MULTISHUTTER_RIG", "config/rig.local.toml"),
                                   os.environ.get("MULTISHUTTER_STATE", "data"),
                                   os.environ.get("MULTISHUTTER_CAPTURES", "captures"))

    @asynccontextmanager
    async def lifespan(app):
        service.start()
        yield
        service.stop()

    app = FastAPI(title="MultiShutter", version="0.3.0", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.rig = service

    @app.middleware("http")
    async def same_origin(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("x-multishutter") != "1":
                return JSONResponse({"detail": "X-MultiShutter request header required"}, status_code=403)
            origin = request.headers.get("origin")
            if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
                return JSONResponse({"detail": "Cross-origin control requests are not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(ServiceError)
    async def conflict(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ValueError)
    async def bad_request(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/api/health")
    def health():
        state = service.snapshot()
        return JSONResponse({"ok": not state["fatal"], "app": "multishutter", "backend": state["backend"]},
                            status_code=503 if state["fatal"] else 200)

    @app.get("/api/state")
    def state():
        return service.snapshot()

    @app.post("/api/rig/discover", status_code=202)
    def rig_discover():
        return service.submit("rig_discover")

    @app.post("/api/rig/pair", status_code=202)
    def rig_pair(body: RigPairBody):
        return service.submit("rig_pair", body.model_dump())

    @app.put("/api/rig/order", status_code=202)
    def rig_order(body: RigOrderBody):
        return service.submit("rig_order", body.model_dump())

    @app.put("/api/rig/enabled", status_code=202)
    def rig_enabled(body: RigEnableBody):
        return service.submit("rig_enable", body.model_dump())

    @app.post("/api/rig/remove", status_code=202)
    def rig_remove(body: RigCameraBody):
        return service.submit("rig_remove", body.model_dump())

    @app.post("/api/capture", status_code=202)
    def capture(body: CaptureBody):
        return service.submit("capture", body.model_dump())

    @app.post("/api/stop")
    def stop():
        service.cancel_event.set()
        return {"ok": True, "note": "Stoppt vor dem nächsten Auslösen; laufende Bilder werden noch gesichert"}

    @app.post("/api/reconnect", status_code=202)
    def reconnect(body: ReconnectBody):
        return service.submit("reconnect", body.model_dump())

    @app.post("/api/acknowledge", status_code=202)
    def acknowledge():
        return service.submit("acknowledge")

    @app.put("/api/options", status_code=202)
    def options(body: dict):
        validate_options({**service.options, **body}, service.rows)
        return service.submit("options", body)

    @app.post("/api/camera-settings", status_code=202)
    def camera_settings(body: CameraSettingsBody):
        return service.submit("settings", body.model_dump())

    @app.post("/api/camera-settings/refresh", status_code=202)
    def read_settings():
        return service.submit("read_settings")

    @app.post('/api/camera-format', status_code=202)
    def camera_format(body: FormatBody):
        return service.submit('capture_format', body.model_dump())

    @app.put('/api/processing/options', status_code=202)
    def processing_options(body: dict):
        validate_processing({**service.processing, **body})
        return service.submit('processing_options', body)

    @app.post('/api/processing/render', status_code=202)
    def render(body: AnalysisBody):
        return service.submit('process', body.model_dump())

    @app.post('/api/processing/all', status_code=202)
    def render_all():
        return service.submit('process_all')

    @app.post("/api/calibration/target")
    def target(body: TargetBody):
        with service.mutex:
            service.target = {**body.model_dump(), "seen": time.monotonic()}
        return {"ok": True}

    @app.post("/api/calibration/capture", status_code=202)
    def calibration_capture(body: CaptureBody):
        if not 3 <= body.takes <= 20:
            raise ValueError("Optische Messung benötigt 3 bis 20 Aufnahmerunden")
        return service.submit("calibration", body.model_dump())

    @app.post("/api/calibration/analyze", status_code=202)
    def analyze(body: AnalysisBody):
        return service.submit("analyze", body.model_dump())

    @app.post("/api/calibration/apply", status_code=202)
    def apply_calibration():
        return service.submit("apply_calibration")

    @app.post("/api/simulate/disconnect", status_code=202)
    def simulate(body: SimulateBody):
        if service.rig.backend != "simulated":
            raise HTTPException(404)
        return service.submit("simulate_disconnect", body.model_dump())

    def session_path(session_id):
        if not re.fullmatch("[a-f0-9]{32}", session_id):
            raise HTTPException(404)
        return service.output / session_id / "manifest.json"

    def summarize(path):
        document = json.loads(path.read_text())
        files = []
        positions = {row["id"]: row["position"] for row in document.get("rig", {}).get("cameras", [])}
        for take in document["takes"]:
            for camera_id, entry in sorted(take["cameras"].items(), key=lambda item: positions.get(item[0], 999)):
                for record in entry.get("files", []):
                    if record.get("status") != "complete":
                        continue
                    source = published_source(service.output, path, take["id"], camera_id, record)
                    if source is None:
                        continue
                    relative = source.relative_to(service.output.resolve()).as_posix()
                    files.append({"camera_id": camera_id, "take": take["id"], "name": source.name,
                                  "size": record["size"], "sha256": record["sha256"],
                                  "download": f"/api/files/{relative}", "preview": f"/api/previews/{relative}"})
        return {"session_id": document["session_id"], "created_at": document["created_at"],
                "status": document["status"], "simulated": document["simulated"],
                "takes": len(document["takes"]), "requested_takes": document["requested_takes"],
                "calibration": document.get("calibration_capture", False), "files": files,
                "error": document.get("error"), "excluded_cameras": document.get("excluded_cameras", []),
                "disabled_cameras": document.get("disabled_cameras", []),
                "processing": processing_state(path.parent)}

    def processing_state(folder):
        state_path = folder / 'processing.json'
        if not state_path.is_file():
            return {'status': 'not_started', 'outputs': []}
        state = json.loads(state_path.read_text())
        for take in state.get('outputs', []):
            for kind in ('video', 'gif'):
                record = take.get(kind)
                if record:
                    record['url'] = '/api/renders/' + record['path']
        return state

    @app.get("/api/sessions")
    def sessions():
        paths = sorted(service.output.glob("*/manifest.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:50]
        result = []
        for path in paths:
            try:
                result.append(summarize(path))
            except (OSError, ValueError, KeyError):
                continue
        return result

    @app.get("/api/sessions/{session_id}")
    def session(session_id: str):
        path = session_path(session_id)
        if not path.is_file():
            raise HTTPException(404)
        return summarize(path)

    @app.get("/api/sessions/{session_id}/manifest")
    def manifest(session_id: str):
        path = session_path(session_id)
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="application/json", filename=f"{session_id}.json")

    def owned_file(relative):
        source = (service.output / relative).resolve()
        if not source.is_relative_to(service.output.resolve()) or not source.is_file():
            raise HTTPException(404)
        # Only manifest-published originals can be downloaded or previewed.
        parts = Path(relative).parts
        if len(parts) != 5 or parts[1] != "takes":
            raise HTTPException(404)
        manifest = session_path(parts[0])
        if not manifest.is_file():
            raise HTTPException(404)
        data = summarize(manifest)
        if not any(row["download"] == f"/api/files/{relative}" for row in data["files"]):
            raise HTTPException(404)
        return source

    @app.get("/api/files/{relative:path}")
    def file(relative: str):
        source = owned_file(relative)
        return FileResponse(source, filename=source.name)

    @app.get("/api/previews/{relative:path}")
    def preview(relative: str):
        source = owned_file(relative)
        try:
            return FileResponse(make_preview(source), media_type="image/jpeg")
        except Exception:
            raise HTTPException(422, "Für diese Datei ist keine Vorschau verfügbar")

    @app.get('/api/renders/{relative:path}')
    def rendered_file(relative: str):
        parts = Path(relative).parts
        if (len(parts) != 4 or parts[1] != 'renders' or not re.fullmatch('[a-f0-9]{32}', parts[2])
                or not re.fullmatch(r'\d{3,6}\.(mp4|gif)', parts[3])):
            raise HTTPException(404)
        manifest = session_path(parts[0])
        source = (service.output / relative).resolve()
        if not manifest.exists() or not source.is_relative_to(service.output.resolve()) or not source.is_file():
            raise HTTPException(404)
        state = processing_state(manifest.parent)
        if not any(row.get(kind, {}).get('path') == relative for row in state.get('outputs', []) for kind in ('video','gif')):
            raise HTTPException(404)
        return FileResponse(source, media_type='video/mp4' if source.suffix == '.mp4' else 'image/gif',
                            headers={'Cache-Control': 'private, max-age=86400'})

    app.mount("/assets", StaticFiles(directory=STATIC), name="assets")

    @app.get("/target")
    def target_page():
        return FileResponse(STATIC / "target.html")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    return app
