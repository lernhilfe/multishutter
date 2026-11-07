"""CPU-only, bounded background rendering. Camera originals are never modified."""
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from .media import open_image, published_source
from .storage import utc_now, write_manifest

DEFAULT_PROCESSING = {'auto_enabled': True, 'video_enabled': True, 'gif_enabled': True,
                      'frame_ms': 150, 'direction': 'pingpong', 'video_loops': 6,
                      'video_width': 1280, 'gif_width': 640, 'aspect': '16:9'}


def validate_processing(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULT_PROCESSING):
        raise ValueError('Unbekannte Verarbeitungseinstellung')
    result = {**DEFAULT_PROCESSING, **values}
    for key in ('auto_enabled', 'video_enabled', 'gif_enabled'):
        if type(result[key]) is not bool:
            raise ValueError(f'{key}: Wahrheitswert erforderlich')
    if not result['video_enabled'] and not result['gif_enabled']:
        raise ValueError('Mindestens Video oder GIF aktivieren; automatische Verarbeitung separat abschalten')
    for key, low, high in (('frame_ms', 20, 2000), ('video_loops', 1, 20)):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f'{key}: ganze Zahl zwischen {low} und {high} erforderlich')
    if result['frame_ms'] % 10:
        raise ValueError('Die Bilddauer muss in 10-ms-Schritten angegeben werden')
    if result['direction'] not in {'forward', 'reverse', 'pingpong'}:
        raise ValueError('Ungültige Bewegungsrichtung')
    if result['aspect'] not in {'16:9', '4:3', '1:1'}:
        raise ValueError('Ungültiges Seitenverhältnis')
    if type(result['video_width']) is not int or result['video_width'] not in {640, 960, 1280, 1920}:
        raise ValueError('Videobreite: 640, 960, 1280 oder 1920')
    if type(result['gif_width']) is not int or result['gif_width'] not in {320, 480, 640, 800}:
        raise ValueError('GIF-Breite: 320, 480, 640 oder 800')
    return result


def sequence_indices(count, direction):
    if not 1 <= count <= 32:
        raise ValueError('Eine Aufnahmerunde benötigt 1 bis 32 Kamerabilder')
    forward = list(range(count))
    if direction == 'reverse':
        return forward[::-1]
    return forward + forward[-2:0:-1] if direction == 'pingpong' else forward


def dimensions(width, aspect):
    x, y = map(int, aspect.split(':'))
    return width, round(width * y / x / 2) * 2


def _fit(image, size):
    from PIL import Image, ImageOps
    scaled = ImageOps.contain(image, size, Image.Resampling.LANCZOS)
    canvas = Image.new('RGB', size, 'black')
    canvas.paste(scaled, ((size[0]-scaled.width)//2, (size[1]-scaled.height)//2))
    return canvas


def _ffmpeg(arguments, cancel):
    process = subprocess.Popen(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                                '-filter_threads', '1', *arguments], stdout=subprocess.DEVNULL,
                               stderr=subprocess.PIPE)
    deadline = time.monotonic() + 180
    try:
        while True:
            if cancel() or time.monotonic() > deadline:
                raise RuntimeError('Videoverarbeitung gestoppt oder Zeitlimit überschritten')
            try:
                _, stderr = process.communicate(timeout=.25)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise RuntimeError('Video konnte nicht erstellt werden: ' + stderr.decode(errors='replace')[-1600:])
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def _record(path, root):
    with path.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': path.relative_to(root).as_posix(), 'size': path.stat().st_size, 'sha256': digest}


def render_take(sources, destination, root, settings, cancel=lambda: False):
    from PIL import Image
    indices = sequence_indices(len(sources), settings['direction'])
    frame_seconds = settings['frame_ms'] / 1000
    duration = len(indices) * frame_seconds * settings['video_loops']
    if settings['video_enabled'] and duration > 120:
        raise ValueError('Video wäre länger als 120 s. Bilddauer oder Wiederholungen reduzieren.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = {'frame_count': len(sources), 'sequence': indices, 'frame_ms': settings['frame_ms']}
    with tempfile.TemporaryDirectory(prefix='multishutter-render-') as directory:
        work = Path(directory)
        gif_frames = []
        for index, source in enumerate(sources):
            if cancel():
                raise RuntimeError('Verarbeitung gestoppt')
            image = open_image(source)
            if settings['video_enabled']:
                _fit(image, dimensions(settings['video_width'], settings['aspect'])).save(work / f'frame-{index:03}.jpg', quality=92)
            if settings['gif_enabled']:
                # Quantize immediately: at most 32 indexed images remain in RAM.
                gif_frames.append(_fit(image, dimensions(settings['gif_width'], settings['aspect'])).quantize(colors=128))
            image.close()
        if settings['video_enabled']:
            timeline = []
            repeated = indices * settings['video_loops']
            for index in repeated:
                timeline += [f"file 'frame-{index:03}.jpg'", f'duration {frame_seconds:.3f}']
            timeline.append(f"file 'frame-{repeated[-1]:03}.jpg'")
            playlist = work / 'frames.txt'
            playlist.write_text('\n'.join(timeline) + '\n')
            output = destination.with_suffix('.mp4')
            temporary = output.with_name(output.stem + '.part.mp4')
            try:
                _ffmpeg(['-f', 'concat', '-safe', '1', '-i', str(playlist), '-vf', 'fps=30',
                         '-t', f'{duration:.3f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
                         '-threads', '2', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', str(temporary)], cancel)
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
            result['video'] = {**_record(output, root), 'duration_s': round(duration, 3)}
        if settings['gif_enabled']:
            output = destination.with_suffix('.gif')
            temporary = output.with_suffix('.gif.part')
            frames = [gif_frames[index] for index in indices]
            try:
                frames[0].save(temporary, format='GIF', save_all=True, append_images=frames[1:],
                               duration=settings['frame_ms'], loop=0, disposal=2, optimize=False)
                temporary.replace(output)
            finally:
                temporary.unlink(missing_ok=True)
            result['gif'] = {**_record(output, root), 'cycle_s': round(len(indices)*frame_seconds, 3)}
    return result


class RenderQueue:
    def __init__(self, root, event=lambda *args: None):
        self.root, self.event = Path(root), event
        self.queue = queue.Queue(maxsize=50)
        self.pending, self.mutex = set(), threading.RLock()
        self.stopping = threading.Event()
        self.thread = None

    def start(self):
        for path in self.root.glob('*/processing.json'):
            try:
                data = json.loads(path.read_text())
                if data.get('status') in {'queued', 'running'}:
                    data.update(status='failed', error='Verarbeitung beim Neustart unterbrochen. Erneut verarbeiten.')
                    write_manifest(path, data)
            except (OSError, ValueError):
                continue
        self.thread = threading.Thread(target=self._run, name='media-renderer', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(8)

    def submit(self, session_id, settings):
        if not re.fullmatch('[a-f0-9]{32}', session_id):
            raise ValueError('Ungültige Sitzung')
        settings = validate_processing(settings)
        manifest = self.root / session_id / 'manifest.json'
        document = json.loads(manifest.read_text())
        if document['status'] != 'complete' or not document['takes'] or document.get('calibration_capture'):
            raise ValueError('Nur vollständige Foto-Sitzungen können verarbeitet werden')
        with self.mutex:
            if session_id in self.pending:
                raise ValueError('Diese Sitzung wird bereits verarbeitet')
            if self.queue.full() or self.stopping.is_set():
                raise ValueError('Verarbeitungswarteschlange ist voll oder wird beendet')
            state_path = manifest.with_name('processing.json')
            previous = json.loads(state_path.read_text()) if state_path.exists() else {}
            state = {'status': 'queued', 'session_id': session_id, 'settings': deepcopy(settings),
                     'created_at': utc_now(), 'progress': 'Wartet auf Verarbeitung',
                     'outputs': previous.get('outputs', []), 'error': None}
            write_manifest(state_path, state)
            self.pending.add(session_id)
            self.queue.put((manifest, state))
        return {'session_id': session_id, 'status': 'queued'}

    def _run(self):
        while not self.stopping.is_set():
            try:
                manifest_path, state = self.queue.get(timeout=.2)
            except queue.Empty:
                continue
            state_path = manifest_path.with_name('processing.json')
            session_id = state['session_id']
            try:
                document = json.loads(manifest_path.read_text())
                cameras = sorted(document['rig']['cameras'], key=lambda row: row['position'])
                generation = uuid4().hex
                outputs = []
                state.update(status='running', started_at=utc_now())
                for number, take in enumerate(document['takes'], 1):
                    if not re.fullmatch(r'\d{3,6}', take['id']):
                        raise ValueError('Ungültige Aufnahmerunde')
                    state['progress'] = f"Runde {number}/{len(document['takes'])} wird verarbeitet"
                    write_manifest(state_path, state)
                    sources = []
                    for camera in cameras:
                        entry = take['cameras'].get(camera['id'], {})
                        if entry.get('status') != 'complete':
                            raise ValueError('Eine Kameraperspektive ist unvollständig')
                        records = sorted(entry.get('files', []), key=lambda row: Path(row['path']).suffix.lower() not in {'.jpg','.jpeg','.png'})
                        candidates = [published_source(self.root, manifest_path, take['id'], camera['id'], row)
                                      for row in records if row.get('status') == 'complete']
                        source = next((path for path in candidates if path is not None), None)
                        if source is None:
                            raise ValueError(f"Originaldatei fehlt: {camera['id']} / {take['id']}")
                        sources.append(source)
                    destination = manifest_path.parent / 'renders' / generation / take['id']
                    result = render_take(sources, destination, self.root, state['settings'], self.stopping.is_set)
                    outputs.append({'take': take['id'], 'camera_ids': [row['id'] for row in cameras], **result})
                state.update(status='complete', outputs=outputs, progress='Video/GIF fertig', finished_at=utc_now())
                self.event(f'{session_id[:8]}: Video/GIF erstellt')
            except Exception as error:
                state.update(status='failed', error=str(error), finished_at=utc_now(), progress='Verarbeitung fehlgeschlagen')
                self.event(f'{session_id[:8]}: Verarbeitung fehlgeschlagen: {error}', 'warning')
            finally:
                try:
                    write_manifest(state_path, state)
                except OSError as error:
                    self.event(f'Verarbeitungsstatus konnte nicht gespeichert werden: {error}', 'error')
                finally:
                    with self.mutex:
                        self.pending.discard(session_id)
                    self.queue.task_done()
