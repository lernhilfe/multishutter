"""Persistent UI roster; USB addresses are discovery hints, never camera identity."""
from dataclasses import asdict
import re

from .config import CameraSpec


def validate_registry(records, backend):
    if not isinstance(records, list) or len(records) > 32:
        raise ValueError("Das Rig darf höchstens 32 Kameras enthalten")
    result, ids, positions, identities = [], set(), set(), set()
    fields = set(asdict(CameraSpec("camera", 0))) | {"enabled"}
    for record in records:
        if not isinstance(record, dict) or set(record) - fields:
            raise ValueError("Ungültiger Kameraeintrag")
        try:
            spec = CameraSpec(**{key: value for key, value in record.items() if key != "enabled"})
        except TypeError as error:
            raise ValueError("Unvollständiger Kameraeintrag") from error
        if not isinstance(spec.id, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", spec.id):
            raise ValueError("Kamera-ID: 1–64 Buchstaben, Ziffern, Bindestriche oder Unterstriche")
        if spec.id in ids or type(spec.position) is not int or spec.position < 0 or spec.position in positions:
            raise ValueError("Kamera-ID und Position müssen eindeutig sein")
        if type(spec.files_per_take) is not int or spec.files_per_take not in {1, 2}:
            raise ValueError("Erwartete Dateien pro Take: 1 oder 2")
        if not all(isinstance(value, str) for value in (spec.model, spec.port, spec.serial)):
            raise ValueError("Ungültige Kameraidentität")
        if backend == "gphoto2" and (not spec.model or not re.fullmatch(r"usb:\d+,\d+", spec.port)):
            raise ValueError("USB-Kamera benötigt Modell und gültige USB-Adresse")
        enabled = record.get("enabled", True)
        if type(enabled) is not bool:
            raise ValueError("Aktivierungsstatus muss ein Wahrheitswert sein")
        identity = (spec.model, spec.serial)
        if spec.serial and identity in identities:
            raise ValueError("Diese Kamera ist bereits im Rig eingetragen")
        ids.add(spec.id)
        positions.add(spec.position)
        if spec.serial:
            identities.add(identity)
        result.append({**asdict(spec), "enabled": enabled})
    return sorted(result, key=lambda record: record["position"])
