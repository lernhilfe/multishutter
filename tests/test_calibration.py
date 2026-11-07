from pathlib import Path
import tempfile
import unittest

from multishutter.calibration import decode_target, encode_counter, summarize_measurements


def target_image(counter, second_counter=None):
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (1000, 1000), "#080808")
    draw = ImageDraw.Draw(image)
    for x, y, color in ((.06, .06, "#ff00ff"), (.94, .06, "#00ffff"),
                         (.94, .94, "#ffff00"), (.06, .94, "#00ff00")):
        draw.rectangle(((x-.04)*1000, (y-.04)*1000, (x+.04)*1000, (y+.04)*1000), fill=color)
    bits = encode_counter(counter)
    if second_counter is not None:
        bits[32:] = encode_counter(second_counter)[32:]
    for row in range(8):
        for col in range(8):
            x, y = (.16+col*.085)*1000, (.16+row*.085)*1000
            draw.rectangle((x, y, x+85, y+85), fill="white" if bits[row*8+col] else "black")
    return image


class CalibrationTests(unittest.TestCase):
    def test_optical_decode_survives_perspective_and_jpeg_compression(self):
        import cv2
        import numpy as np
        from PIL import Image
        source = np.asarray(target_image(52343))
        matrix = cv2.getPerspectiveTransform(np.float32([[0,0],[999,0],[999,999],[0,999]]),
                                             np.float32([[100,30],[900,110],[960,920],[30,850]]))
        warped = cv2.warpPerspective(source, matrix, (1000,1000))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"capture.jpg"
            Image.fromarray(warped).save(path, quality=78)
            self.assertEqual(decode_target(path)["counter"], 52343)

    def test_frame_transition_is_rejected_instead_of_producing_offsets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"transition.png"
            target_image(100, 102).save(path)
            with self.assertRaisesRegex(ValueError, "Rolling Shutter"):
                decode_target(path)

    def test_blank_image_does_not_create_a_calibration(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"blank.png"
            Image.new("RGB", (1000,1000), "white").save(path)
            with self.assertRaises(ValueError):
                decode_target(path)

    def test_counter_wrap_and_robust_compensation_delay_faster_camera(self):
        rows = [{"fast": {"counter": value}, "slow": {"counter": (value+5)%65536}}
                for value in (65533, 100, 300, 500, 700)]
        result = summarize_measurements(rows, ["fast","slow"])
        self.assertEqual(result["cameras"]["fast"]["delay_ms"], 50)
        self.assertEqual(result["cameras"]["slow"]["delay_ms"], 0)
        self.assertEqual(result["cameras"]["fast"]["jitter_mad_ms"], 0)
        self.assertEqual(result["resolution_ms"], 16.67)

    def test_insufficient_or_mismatched_samples_are_rejected(self):
        with self.assertRaises(ValueError):
            summarize_measurements([], ["one"])
        with self.assertRaises(ValueError):
            summarize_measurements([{"one":{"counter":1}}]*3, ["one","two"])
