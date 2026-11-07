"""Optical exposure comparison using a duplicated, CRC-protected Gray-code target.

Only the same display clock is compared across cameras. USB return times and file
arrival times are deliberately excluded from exposure compensation.
"""
import math
import statistics

MAGIC = 0xA5
TICK_MS = 10


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ (0x07 if crc & 0x80 else 0)) & 255
    return crc


def encode_counter(counter):
    counter &= 65535
    gray = counter ^ (counter >> 1)
    payload = bytes([MAGIC, gray >> 8, gray & 255])
    word = int.from_bytes(payload + bytes([crc8(payload)]), "big")
    return [(word >> (31 - index)) & 1 for index in range(32)] * 2


def decode_target(path):
    import cv2
    import numpy as np
    from .media import open_image
    image = open_image(path)
    image.thumbnail((1800, 1800))
    rgb = np.asarray(image)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    # Magenta, cyan, yellow, green locate the four oriented corner markers.
    corners = []
    for low, high in ((140, 178), (80, 105), (18, 40), (43, 79)):
        mask = cv2.inRange(hsv, np.array([low, 90, 65]), np.array([high, 255, 255]))
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
        candidates = [(stats[i, cv2.CC_STAT_AREA], i) for i in range(1, count)
                      if stats[i, cv2.CC_STAT_AREA] >= 16]
        if not candidates:
            raise ValueError("Testmuster nicht gefunden: alle vier farbigen Eckmarken müssen sichtbar sein")
        _, index = max(candidates)
        corners.append(centroids[index])
    source = np.array(corners, dtype=np.float32)
    if abs(cv2.contourArea(source)) < rgb.shape[0] * rgb.shape[1] * 0.025:
        raise ValueError("Testmuster zu klein: Bildschirm stärker bildfüllend aufnehmen")
    destination = np.array([[.06, .06], [.94, .06], [.94, .94], [.06, .94]], np.float32) * 512
    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(rgb, transform, (512, 512))
    luma = cv2.cvtColor(warped, cv2.COLOR_RGB2GRAY)
    values = []
    for row in range(8):
        for col in range(8):
            x, y = int((.16 + (col + .5) * .085) * 512), int((.16 + (row + .5) * .085) * 512)
            values.append(float(np.median(luma[y - 4:y + 5, x - 4:x + 5])))
    low, high = float(np.percentile(values, 10)), float(np.percentile(values, 90))
    if high - low < 55:
        raise ValueError("Testmuster hat zu wenig Kontrast")
    middle = (low + high) / 2
    confidence = min(abs(value - middle) / ((high - low) / 2) for value in values)
    bits = [int(value > middle) for value in values]
    if bits[:32] != bits[32:] or confidence < .35:
        raise ValueError("Bildschirmwechsel oder Rolling Shutter erkannt: kürzere Belichtung wählen und neu messen")
    word = 0
    for bit in bits[:32]:
        word = (word << 1) | bit
    payload = word.to_bytes(4, "big")
    if payload[0] != MAGIC or crc8(payload[:3]) != payload[3]:
        raise ValueError("Prüfsumme des Testmusters ungültig; Ausrichtung und Belichtung prüfen")
    gray = int.from_bytes(payload[1:3], "big")
    counter = gray
    while gray:
        gray >>= 1
        counter ^= gray
    return {"counter": counter, "confidence": round(min(confidence, 1), 3)}


def summarize_measurements(rows, camera_ids, refresh_ms=16.67):
    if len(rows) < 3:
        raise ValueError("Mindestens drei vollständig auswertbare Aufnahmerunden erforderlich")
    if not math.isfinite(refresh_ms) or not 5 <= refresh_ms <= 100:
        raise ValueError("Keine gültige Bildschirmfrequenz gemessen")
    values = {camera_id: [] for camera_id in camera_ids}
    for row in rows:
        if set(row) != set(camera_ids):
            raise ValueError("Unvollständige Kamerazuordnung im Messlauf")
        reference = row[camera_ids[0]]["counter"]
        deltas = {camera_id: ((row[camera_id]["counter"] - reference + 32768) % 65536 - 32768) * TICK_MS
                  for camera_id in camera_ids}
        if max(deltas.values()) - min(deltas.values()) > 1000:
            raise ValueError("Aufnahmezeiten liegen mehr als eine Sekunde auseinander; Messmuster prüfen")
        for camera_id, delta in deltas.items():
            values[camera_id].append(delta)
    medians = {key: statistics.median(samples) for key, samples in values.items()}
    earliest, latest = min(medians.values()), max(medians.values())
    cameras = {}
    for camera_id in camera_ids:
        center = medians[camera_id]
        jitter = statistics.median(abs(value - center) for value in values[camera_id])
        cameras[camera_id] = {"relative_exposure_ms": center - earliest,
                              "delay_ms": latest - center, "jitter_mad_ms": jitter,
                              "samples": len(values[camera_id])}
    return {"method": "optical-gray-code", "cameras": cameras, "valid_samples": len(rows),
            "resolution_ms": round(max(TICK_MS, refresh_ms), 2),
            "spread_ms": latest - earliest,
            "note": "Relative Belichtungszeiten am Bildschirm; Genauigkeit durch Bildwiederholrate und Rolling Shutter begrenzt."}
