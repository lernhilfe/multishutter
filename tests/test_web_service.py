from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from multishutter.backends import SimulatedBackend
from multishutter.camera_settings import plan_settings, image_format, image_format_plan
from multishutter.service import RigService, ServiceError, validate_options


class LocalWorker:
    instances = {}
    fail_id = None

    def __init__(self, rig, spec):
        self.spec, self.timeout = spec, rig.command_timeout
        self.backend = SimulatedBackend(spec)
        self.closed = False
        self.instances[spec.id] = self

    def close(self):
        self.closed = True

    def call(self, operation, **fields):
        if operation == "connect":
            return {"ok": True, "info": self.backend.connect()}
        if operation == "prepare":
            return {"ok": True, "info": self.backend.prepare()}
        if operation == "health":
            return {"ok": True, "info": self.backend.health()}
        if operation == "settings":
            return {"ok": True, "settings": self.backend.settings()}
        if operation in {"set_settings", "restore_settings"}:
            result = self.backend.set_settings(fields["values"], enforce_manual=operation != "restore_settings")
            if operation == "set_settings" and self.spec.id == self.fail_id:
                raise RuntimeError("USB lost after a partial settings write")
            return {"ok": True, "result": result}
        raise AssertionError(operation)


class RigServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = RigService(Path(__file__).parents[1] / "config/rig.simulated.toml",
                                  root / "state", root / "photos", lock_path=root / "lock",
                                  worker_factory=LocalWorker)
        LocalWorker.fail_id = None
        self.service._scan(force=True)

    def tearDown(self):
        for worker in self.service.workers.values():
            worker.close()
        self.temp.cleanup()

    def test_reconnect_keeps_operator_lockout_until_all_cameras_acknowledged(self):
        service = self.service
        service.calibration["valid"] = True
        service.forced_offline.add("center")
        service._scan(force=True)
        self.assertTrue(service.lockout)
        self.assertFalse(service.calibration["valid"])
        self.assertFalse(service.snapshot()["capture_allowed"])
        with self.assertRaises(ServiceError):
            service._execute("acknowledge", {}, "test")
        service.forced_offline.clear()
        service._scan(force=True)
        self.assertEqual(len(service.workers), 3)
        self.assertTrue(service.lockout)
        service._execute("acknowledge", {}, "test")
        self.assertTrue(service.snapshot()["capture_allowed"])

    def test_warn_mode_requires_explicit_partial_capture_and_records_exclusions(self):
        service = self.service
        service.options["disconnect_policy"] = "warn"
        service.forced_offline.add("right")
        service._scan(force=True)
        with self.assertRaises(ServiceError):
            service._capture({"allow_partial": False}, "test")
        service.jobs["test"] = {}
        manifest = {"status": "complete", "session_id": "a" * 32, "takes": []}
        with patch("multishutter.service.run_capture", return_value=(Path("unused"), manifest)) as capture:
            service._capture({"allow_partial": True}, "test")
        self.assertEqual(capture.call_args.kwargs["manifest_extra"]["excluded_cameras"], ["right"])
        self.assertEqual([spec.id for spec in capture.call_args.args[0].cameras], ["left", "center"])

    def test_disconnect_lockout_survives_service_restart_until_acknowledged(self):
        service = self.service
        service._lose('center', 'test disconnect')
        restored = RigService(Path(__file__).parents[1] / 'config/rig.simulated.toml',
                              service.state_dir, service.output, worker_factory=LocalWorker)
        self.assertTrue(restored.lockout)
        restored._scan(force=True)
        self.assertFalse(restored.snapshot()['capture_allowed'])
        restored._execute('acknowledge', {}, 'test')
        import json
        self.assertFalse(json.loads((service.state_dir / 'settings.json').read_text())['lockout'])
        for worker in restored.workers.values(): worker.close()

    def test_physical_exposure_change_invalidates_compensation(self):
        service = self.service
        service.calibration['valid'] = True
        inventory = service.workers['left'].backend.settings()
        inventory['shutterspeed']['value'] = '1/250'
        service._update_inventory('left', inventory)
        self.assertFalse(service.calibration['valid'])

    def test_group_settings_validate_every_camera_before_any_write(self):
        self.service.workers["right"].backend.controls["iso"]["choices"] = ["100"]
        with self.assertRaises(ValueError):
            self.service._apply_settings({"values": {"iso": "400"}})
        for worker in self.service.workers.values():
            self.assertEqual(worker.backend.controls["iso"]["value"], "100")

    def test_partial_group_write_rolls_back_all_targets_and_reports_failure(self):
        LocalWorker.fail_id = "center"
        result = self.service._apply_settings({"values": {"iso": "400"}})
        self.assertFalse(result["ok"])
        self.assertTrue(all(row["ok"] for row in result["rollback"].values()))
        for worker in self.service.workers.values():
            self.assertEqual(worker.backend.controls["iso"]["value"], "100")

    def test_reconnected_camera_reapplies_last_successful_settings(self):
        result = self.service._apply_settings({"values": {"iso": "800", "shutterspeed": "1/250"}})
        self.assertTrue(result["ok"])
        self.service.forced_offline.add("left")
        self.service._scan(force=True)
        self.service.forced_offline.clear()
        self.service._scan(force=True)
        self.assertEqual(self.service.rows["left"]["settings"]["iso"]["value"], "800")

    def test_manual_delay_profile_is_invalidated_after_settings_change(self):
        self.service._execute("options", {"delays_ms": {"left": 40}, "compensation_enabled": True}, "test")
        self.assertTrue(self.service.calibration["valid"])
        self.service._apply_settings({"values": {"iso": "200"}})
        self.assertFalse(self.service.calibration["valid"])

    def test_busy_service_rejects_a_second_queued_operation(self):
        self.service.submit("reconnect")
        with self.assertRaises(ServiceError):
            self.service.submit("capture")

    def test_reconnect_reports_missing_camera_instead_of_false_success(self):
        service = self.service
        service.forced_offline.add('right')
        result = service._execute('reconnect', {}, 'test')
        self.assertFalse(result['ok'])
        self.assertEqual(result['ready'], 2)
        self.assertEqual(result['failed_camera_ids'], ['right'])
        self.assertIn('right:', result['error'])
        service.forced_offline.clear()
        result = service._execute('reconnect', {}, 'test')
        self.assertTrue(result['ok'])
        self.assertIsNone(result['error'])

    def test_reconnect_validates_all_ids_before_closing_any_camera(self):
        worker = self.service.workers['left']
        with self.assertRaises(ValueError):
            self.service._execute('reconnect', {'camera_ids': ['left', 'unknown']}, 'test')
        self.assertIs(self.service.workers['left'], worker)
        self.assertFalse(worker.closed)

    def test_invalid_settings_never_reach_workers(self):
        for options in ({"delays_ms": {"left": float("nan")}}, {"health_timeout_s": -1},
                        {"auto_reconnect": "yes"}, {"delays_ms": {"unknown": 20}}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                validate_options(options, self.service.rows)

    def test_physical_manual_mode_is_required_when_readonly(self):
        inventory = self.service.workers["left"].backend.settings()
        inventory["expprogram"].update(value="P", readonly=True)
        with self.assertRaisesRegex(ValueError, "physical mode dial"):
            plan_settings(inventory, {"iso": "200"})

    def test_rollback_can_restore_a_nonmanual_mode(self):
        backend = self.service.workers["left"].backend
        backend.controls["expprogram"]["value"] = "P"
        result = backend.set_settings({"iso": "400"})
        self.assertEqual(result["applied"]["expprogram"], "M")
        backend.set_settings(result["before"], enforce_manual=False)
        self.assertEqual(backend.controls["expprogram"]["value"], "P")

    def test_disabled_missing_camera_does_not_block_active_rig_or_reconnect(self):
        service = self.service
        old_worker = service.workers['right']
        service.forced_offline.add('right')
        service._scan(force=True)
        service._execute('rig_enable', {'camera_id': 'right', 'enabled': False}, 'test')
        service.forced_offline.clear()
        service._scan(force=True)
        service._execute('acknowledge', {}, 'test')
        self.assertTrue(old_worker.closed)
        self.assertNotIn('right', service.workers)
        self.assertEqual(service.rows['right']['state'], 'disabled')
        self.assertEqual(service.snapshot()['total'], 2)
        self.assertTrue(service.snapshot()['capture_allowed'])
        service.jobs['test'] = {}
        manifest = {'status': 'complete', 'session_id': 'a'*32, 'takes': []}
        with patch('multishutter.service.run_capture', return_value=(Path('unused'), manifest)) as capture:
            service._capture({}, 'test')
        self.assertEqual(capture.call_args.kwargs['manifest_extra']['disabled_cameras'], ['right'])
        self.assertEqual(capture.call_args.kwargs['manifest_extra']['excluded_cameras'], [])
        self.assertEqual(capture.call_args.kwargs['manifest_extra']['requested_camera_ids'], ['left', 'center'])
        with self.assertRaises(ValueError):
            service._apply_settings({'camera_ids': ['right'], 'values': {'iso': '200'}})

    def test_enabling_camera_reapplies_settings_and_requires_acknowledgement(self):
        service = self.service
        service._apply_settings({'camera_ids': ['right'], 'values': {'iso': '800'}})
        service._execute('rig_enable', {'camera_id': 'right', 'enabled': False}, 'test')
        service._execute('rig_enable', {'camera_id': 'right', 'enabled': True}, 'test')
        self.assertEqual(service.rows['right']['settings']['iso']['value'], '800')
        self.assertEqual(service.snapshot()['ready'], 3)
        self.assertTrue(service.lockout)

    def test_order_changes_capture_order_without_reopening_cameras(self):
        service = self.service
        workers = dict(service.workers)
        service.calibration = {'valid': True, 'proposal': {'old': True}}
        service.options['compensation_enabled'] = True
        order = ['right', 'left', 'center']
        service._execute('rig_order', {'camera_ids': order}, 'test')
        self.assertEqual([row['id'] for row in service.snapshot()['cameras']], order)
        self.assertTrue(all(service.workers[key] is worker for key, worker in workers.items()))
        self.assertFalse(service.options['compensation_enabled'])
        self.assertNotIn('proposal', service.calibration)
        service._execute('acknowledge', {}, 'test')
        service.jobs['test'] = {}
        with patch('multishutter.service.run_capture', return_value=(Path('unused'),
                   {'status': 'complete', 'session_id': 'a'*32, 'takes': []})) as capture:
            service._capture({}, 'test')
        self.assertEqual([spec.id for spec in capture.call_args.args[0].cameras], order)
        self.assertEqual([spec.position for spec in capture.call_args.args[0].cameras], [0, 1, 2])

    def test_invalid_order_is_rejected_without_mutating_or_closing_workers(self):
        service = self.service
        original = deepcopy(service.registry)
        for order in (['left'], ['left', 'left', 'right'], ['left', 'center', 'unknown']):
            with self.assertRaises(ValueError):
                service._execute('rig_order', {'camera_ids': order}, 'test')
        self.assertEqual(service.registry, original)
        self.assertFalse(any(worker.closed for worker in service.workers.values()))

    def test_registry_survives_restart_and_removed_camera_is_not_reimported(self):
        service = self.service
        service._execute('rig_remove', {'camera_id': 'center'}, 'test')
        service._execute('rig_order', {'camera_ids': ['right', 'left']}, 'test')
        service._execute('rig_enable', {'camera_id': 'left', 'enabled': False}, 'test')
        restored = RigService(Path(__file__).parents[1] / 'config/rig.simulated.toml',
                              service.state_dir, service.output, worker_factory=LocalWorker)
        self.assertEqual(list(restored.rows), ['right', 'left'])
        self.assertEqual([spec.id for spec in restored.rig.cameras], ['right'])
        self.assertTrue(restored.lockout)

    def test_remove_keeps_photos_and_discards_only_camera_dependent_settings(self):
        service = self.service
        photo = service.output / 'original.ARW'
        photo.write_bytes(b'original')
        service.desired = {'left': {'iso': '800'}, 'right': {'iso': '200'}}
        service.options['delays_ms'] = {'left': 20, 'right': 10}
        worker = service.workers['right']
        service._execute('rig_remove', {'camera_id': 'right'}, 'test')
        self.assertEqual(photo.read_bytes(), b'original')
        self.assertTrue(worker.closed)
        self.assertEqual(service.options['delays_ms'], {'left': 20})
        self.assertEqual(service.desired, {'left': {'iso': '800'}})

    def test_empty_rig_is_valid_but_cannot_capture_and_can_pair_again(self):
        service = self.service
        for camera_id in list(service.rows):
            service._execute('rig_remove', {'camera_id': camera_id}, 'test')
        self.assertEqual(service.snapshot()['total'], 0)
        self.assertFalse(service.snapshot()['capture_allowed'])
        with self.assertRaises(ServiceError):
            service._execute('acknowledge', {}, 'test')
        restored = RigService(Path(__file__).parents[1] / 'config/rig.simulated.toml',
                              service.state_dir, service.output, worker_factory=LocalWorker)
        self.assertEqual(restored.registry, [])
        service._execute('rig_pair', {'camera_id': 'new-left', 'model': 'SIMULATED',
                                    'port': 'simulated:left'}, 'test')
        self.assertEqual(service.rows['new-left']['state'], 'ready')
        self.assertTrue(service.registry[0]['serial'])
        self.assertTrue(service.lockout)

    def test_pair_rejects_duplicate_identity_even_when_existing_camera_disabled(self):
        service = self.service
        service._execute('rig_enable', {'camera_id': 'left', 'enabled': False}, 'test')
        with self.assertRaisesRegex(ValueError, 'bereits eingetragen'):
            service._execute('rig_pair', {'camera_id': 'duplicate', 'model': 'SIMULATED',
                                        'port': 'simulated:left'}, 'test')
        self.assertTrue(LocalWorker.instances['duplicate'].closed)
        self.assertNotIn('duplicate', service.rows)

    def test_pair_failure_does_not_change_roster_or_retain_worker(self):
        service = self.service
        service._execute('rig_remove', {'camera_id': 'right'}, 'test')
        original = deepcopy(service.registry)
        with patch.object(LocalWorker, 'call', return_value={'ok': False, 'error': 'USB init failed'}):
            with self.assertRaises(ServiceError):
                service._execute('rig_pair', {'camera_id': 'new', 'model': 'SIMULATED',
                                            'port': 'simulated:right'}, 'test')
        self.assertEqual(service.registry, original)
        self.assertTrue(LocalWorker.instances['new'].closed)

    def test_failed_registry_disk_write_keeps_current_rig_connections(self):
        service = self.service
        worker = service.workers['left']
        with patch('multishutter.service.write_manifest', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                service._execute('rig_remove', {'camera_id': 'left'}, 'test')
        self.assertIs(service.workers['left'], worker)
        self.assertFalse(worker.closed)
        self.assertIn('left', service.rows)

    def test_shared_jpeg_format_handles_different_sony_quality_names(self):
        service = self.service
        right = service.workers['right'].backend
        right.controls['imagequality'].update(value='RAW+JPEG', choices=['RAW','RAW+JPEG','JPEG'])
        right.controls['pcsaveimgformat'] = {'value':'RAW Only','choices':['RAW Only','JPEG Only','RAW & JPEG'],'readonly':False}
        service.workers['center'].backend.controls['imagequality']['value'] = 'RAW'
        result = service._execute('capture_format', {'format':'jpeg'}, 'test')
        self.assertTrue(result['ok'])
        self.assertEqual(right.controls['imagequality']['value'], 'JPEG')
        self.assertEqual(right.controls['pcsaveimgformat']['value'], 'JPEG Only')
        self.assertTrue(all(image_format(row['settings']['imagequality']['value'])=='jpeg' for row in service.rows.values()))
        self.assertTrue(service.snapshot()['format_consistent'])
        self.assertEqual(service.capture_format, 'jpeg')

    def test_individual_format_override_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Formatbereich'):
            self.service._apply_settings({'camera_ids':['left'],'values':{'imagequality':'RAW'}})

    def test_physical_raw_switch_blocks_capture_until_format_resynchronized(self):
        service = self.service
        inventory = service.workers['left'].backend.settings()
        inventory['imagequality']['value'] = 'RAW'
        service._update_inventory('left', inventory)
        self.assertFalse(service.snapshot()['capture_allowed'])
        with patch('multishutter.service.run_capture') as capture:
            with self.assertRaisesRegex(ServiceError, 'Aufnahmeformat abweichend'):
                service._capture({}, 'test')
            capture.assert_not_called()

    def test_reconnect_common_format_overrides_old_raw_desired_value(self):
        service = self.service
        service.desired['left'] = {'imagequality':'RAW'}
        service._lose('left', 'test')
        service._scan(force=True)
        self.assertEqual(service.rows['left']['settings']['imagequality']['value'], 'Fine')
        self.assertTrue(service._format_matches('left'))

    def test_group_format_failure_rolls_back_and_keeps_saved_target(self):
        service = self.service
        LocalWorker.fail_id = 'right'
        result = service._execute('capture_format', {'format':'raw'}, 'test')
        self.assertFalse(result['ok'])
        self.assertEqual(service.capture_format, 'jpeg')
        self.assertTrue(all(image_format(worker.backend.controls['imagequality']['value'])=='jpeg'
                            for worker in service.workers.values()))

    def test_rig_change_keeps_processing_settings_and_common_format(self):
        service = self.service
        service._execute('processing_options', {'frame_ms':240}, 'test')
        service._execute('capture_format', {'format':'raw'}, 'test')
        service._execute('rig_enable', {'camera_id':'right','enabled':False}, 'test')
        restored = RigService(Path(__file__).parents[1] / 'config/rig.simulated.toml',
                              service.state_dir, service.output, worker_factory=LocalWorker)
        self.assertEqual(restored.capture_format, 'raw')
        self.assertEqual(restored.processing['frame_ms'], 240)


class IdentityWorker(LocalWorker):
    serials = {'usb:001,001': 'SERIAL-ONE', 'usb:001,002': 'SERIAL-TWO', 'usb:001,003': ''}

    def call(self, operation, **fields):
        if operation == 'connect':
            serial = self.serials[self.spec.port]
            if self.spec.serial and self.spec.serial != serial:
                return {'ok': False, 'error': 'Serial mismatch'}
            return {'ok': True, 'info': {'serial': serial}}
        return super().call(operation, **fields)


class PairIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = root / 'rig.toml'
        config.write_text('backend="gphoto2"\n[[cameras]]\nid="one"\nposition=0\n'
                          'model="TEST CAMERA"\nport="usb:001,001"\nserial="SERIAL-ONE"\n')
        self.service = RigService(config, root / 'data', root / 'photos', worker_factory=IdentityWorker,
            discovery=lambda _: [{'model': 'TEST CAMERA', 'port': port} for port in IdentityWorker.serials])
        self.service._scan(force=True)

    def tearDown(self):
        for worker in self.service.workers.values(): worker.close()
        self.temp.cleanup()

    def test_same_model_new_camera_is_paired_with_read_serial_and_reconnect_verifies_it(self):
        service = self.service
        result = service._execute('rig_pair', {'camera_id': 'two', 'model': 'TEST CAMERA',
                                              'port': 'usb:001,002'}, 'test')
        self.assertEqual(result['serial'], 'SERIAL-TWO')
        self.assertEqual(service.workers['two'].spec.serial, 'SERIAL-TWO')
        self.assertEqual(service.registry[-1]['serial'], 'SERIAL-TWO')
        service._lose('two', 'test')
        service._scan(force=True)
        self.assertEqual(service.rows['two']['state'], 'ready')
        self.assertEqual(service.rows['one']['info']['serial'], 'SERIAL-ONE')

    def test_real_pair_without_serial_cannot_be_persisted(self):
        service = self.service
        before = deepcopy(service.registry)
        with self.assertRaisesRegex(ServiceError, 'keine Seriennummer'):
            service._execute('rig_pair', {'camera_id': 'unknown', 'model': 'TEST CAMERA',
                                         'port': 'usb:001,003'}, 'test')
        self.assertEqual(service.registry, before)
        self.assertTrue(IdentityWorker.instances['unknown'].closed)
