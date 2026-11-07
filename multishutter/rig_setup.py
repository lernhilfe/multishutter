"""Generate a rig only after an operator explicitly chooses physical ordering."""

import json
from pathlib import Path
from uuid import uuid4

from .config import load_rig


def configure_rig(cameras: list[dict], backend: str, destination: Path, read=None, emit=print) -> dict:
    if not cameras:
        raise RuntimeError("No cameras detected. Check PC Remote mode and, on Windows, USB forwarding to WSL2.")
    read = read or input
    for number, camera in enumerate(cameras, 1):
        emit(f"{number}: {camera['model']} | {camera['port']}")
    emit("Enter camera numbers in physical order LEFT to RIGHT (e.g. 2,1,3). Select a subset if needed.")
    try:
        order = [int(item.strip()) for item in read("Order: ").split(",")]
    except (ValueError, EOFError) as error:
        raise ValueError("Enter a nonempty comma-separated list of camera numbers") from error
    if len(set(order)) != len(order) or not order or any(index < 1 or index > len(cameras) for index in order):
        raise ValueError("Camera numbers must be unique and refer to the displayed list")
    text = [f"backend = {json.dumps(backend)}", "", "# Physical order explicitly selected by the operator.",
            "# USB addresses may change after reconnecting. Verify before each session."]
    for position, number in enumerate(order):
        camera = cameras[number - 1]
        text.extend(["", "[[cameras]]", f'id = "camera-{position + 1:02d}"', f"position = {position}",
                     f"port = {json.dumps(camera['port'])}", f"model = {json.dumps(camera['model'])}",
                     "files_per_take = 1"])
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
    backup = None
    try:
        temporary.write_text("\n".join(text) + "\n", encoding="utf-8")
        load_rig(temporary)
        if destination.exists():
            backup = destination.with_name(destination.name + f".{uuid4().hex[:8]}.bak")
            backup.write_bytes(destination.read_bytes())
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"config": str(destination), "cameras": len(order), "backup": str(backup) if backup else None}
