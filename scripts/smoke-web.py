"""End-to-end API checks against a running simulated Compose instance only."""
import argparse
import json
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    args = parser.parse_args()
    base = args.url.rstrip("/")

    def request(path, body=None, method="POST", headers=None, expected=200):
        data = json.dumps(body).encode() if body is not None else None
        head = {"Content-Type": "application/json", "X-MultiShutter": "1"} if data is not None else {}
        if headers is not None:
            head = headers
        req = urllib.request.Request(base + path, data, headers=head, method=method if data is not None else "GET")
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                status, content = response.status, response.read()
        except urllib.error.HTTPError as error:
            status, content = error.code, error.read()
        if status != expected:
            raise AssertionError(f"{path}: expected {expected}, received {status}: {content[:400]}")
        try:
            return json.loads(content)
        except ValueError:
            return content

    def job(path, body, method="POST", success=True):
        operation = request(path, body, method, expected=202)
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            state = request("/api/state")
            result = next(item for item in state["jobs"] if item["id"] == operation["id"])
            if result["status"] in {"complete", "failed"}:
                assert result["status"] == ("complete" if success else "failed"), result
                return result
            time.sleep(.15)
        raise AssertionError("Job timeout")

    state = request("/api/state")
    assert state["backend"] == "simulated", "This script must never run against real cameras"
    assert state["ready"] == state["total"] == 3, state
    original_options = state["options"]
    original_format = state['capture_format']
    original_processing = state['processing']
    original_values = {row["id"]: {key: row["settings"][key]["value"] for key in ("iso", "shutterspeed")}
                       for row in state["cameras"]}
    checks = []
    request("/api/capture", {"takes": 1}, headers={"Content-Type": "application/json"}, expected=403)
    request("/api/capture", {"takes": 1}, headers={"Content-Type": "application/json", "X-MultiShutter": "1", "Origin": "https://foreign.invalid"}, expected=403)
    request("/api/files/..%2F..%2Fetc%2Fpasswd", expected=404)
    request("/api/options", {"health_timeout_s": -1}, method="PUT", expected=400)
    checks.append("CSRF, cross-origin, file confinement and invalid options rejected")
    job("/api/camera-settings", {"values": {"iso": "99999"}}, success=False)
    assert all(row["settings"]["iso"]["value"] == original_values[row["id"]]["iso"]
               for row in request("/api/state")["cameras"])
    checks.append("Unsupported group setting leaves every camera unchanged")
    operation = request("/api/capture", {"takes": 1, "countdown": 4, "interval": 0}, expected=202)
    request("/api/capture", {"takes": 1}, expected=409)
    request("/api/stop", {})
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and request("/api/state")["busy"]:
        time.sleep(.15)
    stopped = next(item for item in request("/api/state")["jobs"] if item["id"] == operation["id"])
    assert stopped["status"] == "failed", stopped
    checks.append("Concurrent capture rejected; queued/countdown stop honored")
    job("/api/simulate/disconnect", {"camera_id": "center", "offline": True})
    assert request("/api/state")["lockout"]
    job("/api/capture", {"takes": 1, "countdown": 0}, success=False)
    job("/api/options", {"disconnect_policy": "warn"}, method="PUT")
    job("/api/capture", {"takes": 1, "countdown": 0}, success=False)
    partial = job("/api/capture", {"takes": 1, "countdown": 0, "interval": 0, "allow_partial": True})
    session = request("/api/sessions/" + partial["result"]["session_id"])
    assert session["excluded_cameras"] == ["center"] and len(session["files"]) == 2, session
    job("/api/simulate/disconnect", {"camera_id": "center", "offline": False})
    assert request("/api/state")["lockout"]
    job("/api/acknowledge", {})
    checks.append("Disconnect locks capture; warn mode requires explicit partial acknowledgement; reconnect stays latched")
    job("/api/camera-settings", {"values": {"iso": "800", "shutterspeed": "1/250"}})
    assert all(row["settings"]["iso"]["value"] == "800" for row in request("/api/state")["cameras"])
    job("/api/options", {"disconnect_policy": "lockout", "compensation_enabled": True,
                         "delays_ms": {"left": 100, "center": 0, "right": 50}}, method="PUT")
    captured = job("/api/capture", {"takes": 2, "countdown": 0, "interval": 0})
    session_id = captured["result"]["session_id"]
    manifest = request(f"/api/sessions/{session_id}/manifest")
    assert manifest["delays_ms"]["left"] == 100
    for take in manifest["takes"]:
        rows = take["cameras"]
        difference = (rows["left"]["trigger_started_ns"] - rows["center"]["trigger_started_ns"]) / 1e6
        assert 60 < difference < 180, difference
    session = request(f"/api/sessions/{session_id}")
    assert len(session["files"]) == 6
    for file in session["files"]:
        assert request(file["download"])
        assert request(file["preview"])
    checks.append("Shared settings, two rounds with measured scheduled delay, originals and previews verified")
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        rendered=request(f'/api/sessions/{session_id}')['processing']
        if rendered['status'] in {'complete','failed'}: break
        time.sleep(.15)
    assert rendered['status']=='complete',rendered
    assert len(rendered['outputs'])==2
    for take in rendered['outputs']:
        video=request(take['video']['url'])
        gif=request(take['gif']['url'])
        assert video[4:8]==b'ftyp' and gif[:3]==b'GIF'
        ranged=request(take['video']['url'],headers={'Range':'bytes=0-63'},expected=206)
        assert len(ranged)==64
    request('/api/renders/..%2F..%2Fetc%2Fpasswd',expected=404)
    request('/api/processing/options',{'frame_ms':155},method='PUT',expected=400)
    job('/api/camera-settings',{'camera_ids':['left'],'values':{'imagequality':'RAW'}},success=False)
    job('/api/camera-format',{'format':'raw'})
    assert request('/api/state')['format_consistent']
    job('/api/camera-format',{'format':'jpeg'})
    assert all(row['settings']['imagequality']['value']=='Fine' for row in request('/api/state')['cameras'])
    job('/api/processing/options',{'frame_ms':200,'video_loops':2},method='PUT')
    job('/api/processing/render',{'session_id':session_id})
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        updated=request(f'/api/sessions/{session_id}')['processing']
        if updated['status'] in {'complete','failed'}: break
        time.sleep(.15)
    assert updated['status']=='complete' and updated['outputs'][0]['frame_ms']==200,updated
    assert updated['outputs'][0]['video']['url']!=rendered['outputs'][0]['video']['url']
    assert request(f'/api/sessions/{session_id}/manifest')==manifest
    checks.append('Automatic MP4/GIF for every take, seeking/range requests, common format and re-rendering with changed timing verified')
    job("/api/calibration/capture", {"takes": 5, "countdown": 0}, success=False)
    checks.append("Simulation cannot falsely claim an optical exposure measurement")
    original_order = [row["id"] for row in state["cameras"]]
    reverse_order = list(reversed(original_order))
    job('/api/rig/order', {'camera_ids': reverse_order}, method='PUT')
    changed = request('/api/state')
    assert [row['id'] for row in changed['cameras']] == reverse_order
    assert changed['lockout'] and not changed['options']['compensation_enabled']
    job('/api/acknowledge', {})
    ordered = job('/api/capture', {'takes': 1, 'countdown': 0, 'interval': 0})
    ordered_id = ordered['result']['session_id']
    assert [row['id'] for row in request(f'/api/sessions/{ordered_id}/manifest')['rig']['cameras']] == reverse_order
    assert [row['camera_id'] for row in request(f'/api/sessions/{ordered_id}')['files']] == reverse_order
    job('/api/rig/enabled', {'camera_id': 'center', 'enabled': False}, method='PUT')
    changed = request('/api/state')
    assert changed['total'] == 2 and changed['ready'] == 2
    assert next(row for row in changed['registered_cameras'] if row['id'] == 'center')['state'] == 'disabled'
    job('/api/acknowledge', {})
    disabled = job('/api/capture', {'takes': 1, 'countdown': 0, 'interval': 0})
    disabled_manifest = request('/api/sessions/' + disabled['result']['session_id'] + '/manifest')
    assert disabled_manifest['disabled_cameras'] == ['center'] and disabled_manifest['excluded_cameras'] == []
    assert list(disabled_manifest['takes'][0]['cameras']) == ['right', 'left']
    job('/api/camera-settings', {'values': {'iso': '400'}})
    assert next(row for row in request('/api/state')['registered_cameras'] if row['id'] == 'center')['settings']['iso']['value'] == '800'
    job('/api/rig/enabled', {'camera_id': 'center', 'enabled': True}, method='PUT')
    assert request('/api/state')['ready'] == 3
    job('/api/rig/remove', {'camera_id': 'right'})
    assert request(session['files'][0]['download'])  # Previous originals are retained.
    found = job('/api/rig/discover', {})['result']['cameras']
    right = next(row for row in found if row['port'] == 'simulated:right')
    assert right['registered_id'] is None
    job('/api/rig/pair', {'camera_id': 'duplicate', 'model': 'SIMULATED', 'port': 'simulated:left'}, success=False)
    job('/api/rig/pair', {'camera_id': 'right', 'model': right['model'], 'port': right['port']})
    assert request('/api/state')['ready'] == 3
    job('/api/rig/order', {'camera_ids': original_order}, method='PUT')
    job('/api/acknowledge', {})
    assert request(f'/api/sessions/{session_id}/manifest') == manifest
    request('/api/rig/enabled', {'camera_id': 'left', 'enabled': 'false'}, method='PUT', expected=422)
    checks.append('Rig order matches manifests and gallery; disabled cameras are excluded; remove retains photos; discovery and serial pairing restore camera')
    for camera_id, values in original_values.items():
        job("/api/camera-settings", {"camera_ids": [camera_id], "values": values})
    job("/api/options", original_options, method="PUT")
    job('/api/camera-format',{'format':original_format})
    job('/api/processing/options',original_processing,method='PUT')
    print(json.dumps({"ok": True, "checks": checks, "session_id": session_id}, indent=2))


if __name__ == "__main__":
    main()
