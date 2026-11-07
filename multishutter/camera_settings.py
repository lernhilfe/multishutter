"""Bounded camera controls. Storage deletion and firmware operations are never exposed."""
import math
import re

CONTROL_KEYS = ("expprogram", "iso", "shutterspeed", "f-number", "whitebalance",
                "colortemperature", "focusmode", "capturemode", "imagequality", "pcsaveimgformat")


def image_format(value):
    value = str(value).upper()
    if 'RAW' in value:
        return 'raw+jpeg' if 'JPEG' in value else 'raw'
    if 'JPEG' in value or value in {'FINE', 'EXTRA FINE', 'STANDARD'}:
        return 'jpeg'
    return None


def image_format_plan(inventory, family):
    if family not in {'jpeg', 'raw', 'raw+jpeg'}:
        raise ValueError('Aufnahmeformat muss JPEG, RAW oder RAW+JPEG sein')
    quality = inventory.get('imagequality', {})
    choices = [value for value in quality.get('choices', []) if image_format(value) == family]
    if not choices:
        raise ValueError(f'Kamera unterstützt das gemeinsame Format {family.upper()} nicht')
    preferred = ('JPEG', 'Fine', 'Extra Fine', 'Standard') if family == 'jpeg' else ()
    value = next((value for value in preferred if value in choices), choices[0])
    plan = {}
    if quality.get('readonly'):
        if image_format(quality.get('value')) != family:
            raise ValueError('Aufnahmeformat ist an dieser Kamera nicht fernsteuerbar')
    else:
        plan['imagequality'] = value
    remote = inventory.get('pcsaveimgformat')
    if remote:
        value = {'jpeg': 'JPEG Only', 'raw': 'RAW Only', 'raw+jpeg': 'RAW & JPEG'}[family]
        if value not in remote.get('choices', []):
            raise ValueError('Kamera kann das gemeinsame Übertragungsformat nicht einstellen')
        if remote.get('readonly'):
            if remote.get('value') != value:
                raise ValueError('Übertragungsformat ist nicht fernsteuerbar')
        else:
            plan['pcsaveimgformat'] = value
    return plan


def manual_mode(value):
    return str(value).strip().lower() in {"m", "manual", "manual exposure", "manual exposure (m)"}


def normalized(key, value):
    value = str(value).strip()
    if key == "expprogram" and manual_mode(value):
        return "M"
    if key == "f-number":
        return re.sub(r"^f/?", "", value.lower())
    return value


def plan_settings(inventory, values, enforce_manual=True):
    if not values or set(values) - set(CONTROL_KEYS):
        raise ValueError("Choose supported camera settings")
    planned = {}
    for key, requested in values.items():
        widget = inventory.get(key)
        if not widget or widget.get("readonly", True):
            raise ValueError(f"{key}: camera does not expose a writable setting")
        if widget.get("choices"):
            matching = [choice for choice in widget["choices"]
                        if normalized(key, choice) == normalized(key, requested)]
            if not matching:
                raise ValueError(f"{key}: unsupported value {requested!r}")
            planned[key] = matching[0]
        elif "range" in widget:
            number = float(requested)
            low, high, step = widget["range"]
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError(f"{key}: value must be between {low} and {high}")
            if step and abs((number - low) / step - round((number - low) / step)) > 1e-5:
                raise ValueError(f"{key}: value must use steps of {step}")
            planned[key] = number
        else:
            raise ValueError(f"{key}: free-form camera writes are not enabled")
    if enforce_manual and set(values) & {"iso", "shutterspeed", "f-number"}:
        mode = inventory.get("expprogram", {})
        current = planned.get("expprogram", mode.get("value"))
        if not manual_mode(current):
            if mode.get("readonly", True):
                raise ValueError("Set the camera's physical mode dial to M before synchronizing exposure")
            candidates = [v for v in mode.get("choices", []) if manual_mode(v)]
            if not candidates:
                raise ValueError("Camera does not offer manual exposure mode")
            planned = {"expprogram": candidates[0], **planned}
    return planned
