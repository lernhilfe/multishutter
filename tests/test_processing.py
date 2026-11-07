import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from PIL import Image

from multishutter.processing import RenderQueue, render_take, sequence_indices, validate_processing


class ProcessingTests(unittest.TestCase):
    def test_sequence_does_not_duplicate_turnaround_endpoints(self):
        self.assertEqual(sequence_indices(3,'pingpong'), [0,1,2,1])
        self.assertEqual(sequence_indices(3,'reverse'), [2,1,0])
        self.assertEqual(sequence_indices(1,'pingpong'), [0])
        with self.assertRaises(ValueError): sequence_indices(33,'forward')

    def test_processing_options_reject_unsafe_or_ambiguous_values(self):
        for value in ({'frame_ms':float('nan')},{'frame_ms':155},{'video_loops':True},
                      {'gif_width':5000},{'aspect':'evil'},{'video_enabled':False,'gif_enabled':False}):
            with self.subTest(value=value), self.assertRaises(ValueError): validate_processing(value)

    def test_real_mp4_and_gif_have_expected_size_timing_and_frame_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            sources=[]
            for index,color in enumerate(('red','green','blue')):
                source=root/f'{index}.jpg'
                Image.new('RGB',(160,120),color).save(source)
                sources.append(source)
            settings=validate_processing({'video_width':640,'gif_width':320,'frame_ms':100,'video_loops':2})
            result=render_take(sources,root/'renders/001',root,settings)
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format',
                        '-of','json',str(root/result['video']['path'])]))
            stream=probe['streams'][0]
            self.assertEqual((stream['codec_name'],stream['width'],stream['height']),('h264',640,360))
            self.assertAlmostEqual(float(probe['format']['duration']),.8,delta=.05)
            with Image.open(root/result['gif']['path']) as gif:
                self.assertEqual(gif.n_frames,4)
                self.assertEqual(gif.info['loop'],0)
                colors=[]
                for index in range(gif.n_frames):
                    gif.seek(index)
                    self.assertEqual(gif.info['duration'],100)
                    pixel=gif.convert('RGB').getpixel((160,90))
                    colors.append(max(range(3),key=lambda i:pixel[i]))
                self.assertEqual(colors,[0,1,2,1])
            for kind in ('video','gif'):
                path=root/result[kind]['path']
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),result[kind]['sha256'])
            self.assertFalse(list(root.rglob('*.part*')))

    def fixture(self, root):
        sid='a'*32
        folder=root/sid
        cameras=[{'id':'z-left','position':0},{'id':'a-right','position':1}]
        entries={}
        for camera,color in zip(cameras,('red','blue')):
            source=folder/'takes/001'/camera['id']/'photo.jpg'
            source.parent.mkdir(parents=True)
            Image.new('RGB',(160,120),color).save(source)
            entries[camera['id']]={'status':'complete','files':[{'status':'complete','path':str(source)}]}
        document={'status':'complete','rig':{'cameras':list(reversed(cameras))},'takes':[{'id':'001','cameras':entries}]}
        (folder/'manifest.json').write_text(json.dumps(document))
        return sid,folder,document

    def wait_render(self, folder):
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            data=json.loads((folder/'processing.json').read_text())
            if data['status'] in {'complete','failed'}: return data
            time.sleep(.03)
        self.fail('Renderer did not finish')

    def test_background_queue_uses_manifest_positions_and_preserves_original_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            sid,folder,document=self.fixture(root)
            before=(folder/'manifest.json').read_bytes()
            renderer=RenderQueue(root)
            renderer.start()
            try:
                renderer.submit(sid,validate_processing({'video_width':640,'gif_width':320,'video_loops':1}))
                state=self.wait_render(folder)
                self.assertEqual(state['status'],'complete',state.get('error'))
                self.assertEqual(state['outputs'][0]['camera_ids'],['z-left','a-right'])
                self.assertEqual((folder/'manifest.json').read_bytes(),before)
            finally: renderer.stop()

    def test_missing_original_reports_failure_and_retains_previous_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            sid,folder,_=self.fixture(root)
            previous=[{'take':'001','video':{'path':f'{sid}/renders/old/001.mp4'}}]
            (folder/'processing.json').write_text(json.dumps({'status':'complete','outputs':previous}))
            next(folder.rglob('photo.jpg')).unlink()
            renderer=RenderQueue(root)
            renderer.start()
            try:
                renderer.submit(sid,validate_processing({}))
                state=self.wait_render(folder)
                self.assertEqual(state['status'],'failed')
                self.assertIn('Originaldatei fehlt',state['error'])
                self.assertEqual(state['outputs'],previous)
            finally: renderer.stop()

    def test_restart_marks_interrupted_processing_without_touching_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            _,folder,_=self.fixture(root)
            (folder/'processing.json').write_text(json.dumps({'status':'running','outputs':[]}))
            renderer=RenderQueue(root)
            renderer.start()
            try:
                state=json.loads((folder/'processing.json').read_text())
                self.assertEqual(state['status'],'failed')
                self.assertEqual(len(list(folder.rglob('photo.jpg'))),2)
            finally: renderer.stop()
