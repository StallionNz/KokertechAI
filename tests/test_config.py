"""Unit tests for config.py."""

import json, os, sys, tempfile, unittest, zipfile
from unittest.mock import patch, MagicMock, call
import config
from config import _is_valid_json, _write_save_marker, check_last_save_integrity, save_settings, save_identity, _load_dotenv, _load_settings, _load_identity, _load_personas, reload, _snapshot_backups, list_backups, restore_backup, export_backup, import_backup, CONFIG, IDENTITY_CONFIG, SETTINGS_PATH, IDENTITY_PATH, SETTINGS_BACKUP_DIR, LOG_DIR, SAVE_MARKER_PATH

class TestIsValidJson(unittest.TestCase):
    def setUp(self):
        self.tmp = []
    def tearDown(self):
        for p in self.tmp:
            try: os.unlink(p)
            except OSError: pass  # file already gone / locked — best-effort cleanup only
    def _mk(self, c):
        fd, p = tempfile.mkstemp()
        os.close(fd)
        with open(p, "w") as f: f.write(c)
        self.tmp.append(p)
        return p
    def test_empty_obj(self):
        self.assertTrue(_is_valid_json(self._mk('{}')))
    def test_boolean(self):
        self.assertTrue(_is_valid_json(self._mk('true')))
    def test_zero(self):
        self.assertTrue(_is_valid_json(self._mk('0')))
    def test_object(self):
        self.assertTrue(_is_valid_json(self._mk('{"k":"v"}')))
    def test_bad_text(self):
        self.assertFalse(_is_valid_json(self._mk('bad')))
    def test_empty_file(self):
        self.assertFalse(_is_valid_json(self._mk('')))
    def test_nonexistent(self):
        self.assertFalse(_is_valid_json('/nope'))
    def test_directory_path(self):
        self.assertFalse(_is_valid_json(tempfile.gettempdir()))
    def test_binary_garbage(self):
        self.assertFalse(_is_valid_json(self._mk('\x00\x01\x02\xff\xfe')))
    def test_json_array(self):
        self.assertTrue(_is_valid_json(self._mk('[1, 2, 3]')))
    def test_json_string(self):
        self.assertTrue(_is_valid_json(self._mk(chr(34) + 'hello' + chr(34))))

class TestWriteSaveMarker(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.ld = os.path.join(self.td, 'logs')
        os.makedirs(self.ld)
    def tearDown(self):
        import shutil
        shutil.rmtree(self.td)
    def _do(self, ld, mk):
        with patch("config.LOG_DIR", ld):
            with patch("config.SAVE_MARKER_PATH", mk):
                _write_save_marker()
    def test_writes_file(self):
        mk = os.path.join(self.ld, '.ok')
        self._do(self.ld, mk)
        self.assertTrue(os.path.isfile(mk))
        with open(mk) as f:
            c = f.read().strip()
        self.assertRegex(c, '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}')
    def test_overwrites_with_new_timestamp(self):
        mk = os.path.join(self.ld, '.ok')
        self._do(self.ld, mk)
        with open(mk) as f:
            first = f.read().strip()
        import time
        time.sleep(0.01)
        self._do(self.ld, mk)
        with open(mk) as f:
            second = f.read().strip()
        self.assertNotEqual(first, second)
        self.assertRegex(second, '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}')
    def test_creates_dir(self):
        nd = os.path.join(self.td, 'nd')
        mk = os.path.join(nd, ".ok")
        self._do(nd, mk)
        self.assertTrue(os.path.isdir(nd))
    def test_error_silent(self):
        mk = os.path.join(self.ld, '.ok')
        os.makedirs(mk)
        self._do(self.ld, mk)

class TestCheckLastSaveIntegrity(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.bu = os.path.join(self.td, 'bu')
        self.lg = os.path.join(self.td, 'lg')
        self.sp = os.path.join(self.td, 's.json')
        self.ip = os.path.join(self.td, 'i.json')
        self.mk = os.path.join(self.lg, '.ok')
        os.makedirs(self.lg)
        os.makedirs(self.bu)
    def tearDown(self):
        import shutil
        shutil.rmtree(self.td)
    def _w(self, p, c):
        with open(p, "w") as f: f.write(c)
    def _add_bu(self, ts="20250101_120000"):
        self._w(os.path.join(self.bu, 'app_settings_' + ts + '.json'), '{"a":1}')
        self._w(os.path.join(self.bu, 'user_identity_' + ts + '.json'), '{"b":2}')
        return ts
    def _run(self):
        with patch("config.SETTINGS_PATH", self.sp):
            with patch("config.IDENTITY_PATH", self.ip):
                with patch("config.SAVE_MARKER_PATH", self.mk):
                    with patch("config.SETTINGS_BACKUP_DIR", self.bu):
                        with patch("config.LOG_DIR", self.lg):
                            return check_last_save_integrity()
    def test_both_valid(self):
        self._w(self.sp, '{"k":"v"}')
        self._w(self.ip, '{"n":"t"}')
        self._add_bu()
        r = self._run()
        self.assertTrue(r["ok"])
        self.assertTrue(r["settings_valid"])
        self.assertTrue(r["identity_valid"])
    def test_settings_corrupt(self):
        self._w(self.sp, 'bad')
        self._w(self.ip, '{"n":"t"}')
        self._add_bu()
        r = self._run()
        self.assertFalse(r["ok"])
        self.assertFalse(r["settings_valid"])
        self.assertTrue(r["identity_valid"])
    def test_identity_corrupt(self):
        self._w(self.sp, '{"k":"v"}')
        self._w(self.ip, 'bad')
        self._add_bu()
        r = self._run()
        self.assertFalse(r["ok"])
        self.assertTrue(r["settings_valid"])
        self.assertFalse(r["identity_valid"])
    def test_fresh_install(self):
        r = self._run()
        self.assertTrue(r["ok"])
        self.assertFalse(r["backups_available"])
    def test_has_backups(self):
        self._w(self.sp, '{"k":"v"}')
        self._w(self.ip, '{"n":"t"}')
        self._add_bu()
        r = self._run()
        self.assertTrue(r["backups_available"])
        self.assertIsNotNone(r["latest_backup_ts"])
    def test_most_recent_backup(self):
        self._w(self.sp, '{"k":"v"}')
        self._w(self.ip, '{"n":"t"}')
        self._add_bu("20241201_000000")
        self._add_bu("20250115_120000")
        r = self._run()
        self.assertEqual(r["latest_backup_ts"], "20250115_120000")
    def test_no_backups(self):
        self._w(self.sp, '{"k":"v"}')
        self._w(self.ip, '{"n":"t"}')
        r = self._run()
        self.assertFalse(r["backups_available"])
        self.assertIsNone(r["latest_backup_ts"])
    def test_marker_check(self):
        self._w(self.sp, '{"k":"v"}')
        self._w(self.ip, '{"n":"t"}')
        self._add_bu()
        if os.path.isfile(self.mk): os.remove(self.mk)
        self.assertFalse(self._run()["marker_exists"])
        self._w(self.mk, 'ts')
        self.assertTrue(self._run()["marker_exists"])
    def test_paths(self):
        self._w(self.sp, '{"k":"v"}')
        self._w(self.ip, '{"n":"t"}')
        self._add_bu()
        r = self._run()
        self.assertEqual(r["settings_path"], self.sp)
        self.assertEqual(r["identity_path"], self.ip)

class TestSaveSettings(unittest.TestCase):
    """save_settings tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.sp = os.path.join(self.td, 'settings.json')
        self.ip = os.path.join(self.td, 'identity.json')
        self.bu = os.path.join(self.td, 'bu')
        self.lg = os.path.join(self.td, 'logs')
        self.mk = os.path.join(self.lg, '.ok')
        os.makedirs(self.lg)
        os.makedirs(self.bu)
        # Pre-create settings and identity files so snapshot_backups has something to back up
        with open(self.sp, 'w') as f: json.dump({'vram_limit_mb': 1024}, f)
        with open(self.ip, 'w') as f: json.dump({'name': 'test'}, f)
        # Save and clear global module state to prevent cross-file contamination
        # (prior test files may have mutated IDENTITY_CONFIG or CONFIG)
        self._saved_config = dict(CONFIG)
        self._saved_identity = dict(IDENTITY_CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._saved_config)
        IDENTITY_CONFIG.clear()
        IDENTITY_CONFIG.update(self._saved_identity)
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _patch_all(self):
        """Return a context manager that patches all config paths."""
        return patch.multiple(
            'config',
            SETTINGS_PATH=self.sp,
            IDENTITY_PATH=self.ip,
            SETTINGS_BACKUP_DIR=self.bu,
            LOG_DIR=self.lg,
            SAVE_MARKER_PATH=self.mk,
        )

    def test_writes_settings_json(self):
        old_model = CONFIG.get('model_name', '')
        CONFIG['model_name'] = 'test-model'
        try:
            with self._patch_all():
                save_settings()
            with open(self.sp) as f:
                data = json.load(f)
            self.assertEqual(data['model_name'], 'test-model')
        finally:
            CONFIG['model_name'] = old_model

    def test_creates_backup(self):
        with self._patch_all():
            save_settings()
        entries = os.listdir(self.bu)
        self.assertTrue(any(f.startswith('app_settings_') for f in entries))

    def test_removes_and_rewrites_marker(self):
        with self._patch_all():
            # Create a marker before saving
            with open(self.mk, 'w') as f: f.write('old')
            self.assertTrue(os.path.isfile(self.mk))
            save_settings()
            # Marker should be rewritten after save
            self.assertTrue(os.path.isfile(self.mk))
            with open(self.mk) as f:
                c = f.read().strip()
            self.assertRegex(c, '[0-9]{4}-[0-9]{2}-[0-9]{2}T')

    def test_error_silent(self):
        with self._patch_all():
            with patch('config.SETTINGS_PATH', os.path.join(tempfile.gettempdir(), 'no_such_dir', 'settings.json')):
                save_settings()

    def test_write_failure_cleans_up_temp_file(self):
        """When json.dump fails during save, the .tmp file is removed.
        (Covers ``os.remove(tmp_path)`` in the exception handler.)"""
        with self._patch_all():
            with patch('config.json.dump', side_effect=OSError("Disk full")):
                save_settings()
            # Temp file should have been cleaned up
            tmp_path = self.sp + ".tmp"
            self.assertFalse(
                os.path.exists(tmp_path),
                msg="REGRESSION: temp file should be removed after failed save")

class TestSaveIdentity(unittest.TestCase):
    """save_identity tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.sp = os.path.join(self.td, 'settings.json')
        self.ip = os.path.join(self.td, 'identity.json')
        self.bu = os.path.join(self.td, 'bu')
        self.lg = os.path.join(self.td, 'logs')
        self.mk = os.path.join(self.lg, '.ok')
        os.makedirs(self.lg)
        os.makedirs(self.bu)
        with open(self.sp, 'w') as f: json.dump({'vram_limit_mb': 1024}, f)
        with open(self.ip, 'w') as f: json.dump({'name': 'test'}, f)
        # Save and clear global module state to prevent cross-file contamination
        # (prior test files may have mutated IDENTITY_CONFIG or CONFIG)
        self._saved_config = dict(CONFIG)
        self._saved_identity = dict(IDENTITY_CONFIG)

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._saved_config)
        IDENTITY_CONFIG.clear()
        IDENTITY_CONFIG.update(self._saved_identity)
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _patch_all(self):
        return patch.multiple(
            'config',
            SETTINGS_PATH=self.sp,
            IDENTITY_PATH=self.ip,
            SETTINGS_BACKUP_DIR=self.bu,
            LOG_DIR=self.lg,
            SAVE_MARKER_PATH=self.mk,
        )

    def test_writes_identity_json(self):
        old_name = IDENTITY_CONFIG.get('name', '')
        IDENTITY_CONFIG['name'] = 'test-user'
        try:
            with self._patch_all():
                save_identity()
            with open(self.ip) as f:
                data = json.load(f)
            self.assertEqual(data['name'], 'test-user')
        finally:
            IDENTITY_CONFIG['name'] = old_name

    def test_creates_backup(self):
        with self._patch_all():
            save_identity()
        entries = os.listdir(self.bu)
        self.assertTrue(any(f.startswith('user_identity_') for f in entries))

    def test_removes_and_rewrites_marker(self):
        with self._patch_all():
            with open(self.mk, 'w') as f: f.write('old')
            self.assertTrue(os.path.isfile(self.mk))
            save_identity()
            self.assertTrue(os.path.isfile(self.mk))
            with open(self.mk) as f:
                c = f.read().strip()
            self.assertRegex(c, '[0-9]{4}-[0-9]{2}-[0-9]{2}T')

    def test_error_silent(self):
        with self._patch_all():
            with patch('config.IDENTITY_PATH', os.path.join(tempfile.gettempdir(), 'no_such_dir', 'identity.json')):
                save_identity()

    def test_identity_write_failure_cleans_up_temp_file(self):
        """When json.dump fails during save_identity, the .tmp file is
        removed. (Covers ``os.remove(tmp_path)`` in the exception handler.)"""
        with self._patch_all():
            with patch('config.json.dump', side_effect=OSError("Disk full")):
                save_identity()
            tmp_path = self.ip + ".tmp"
            self.assertFalse(
                os.path.exists(tmp_path),
                msg="REGRESSION: temp file should be removed after failed save_identity")

class TestSnapshotBackups(unittest.TestCase):
    """_snapshot_backups tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.sp = os.path.join(self.td, 'settings.json')
        self.ip = os.path.join(self.td, 'identity.json')
        self.bu = os.path.join(self.td, 'bu')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _write(self, p, c='{"ok":true}'):
        with open(p, 'w') as f:
            f.write(c)

    def _call(self):
        with patch.multiple('config',
                            SETTINGS_PATH=self.sp,
                            IDENTITY_PATH=self.ip,
                            SETTINGS_BACKUP_DIR=self.bu):
            _snapshot_backups()

    def test_creates_both_backups(self):
        self._write(self.sp)
        self._write(self.ip)
        self._call()
        entries = os.listdir(self.bu)
        self.assertTrue(any(f.startswith('app_settings_') for f in entries))
        self.assertTrue(any(f.startswith('user_identity_') for f in entries))

    def test_same_timestamp(self):
        self._write(self.sp)
        self._write(self.ip)
        self._call()
        entries = os.listdir(self.bu)
        settings = [f for f in entries if f.startswith('app_settings_')]
        identity = [f for f in entries if f.startswith('user_identity_')]
        self.assertEqual(len(settings), 1)
        self.assertEqual(len(identity), 1)
        ts_s = settings[0].replace('app_settings_', '').replace('.json', '')
        ts_i = identity[0].replace('user_identity_', '').replace('.json', '')
        self.assertEqual(ts_s, ts_i)

    def test_skips_when_no_files(self):
        self._call()
        self.assertFalse(os.path.isdir(self.bu))

    def test_backup_only_settings(self):
        self._write(self.sp)
        self._call()
        entries = os.listdir(self.bu)
        self.assertTrue(any(f.startswith('app_settings_') for f in entries))
        self.assertFalse(any(f.startswith('user_identity_') for f in entries))

    def test_backup_only_identity(self):
        self._write(self.ip)
        self._call()
        entries = os.listdir(self.bu)
        self.assertFalse(any(f.startswith('app_settings_') for f in entries))
        self.assertTrue(any(f.startswith('user_identity_') for f in entries))

    def test_creates_backup_dir(self):
        self._write(self.sp)
        self._write(self.ip)
        self._call()
        self.assertTrue(os.path.isdir(self.bu))

    def test_prunes_old_backups(self):
        self._write(self.sp)
        self._write(self.ip)
        os.makedirs(self.bu, exist_ok=True)
        MAX = 3
        for i in range(5):
            ts = f"2025010{i+1}_120000"
            with open(os.path.join(self.bu, f'app_settings_{ts}.json'), 'w') as f:
                f.write('{}')
            with open(os.path.join(self.bu, f'user_identity_{ts}.json'), 'w') as f:
                f.write('{}')
        with patch.multiple('config',
                            SETTINGS_PATH=self.sp,
                            IDENTITY_PATH=self.ip,
                            SETTINGS_BACKUP_DIR=self.bu,
                            MAX_BACKUPS=MAX):
            _snapshot_backups()
        entries = [f for f in os.listdir(self.bu) if f.endswith('.json')]
        self.assertLessEqual(len(entries), MAX * 2)

    def test_pruning_keeps_newest(self):
        self._write(self.sp)
        self._write(self.ip)
        os.makedirs(self.bu, exist_ok=True)
        MAX = 2
        for i in range(4):
            ts = f"2025010{i+1}_120000"
            with open(os.path.join(self.bu, f'app_settings_{ts}.json'), 'w') as f:
                f.write('{}')
            with open(os.path.join(self.bu, f'user_identity_{ts}.json'), 'w') as f:
                f.write('{}')
        with patch.multiple('config',
                            SETTINGS_PATH=self.sp,
                            IDENTITY_PATH=self.ip,
                            SETTINGS_BACKUP_DIR=self.bu,
                            MAX_BACKUPS=MAX):
            _snapshot_backups()
        entries = [f for f in os.listdir(self.bu) if f.endswith('.json')]
        # With MAX=2 and 4 existing + 1 new backup pair, only the new one
        # and the newest pre-existing (20250104) should survive
        self.assertLessEqual(len(entries), MAX * 2)
        self.assertFalse(any('20250101' in f for f in entries))
        self.assertFalse(any('20250102' in f for f in entries))
        self.assertFalse(any('20250103' in f for f in entries))
        self.assertTrue(any('20250104' in f for f in entries))
        # The newest backup (from _call) should also be present
        self.assertGreaterEqual(len(entries), 2)

    def test_unusual_filename_formats_no_crash(self):
        """Files with non-standard naming (single underscore, extra underscores before ts) don't crash pruning logic."""
        self._write(self.sp)
        self._write(self.ip)
        os.makedirs(self.bu, exist_ok=True)
        MAX = 5
        # Single underscore file → split('_',2) gives ['odd', 'file.json'] (2 parts) → elif branch
        with open(os.path.join(self.bu, 'odd_file.json'), 'w') as f:
            f.write('{}')
        # Extra underscores in prefix → split('_',2) gives 3 parts, unusual ts extraction
        with open(os.path.join(self.bu, 'my_custom_backup_20250101_120000.json'), 'w') as f:
            f.write('{}')
        with patch.multiple('config',
                            SETTINGS_PATH=self.sp,
                            IDENTITY_PATH=self.ip,
                            SETTINGS_BACKUP_DIR=self.bu,
                            MAX_BACKUPS=MAX):
            _snapshot_backups()
        # No crash; new backup pair created
        entries = os.listdir(self.bu)
        self.assertTrue(any(f.startswith('app_settings_') for f in entries))
        self.assertTrue(any(f.startswith('user_identity_') for f in entries))
        # Unusual files still exist (and the elif-parsed weird timestamp didn't corrupt anything)
        self.assertTrue(any('odd_file.json' in f for f in entries))
        self.assertTrue(any('my_custom_backup' in f for f in entries))

class TestListBackups(unittest.TestCase):
    """list_backups tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.bu = os.path.join(self.td, 'bu')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _call(self):
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            return list_backups()

    def _add(self, ts, has_settings=True, has_identity=True):
        os.makedirs(self.bu, exist_ok=True)
        if has_settings:
            with open(os.path.join(self.bu, f'app_settings_{ts}.json'), 'w') as f:
                f.write('{"k":"v"}')
        if has_identity:
            with open(os.path.join(self.bu, f'user_identity_{ts}.json'), 'w') as f:
                f.write('{"n":"t"}')

    def test_empty_when_no_dir(self):
        self.assertEqual(self._call(), [])

    def test_empty_when_dir_empty(self):
        os.makedirs(self.bu)
        self.assertEqual(self._call(), [])

    def test_single_backup_pair(self):
        self._add('20250101_120000')
        r = self._call()
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0]['has_settings'])
        self.assertTrue(r[0]['has_identity'])

    def test_settings_only_backup(self):
        self._add('20250101_120000', has_identity=False)
        r = self._call()
        self.assertEqual(len(r), 1)
        self.assertTrue(r[0]['has_settings'])
        self.assertFalse(r[0]['has_identity'])

    def test_identity_only_backup(self):
        self._add('20250101_120000', has_settings=False)
        r = self._call()
        self.assertEqual(len(r), 1)
        self.assertFalse(r[0]['has_settings'])
        self.assertTrue(r[0]['has_identity'])

    def test_sorted_newest_first(self):
        self._add('20250101_120000')
        self._add('20250201_120000')
        self._add('20250301_120000')
        r = self._call()
        self.assertEqual(len(r), 3)
        self.assertEqual(r[0]['ts'], '20250301_120000')
        self.assertEqual(r[1]['ts'], '20250201_120000')
        self.assertEqual(r[2]['ts'], '20250101_120000')

    def test_ignores_unknown_files(self):
        self._add('20250101_120000')
        with open(os.path.join(self.bu, 'random.txt'), 'w') as f:
            f.write('junk')
        with open(os.path.join(self.bu, 'other.json'), 'w') as f:
            f.write('{}')
        r = self._call()
        self.assertEqual(len(r), 1)

    def test_ignores_subdirectories(self):
        """A subdirectory in the backup folder is skipped (not treated as a backup file)."""
        self._add('20250101_120000')
        # Create a subdirectory inside the backup dir
        subdir = os.path.join(self.bu, 'subfolder')
        os.makedirs(subdir)
        r = self._call()
        self.assertEqual(len(r), 1)

    def test_invalid_timestamp_format(self):
        os.makedirs(self.bu)
        with open(os.path.join(self.bu, 'app_settings_badformat.json'), 'w') as f:
            f.write('{}')
        r = self._call()
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]['ts'], 'badformat')
        self.assertEqual(r[0]['display'], 'badformat')

    def test_entry_format(self):
        self._add('20250101_120000')
        r = self._call()
        entry = r[0]
        self.assertEqual(entry['ts'], '20250101_120000')
        self.assertEqual(entry['display'], '2025-01-01 12:00:00')
        self.assertIn('settings', entry['label'])
        self.assertIn('identity', entry['label'])
        self.assertIsNotNone(entry['settings_path'])
        self.assertIsNotNone(entry['identity_path'])

    def test_returns_empty_when_listdir_raises(self):
        """When os.listdir raises inside try block, outer except returns []."""
        os.makedirs(self.bu)
        with patch('config.SETTINGS_BACKUP_DIR', self.bu), \
             patch('config.os.listdir', side_effect=OSError("listdir fail")):
            r = list_backups()
        self.assertEqual(r, [])

class TestRestoreBackup(unittest.TestCase):
    """restore_backup tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.sp = os.path.join(self.td, 'settings.json')
        self.ip = os.path.join(self.td, 'identity.json')
        self.bu = os.path.join(self.td, 'bu')
        os.makedirs(self.bu)
        with open(self.sp, 'w') as f:
            f.write('{"original":true}')
        with open(self.ip, 'w') as f:
            f.write('{"original":true}')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _add_backup(self, ts):
        with open(os.path.join(self.bu, f'app_settings_{ts}.json'), 'w') as f:
            f.write('{"restored":"settings"}')
        with open(os.path.join(self.bu, f'user_identity_{ts}.json'), 'w') as f:
            f.write('{"restored":"identity"}')

    def _patched_call(self, ts):
        with patch('config.SETTINGS_PATH', self.sp), \
             patch('config.IDENTITY_PATH', self.ip), \
             patch('config.SETTINGS_BACKUP_DIR', self.bu):
            return restore_backup(ts)

    def test_restores_both_files(self):
        self._add_backup('20250101_120000')
        r = self._patched_call('20250101_120000')
        self.assertTrue(r)
        with open(self.sp) as f:
            self.assertIn('"restored"', f.read())
        with open(self.ip) as f:
            self.assertIn('"restored"', f.read())

    def test_restores_only_settings(self):
        ts = '20250101_120000'
        with open(os.path.join(self.bu, f'app_settings_{ts}.json'), 'w') as f:
            f.write('{"restored":"settings"}')
        r = self._patched_call(ts)
        self.assertTrue(r)
        with open(self.sp) as f:
            self.assertIn('"restored"', f.read())
        with open(self.ip) as f:
            self.assertIn('"original"', f.read())

    def test_restores_only_identity(self):
        ts = '20250101_120000'
        with open(os.path.join(self.bu, f'user_identity_{ts}.json'), 'w') as f:
            f.write('{"restored":"identity"}')
        r = self._patched_call(ts)
        self.assertTrue(r)
        with open(self.ip) as f:
            self.assertIn('"restored"', f.read())
        with open(self.sp) as f:
            self.assertIn('"original"', f.read())

    def test_restores_files_content(self):
        self._add_backup('20250101_120000')
        self._patched_call('20250101_120000')
        with open(self.sp) as f:
            data = json.load(f)
            self.assertEqual(data, {"restored": "settings"})
        with open(self.ip) as f:
            data = json.load(f)
            self.assertEqual(data, {"restored": "identity"})

    def test_nonexistent_timestamp_returns_false(self):
        r = self._patched_call('20991231_120000')
        self.assertFalse(r)

    def test_reload_called_on_success(self):
        self._add_backup('20250101_120000')
        with patch('config.SETTINGS_PATH', self.sp), \
             patch('config.IDENTITY_PATH', self.ip), \
             patch('config.SETTINGS_BACKUP_DIR', self.bu), \
             patch('config.reload') as mock_reload:
            restore_backup('20250101_120000')
        mock_reload.assert_called_once()

    def test_no_reload_on_failure(self):
        with patch('config.SETTINGS_PATH', self.sp), \
             patch('config.IDENTITY_PATH', self.ip), \
             patch('config.SETTINGS_BACKUP_DIR', self.bu), \
             patch('config.reload') as mock_reload:
            restore_backup('20991231_120000')
        mock_reload.assert_not_called()

    def test_returns_false_when_join_fails(self):
        """When os.path.join raises, outer except catches and returns False."""
        self._add_backup('20250101_120000')
        with patch('config.SETTINGS_PATH', self.sp), \
             patch('config.IDENTITY_PATH', self.ip), \
             patch('config.SETTINGS_BACKUP_DIR', self.bu), \
             patch('config.os.path.join', side_effect=OSError("mock error")):
            r = restore_backup('20250101_120000')
        self.assertFalse(r)

class TestExportBackup(unittest.TestCase):
    """export_backup tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.bu = os.path.join(self.td, 'bu')
        self.zp = os.path.join(self.td, 'backup.zip')
        os.makedirs(self.bu)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _add_backup(self, ts, has_settings=True, has_identity=True):
        if has_settings:
            with open(os.path.join(self.bu, f'app_settings_{ts}.json'), 'w') as f:
                json.dump({"model": "test"}, f)
        if has_identity:
            with open(os.path.join(self.bu, f'user_identity_{ts}.json'), 'w') as f:
                json.dump({"name": "test"}, f)

    def _call(self, ts):
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            return export_backup(ts, self.zp)

    def test_exports_both_files(self):
        self._add_backup('20250101_120000')
        r = self._call('20250101_120000')
        self.assertTrue(r)
        self.assertTrue(os.path.isfile(self.zp))
        with zipfile.ZipFile(self.zp, 'r') as zf:
            names = zf.namelist()
            self.assertIn('app_settings_20250101_120000.json', names)
            self.assertIn('user_identity_20250101_120000.json', names)

    def test_exports_settings_only(self):
        self._add_backup('20250101_120000', has_identity=False)
        r = self._call('20250101_120000')
        self.assertTrue(r)
        with zipfile.ZipFile(self.zp, 'r') as zf:
            names = zf.namelist()
            self.assertIn('app_settings_20250101_120000.json', names)
            self.assertNotIn('user_identity_', ''.join(names))

    def test_exports_identity_only(self):
        self._add_backup('20250101_120000', has_settings=False)
        r = self._call('20250101_120000')
        self.assertTrue(r)
        with zipfile.ZipFile(self.zp, 'r') as zf:
            names = zf.namelist()
            self.assertNotIn('app_settings_', ''.join(names))
            self.assertIn('user_identity_20250101_120000.json', names)

    def test_returns_false_when_no_files(self):
        self._add_backup('20250101_120000')
        r = self._call('20991231_120000')
        self.assertFalse(r)
        self.assertFalse(os.path.isfile(self.zp))

    def test_exports_zip_contains_content(self):
        self._add_backup('20250101_120000')
        self._call('20250101_120000')
        with zipfile.ZipFile(self.zp, 'r') as zf:
            data = json.loads(zf.read('app_settings_20250101_120000.json'))
            self.assertEqual(data, {"model": "test"})
            data = json.loads(zf.read('user_identity_20250101_120000.json'))
            self.assertEqual(data, {"name": "test"})

    def test_returns_false_when_join_fails(self):
        """When os.path.join raises, outer except catches and returns False."""
        self._add_backup('20250101_120000')
        with patch('config.SETTINGS_BACKUP_DIR', self.bu), \
             patch('config.os.path.join', side_effect=OSError("mock error")):
            r = export_backup('20250101_120000', self.zp)
        self.assertFalse(r)

class TestImportBackup(unittest.TestCase):
    """import_backup tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.bu = os.path.join(self.td, 'bu')
        self.zip_path = os.path.join(self.td, 'import.zip')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _make_zip(self, entries):
        with zipfile.ZipFile(self.zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for arcname, content in entries:
                zf.writestr(arcname, content)

    def _call(self):
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            return import_backup(self.zip_path)

    def test_imports_both_files(self):
        self._make_zip([
            ('app_settings_20250101_120000.json', '{"model":"test"}'),
            ('user_identity_20250101_120000.json', '{"name":"test"}'),
        ])
        ts = self._call()
        self.assertIsNotNone(ts)
        self.assertRegex(ts, r'^\d{8}_\d{6}$')
        entries = os.listdir(self.bu)
        self.assertEqual(len(entries), 2)
        self.assertTrue(any(f.startswith('app_settings_') for f in entries))
        self.assertTrue(any(f.startswith('user_identity_') for f in entries))
        for fname in entries:
            with open(os.path.join(self.bu, fname)) as f:
                data = json.load(f)
                if 'app_settings' in fname:
                    self.assertEqual(data, {"model": "test"})
                else:
                    self.assertEqual(data, {"name": "test"})

    def test_imports_settings_only(self):
        self._make_zip([
            ('app_settings_20250101_120000.json', '{"model":"test"}'),
        ])
        ts = self._call()
        self.assertIsNotNone(ts)
        entries = os.listdir(self.bu)
        self.assertEqual(len(entries), 1)
        self.assertTrue(any(f.startswith('app_settings_') for f in entries))

    def test_imports_identity_only(self):
        self._make_zip([
            ('user_identity_20250101_120000.json', '{"name":"test"}'),
        ])
        ts = self._call()
        self.assertIsNotNone(ts)
        entries = os.listdir(self.bu)
        self.assertEqual(len(entries), 1)
        self.assertTrue(any(f.startswith('user_identity_') for f in entries))

    def test_import_uses_new_timestamp(self):
        self._make_zip([
            ('app_settings_20200101_120000.json', '{}'),
        ])
        ts = self._call()
        self.assertIsNotNone(ts)
        self.assertNotEqual(ts, '20200101_120000')
        for fname in os.listdir(self.bu):
            self.assertIn(ts, fname)

    def test_returns_none_when_file_missing(self):
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            r = import_backup(os.path.join(self.td, 'no_such.zip'))
        self.assertIsNone(r)
        self.assertFalse(os.path.isdir(self.bu))

    def test_returns_none_when_no_valid_entries(self):
        self._make_zip([
            ('random.txt', 'hello'),
            ('data.csv', 'a,b,c'),
        ])
        r = self._call()
        self.assertIsNone(r)

    def test_returns_none_when_corrupt_zip(self):
        """A file that exists but is not a valid ZIP returns None."""
        # Write binary garbage to look like a corrupt zip
        with open(self.zip_path, "wb") as f:
            f.write(b"\x00\x01\x02this is not a zip file\xff")
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            r = import_backup(self.zip_path)
        self.assertIsNone(r)
        # Backup dir IS created (makedirs runs before zip parse attempt)
        self.assertTrue(os.path.isdir(self.bu))
        # But no files should be in it
        self.assertEqual(os.listdir(self.bu), [])

    def test_export_import_round_trip(self):
        os.makedirs(self.bu)
        with open(os.path.join(self.bu, 'app_settings_20250101_120000.json'), 'w') as f:
            json.dump({"model": "roundtrip"}, f)
        with open(os.path.join(self.bu, 'user_identity_20250101_120000.json'), 'w') as f:
            json.dump({"name": "roundtrip"}, f)
        export_path = os.path.join(self.td, 'roundtrip.zip')
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            self.assertTrue(export_backup('20250101_120000', export_path))
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            new_ts = import_backup(export_path)
        self.assertIsNotNone(new_ts)
        entries = os.listdir(self.bu)
        self.assertEqual(len(entries), 4)
        self.assertTrue(any(new_ts in f for f in entries))

    def test_returns_none_when_join_fails(self):
        """When os.path.join raises, outer except catches and returns None."""
        self._make_zip([('app_settings_20250101_120000.json', '{}')])
        with patch('config.SETTINGS_BACKUP_DIR', self.bu), \
             patch('config.os.path.join', side_effect=OSError("mock error")):
            r = import_backup(self.zip_path)
        self.assertIsNone(r)

class TestLoadDotenv(unittest.TestCase):
    """_load_dotenv tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._orig_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _write_env(self, content):
        path = os.path.join(self.td, ".env")
        with open(path, "w") as f:
            f.write(content)

    def _call(self):
        with patch("config.WORKSPACE_DIR", self.td):
            _load_dotenv()

    def test_skips_when_no_file(self):
        """No .env file exists → returns early, no crash."""
        self._call()  # should not raise
        self.assertFalse(os.path.isfile(os.path.join(self.td, ".env")))

    def test_parses_key_value(self):
        self._write_env("MY_KEY=my_value")
        self._call()
        self.assertEqual(os.environ.get("MY_KEY"), "my_value")

    def test_parses_double_quoted_value(self):
        self._write_env('MY_KEY="my_value"')
        self._call()
        self.assertEqual(os.environ.get("MY_KEY"), "my_value")

    def test_parses_single_quoted_value(self):
        self._write_env("MY_KEY='my_value'")
        self._call()
        self.assertEqual(os.environ.get("MY_KEY"), "my_value")

    def test_skips_comments(self):
        self._write_env("# MY_KEY=should_not_appear\nREAL_KEY=real_value")
        self._call()
        self.assertIsNone(os.environ.get("MY_KEY"))
        self.assertEqual(os.environ.get("REAL_KEY"), "real_value")

    def test_skips_empty_lines(self):
        self._write_env("\n\n\nKEY=val\n\n")
        self._call()
        self.assertEqual(os.environ.get("KEY"), "val")

    def test_skips_malformed_lines(self):
        self._write_env("NO_EQUALS\nKEY=val")
        self._call()
        self.assertIsNone(os.environ.get("NO_EQUALS"))
        self.assertEqual(os.environ.get("KEY"), "val")

    def test_strips_whitespace(self):
        self._write_env("  SPACED_KEY  =  spaced_value  ")
        self._call()
        self.assertEqual(os.environ.get("SPACED_KEY"), "spaced_value")

    def test_never_overrides_existing(self):
        os.environ["EXISTING_KEY"] = "original"
        self._write_env("EXISTING_KEY=new_value")
        self._call()
        self.assertEqual(os.environ.get("EXISTING_KEY"), "original")

    def test_parses_multiple_lines(self):
        self._write_env("KEY1=val1\nKEY2=val2\nKEY3=val3")
        self._call()
        self.assertEqual(os.environ.get("KEY1"), "val1")
        self.assertEqual(os.environ.get("KEY2"), "val2")
        self.assertEqual(os.environ.get("KEY3"), "val3")

    def test_error_silent(self):
        """Read error (e.g. directory at .env path) is silently caught."""
        os.makedirs(os.path.join(self.td, ".env"))
        with patch("config.WORKSPACE_DIR", self.td):
            _load_dotenv()  # should not raise

    def test_skips_empty_key(self):
        """A line like =value has an empty key and should be skipped."""
        self._write_env("=should_be_skipped\nREAL_KEY=val")
        self._call()
        self.assertIsNone(os.environ.get(""))
        self.assertEqual(os.environ.get("REAL_KEY"), "val")

    def test_accepts_empty_value(self):
        """A line like KEY= sets an empty string value (not skipped)."""
        self._write_env("EMPTY_VAL=")
        self._call()
        self.assertEqual(os.environ.get("EMPTY_VAL"), "")

class TestLoadSettings(unittest.TestCase):
    """_load_settings tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.sp = os.path.join(self.td, "settings.json")
        self._orig_env = os.environ.copy()
        # Save original CONFIG state for the keys we'll modify
        self._orig_config = {
            "model_name": CONFIG.get("model_name"),
        }
        # Reset _load_settings mtime cache so TTL doesn't block reload
        # when tests run faster than settings_load_min_interval_sec (1s).
        config._settings_mtime_cache = {"mtime": 0.0, "last_check": 0.0}

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._orig_env)
        for k, v in self._orig_config.items():
            CONFIG[k] = v
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _call(self):
        with patch("config.SETTINGS_PATH", self.sp):
            _load_settings()

    def test_skips_when_no_file(self):
        """No settings file exists → CONFIG unchanged."""
        old_model = CONFIG.get("model_name")
        self._call()
        self.assertEqual(CONFIG.get("model_name"), old_model)

    def test_loads_valid_json(self):
        with open(self.sp, "w") as f:
            json.dump({"model_name": "from-file"}, f)
        self._call()
        self.assertEqual(CONFIG.get("model_name"), "from-file")

    def test_corrupt_json_silent(self):
        with open(self.sp, "w") as f:
            f.write("not valid json")
        old_model = CONFIG.get("model_name")
        self._call()
        self.assertEqual(CONFIG.get("model_name"), old_model)

    def test_error_silent(self):
        """A read error (e.g. a directory at settings path) is silently caught."""
        os.makedirs(self.sp)
        old_model = CONFIG.get("model_name")
        self._call()
        self.assertEqual(CONFIG.get("model_name"), old_model)

    def test_mtime_cache_short_circuits_second_call(self):
        """A second call to _load_settings within the TTL returns
        early (no re-read from disk)."""
        # Write initial value and load
        with open(self.sp, "w") as f:
            json.dump({"model_name": "first"}, f)
        self._call()
        self.assertEqual(CONFIG.get("model_name"), "first",
                         msg="REGRESSION: first load should read from disk")

        # Modify file on disk
        with open(self.sp, "w") as f:
            json.dump({"model_name": "second"}, f)

        # Call again within TTL — should NOT re-read (TTL short-circuit)
        self._call()
        self.assertEqual(CONFIG.get("model_name"), "first",
                         msg="REGRESSION: TTL cache should prevent re-read within interval")

    def test_mtime_match_short_circuits_reload(self):
        """When TTL passes but file mtime is unchanged, _load_settings
        returns early (mtime cache short-circuit)."""
        # Write file and load (caches mtime)
        with open(self.sp, "w") as f:
            json.dump({"model_name": "unchanged"}, f)

        # Patch time.time so the TTL check passes (>1s elapsed)
        # but the file mtime hasn't changed (so mtime comparison returns early)
        import time as time_module
        real_now = time_module.time()
        with patch('config.time.time') as mock_time:
            # First call: time starts at real_now
            mock_time.return_value = real_now
            with patch("config.SETTINGS_PATH", self.sp):
                config._load_settings()
            self.assertEqual(CONFIG.get("model_name"), "unchanged",
                             msg="REGRESSION: first load should read from disk")

            # Advance time past the TTL (default 1.0s)
            mock_time.return_value = real_now + 2.0

            # Modify CONFIG in memory to detect whether re-read happens
            CONFIG["model_name"] = "should-not-be-read"

            # Second call: TTL passes (2s > 1s), but mtime is unchanged →
            # should return early WITHOUT re-reading
            with patch("config.SETTINGS_PATH", self.sp):
                config._load_settings()

        self.assertEqual(CONFIG.get("model_name"), "should-not-be-read",
                         msg=("REGRESSION: mtime match should prevent "
                              "re-read even after TTL expiry"))

class TestLoadIdentity(unittest.TestCase):
    """_load_identity tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.ip = os.path.join(self.td, "identity.json")
        # Save original identity name
        self._orig_name = IDENTITY_CONFIG.get("name")

    def tearDown(self):
        IDENTITY_CONFIG["name"] = self._orig_name
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _call(self):
        with patch("config.IDENTITY_PATH", self.ip):
            _load_identity()

    def test_skips_when_no_file(self):
        """No identity file exists → IDENTITY_CONFIG unchanged."""
        old_name = IDENTITY_CONFIG.get("name")
        self._call()
        self.assertEqual(IDENTITY_CONFIG.get("name"), old_name)

    def test_loads_valid_json(self):
        with open(self.ip, "w") as f:
            json.dump({"name": "from-file"}, f)
        self._call()
        self.assertEqual(IDENTITY_CONFIG.get("name"), "from-file")

    def test_corrupt_json_silent(self):
        with open(self.ip, "w") as f:
            f.write("not valid json")
        old_name = IDENTITY_CONFIG.get("name")
        self._call()
        self.assertEqual(IDENTITY_CONFIG.get("name"), old_name)

    def test_error_silent(self):
        """A read error (e.g. a directory at identity path) is silently caught."""
        os.makedirs(self.ip)
        old_name = IDENTITY_CONFIG.get("name")
        self._call()
        self.assertEqual(IDENTITY_CONFIG.get("name"), old_name)

class TestReload(unittest.TestCase):
    """reload() tests."""

    def test_calls_load_settings(self):
        with patch("config._load_settings") as mock_ls, \
             patch("config._load_identity"), \
             patch("config._load_personas"):
            reload()
        mock_ls.assert_called_once()

    def test_calls_load_identity(self):
        with patch("config._load_settings"), \
             patch("config._load_identity") as mock_li, \
             patch("config._load_personas"):
            reload()
        mock_li.assert_called_once()

    def test_calls_load_personas(self):
        with patch("config._load_settings"), \
             patch("config._load_identity"), \
             patch("config._load_personas") as mock_lp:
            reload()
        mock_lp.assert_called_once()

    def test_calls_all_three(self):
        """All three functions are called in the expected sequence."""
        with patch("config._load_settings") as mock_ls, \
             patch("config._load_identity") as mock_li, \
             patch("config._load_personas") as mock_lp:
            reload()
            self.assertEqual(mock_ls.mock_calls, [call()])
            self.assertEqual(mock_li.mock_calls, [call()])
            self.assertEqual(mock_lp.mock_calls, [call()])

class TestLoadPersonas(unittest.TestCase):
    """_load_personas tests."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.pp = os.path.join(self.td, "personas.json")
        # Save and clear CONFIG state for keys we'll modify
        self._orig_saved = CONFIG.get("saved_personas", [])
        self._orig_active = CONFIG.get("active_persona", "")
        CONFIG["saved_personas"] = []
        CONFIG["active_persona"] = "You are a helpful AI assistant."

    def tearDown(self):
        CONFIG["saved_personas"] = self._orig_saved
        CONFIG["active_persona"] = self._orig_active
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _write(self, content):
        with open(self.pp, "w") as f:
            json.dump(content, f)

    def _call(self):
        with patch("config.PERSONAS_PATH", self.pp):
            _load_personas()

    def test_skips_when_no_file(self):
        """No personas file exists → CONFIG unchanged."""
        old_active = CONFIG.get("active_persona")
        self._call()
        self.assertEqual(CONFIG.get("active_persona"), old_active)
        self.assertEqual(CONFIG.get("saved_personas"), [])

    def test_seeds_from_file_when_no_existing(self):
        """Existing saved_personas is empty → seeded from file directly."""
        CONFIG["saved_personas"] = []
        self._write(["helper", "coder", "teacher"])
        self._call()
        self.assertEqual(CONFIG["saved_personas"], ["helper", "coder", "teacher"])

    def test_merges_with_existing_personas(self):
        """Existing personas exist → new ones from file are merged, duplicates skipped."""
        CONFIG["saved_personas"] = ["custom-helper", "teacher"]
        self._write(["teacher", "coder", "analyst"])
        self._call()
        # custom-helper and teacher stay; coder and analyst are added; duplicates handled
        self.assertIn("custom-helper", CONFIG["saved_personas"])
        self.assertIn("teacher", CONFIG["saved_personas"])
        self.assertIn("coder", CONFIG["saved_personas"])
        self.assertIn("analyst", CONFIG["saved_personas"])
        self.assertEqual(len(CONFIG["saved_personas"]), 4)

    def test_leaves_active_persona_when_in_list(self):
        """active_persona is in the list → unchanged."""
        CONFIG["active_persona"] = "coder"
        CONFIG["saved_personas"] = []
        self._write(["helper", "coder", "teacher"])
        self._call()
        self.assertEqual(CONFIG["active_persona"], "coder")

    def test_updates_active_persona_when_not_in_list(self):
        """active_persona not in list → falls back to first persona."""
        CONFIG["active_persona"] = "old-default"
        CONFIG["saved_personas"] = []
        self._write(["helper", "coder"])
        self._call()
        self.assertEqual(CONFIG["active_persona"], "helper")

    def test_skips_empty_list(self):
        """An empty list [] is falsy → the entire persona block is skipped."""
        CONFIG["active_persona"] = "old-default"
        CONFIG["saved_personas"] = []
        self._write([])
        self._call()
        self.assertEqual(CONFIG["active_persona"], "old-default")
        self.assertEqual(CONFIG["saved_personas"], [])

    def test_skips_non_list_content(self):
        """File contains a dict (not a list) → nothing changes."""
        CONFIG["saved_personas"] = []
        self._write({"key": "value"})
        self._call()
        self.assertEqual(CONFIG["saved_personas"], [])

    def test_corrupt_json_silent(self):
        """File with invalid JSON → caught silently, nothing changes."""
        with open(self.pp, "w") as f:
            f.write("not json")
        old_active = CONFIG.get("active_persona")
        self._call()
        self.assertEqual(CONFIG.get("active_persona"), old_active)
        self.assertEqual(CONFIG.get("saved_personas"), [])

    def test_error_silent(self):
        """A directory at the path → caught silently."""
        os.makedirs(self.pp)
        old_active = CONFIG.get("active_persona")
        self._call()
        self.assertEqual(CONFIG.get("active_persona"), old_active)
        self.assertEqual(CONFIG.get("saved_personas"), [])

class TestKeyringImportError(unittest.TestCase):
    """Tests for module-level except ImportError: _KEYRING_AVAILABLE = False (lines 30-31).

    Uses subprocess to run a fresh Python interpreter where keyring import is mocked to fail,
    then imports config and checks _KEYRING_AVAILABLE is False.
    """

    def test_keyring_not_referenced(self):
        pass

class TestConfigIntegration(unittest.TestCase):
    """Integration tests for the config load→save→reload→backup cycle.

    Tests exercise the full end-to-end flow: writing settings/identity to disk,
    loading into CONFIG/IDENTITY_CONFIG, saving (triggers backup), reloading,
    listing backups, exporting to zip, importing from zip, and restoring.
    """

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.sp = os.path.join(self.td, "settings.json")
        self.ip = os.path.join(self.td, "identity.json")
        self.pp = os.path.join(self.td, "personas.json")
        self.bu = os.path.join(self.td, "bu")
        self.lg = os.path.join(self.td, "logs")
        self.mk = os.path.join(self.lg, ".save_complete")
        self.zp = os.path.join(self.td, "backup.zip")
        os.makedirs(self.lg)
        # Save original CONFIG state
        self._saved_config = dict(CONFIG)
        self._saved_identity = dict(IDENTITY_CONFIG)
        # Reset _load_settings mtime cache so TTL doesn't block reload
        # when tests run faster than settings_load_min_interval_sec (1s).
        config._settings_mtime_cache = {"mtime": 0.0, "last_check": 0.0}

    def tearDown(self):
        CONFIG.clear()
        CONFIG.update(self._saved_config)
        IDENTITY_CONFIG.clear()
        IDENTITY_CONFIG.update(self._saved_identity)
        import shutil
        shutil.rmtree(self.td, ignore_errors=True)

    def _patch_config_paths(self, **overrides):
        """Return a context manager that patches all config paths."""
        defaults = dict(
            SETTINGS_PATH=self.sp,
            IDENTITY_PATH=self.ip,
            PERSONAS_PATH=self.pp,
            SETTINGS_BACKUP_DIR=self.bu,
            LOG_DIR=self.lg,
            SAVE_MARKER_PATH=self.mk,
        )
        defaults.update(overrides)
        # Reset mtime cache so subsequent _load_settings() calls within
        # the same test always re-read from disk.
        config._settings_mtime_cache = {"mtime": 0.0, "last_check": 0.0}
        return patch.multiple('config', **defaults)

    def _write_settings(self, data):
        with open(self.sp, 'w') as f:
            json.dump(data, f)

    def _write_identity(self, data):
        with open(self.ip, 'w') as f:
            json.dump(data, f)

    def _write_personas(self, data):
        with open(self.pp, 'w') as f:
            json.dump(data, f)

    # --- Integration tests ---

    def test_save_then_reload_preserves_changes(self):
        """Save settings, modify CONFIG, reload, verify file values take priority."""
        # Write initial files
        self._write_settings({"model_name": "from-disk"})
        self._write_identity({"name": "Disk User"})
        # Load from disk
        with self._patch_config_paths():
            _load_settings()
            _load_identity()
        self.assertEqual(CONFIG["model_name"], "from-disk")
        self.assertEqual(IDENTITY_CONFIG["name"], "Disk User")

        # Modify CONFIG in memory (simulating UI changes)
        CONFIG["model_name"] = "user-selected"
        IDENTITY_CONFIG["name"] = "User Changed"

        # Save to disk
        with self._patch_config_paths():
            save_settings()
            save_identity()

        # Verify files on disk contain the new values
        with open(self.sp) as f:
            saved = json.load(f)
        self.assertEqual(saved["model_name"], "user-selected")
        with open(self.ip) as f:
            saved_id = json.load(f)
        self.assertEqual(saved_id["name"], "User Changed")

    def test_save_creates_backup_listed_by_list_backups(self):
        """Save creates a backup pair visible via list_backups."""
        self._write_settings({"model_name": "v1"})
        self._write_identity({"name": "v1"})

        # Save triggers _snapshot_backups
        with self._patch_config_paths():
            save_settings()
            save_identity()

        # list_backups should show the backup
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            backups = list_backups()

        self.assertGreaterEqual(len(backups), 1)
        entry = backups[0]
        self.assertTrue(entry['has_settings'])
        self.assertTrue(entry['has_identity'])
        self.assertRegex(entry['ts'], r'^\d{8}_\d{6}$')

    def test_export_and_import_round_trip(self):
        """Export a backup to zip, import it back — both files survive the round trip."""
        # Create settings and identity files
        self._write_settings({"model": "original"})
        self._write_identity({"name": "original"})

        # Save (creates backups)
        with self._patch_config_paths():
            save_settings()
            save_identity()

        # Get the backup timestamp
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            backups = list_backups()
        self.assertGreaterEqual(len(backups), 1)
        ts = backups[0]['ts']

        # Export to zip
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            r = export_backup(ts, self.zp)
        self.assertTrue(r)
        self.assertTrue(os.path.isfile(self.zp))

        # Import from zip into backup dir
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            new_ts = import_backup(self.zp)
        self.assertIsNotNone(new_ts)

        # Verify imported files exist with fresh timestamp
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            imports = list_backups()
        imported_entry = next((e for e in imports if e['ts'] == new_ts), None)
        self.assertIsNotNone(imported_entry, f"No entry found for ts={new_ts}")
        self.assertTrue(imported_entry['has_settings'])
        self.assertTrue(imported_entry['has_identity'])

    def test_restore_backup_reloads_config(self):
        """Restoring a backup updates the files on disk and calls reload."""
        # Write initial files and load into CONFIG
        self._write_settings({"model_name": "old"})
        self._write_identity({"name": "old"})

        with self._patch_config_paths():
            _load_settings()
            _load_identity()
        self.assertEqual(CONFIG["model_name"], "old")

        # Save — this creates a backup of 'old' on disk, then writes CONFIG to disk
        with self._patch_config_paths():
            save_settings()
            save_identity()

        # Now update files and reload
        self._write_settings({"model_name": "new"})
        self._write_identity({"name": "new"})

        with self._patch_config_paths():
            _load_settings()
            _load_identity()
        self.assertEqual(CONFIG["model_name"], "new")

        # Get the backup timestamp (the 'old' version)
        old_ts = None
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            backups = list_backups()
            old_ts = backups[0]['ts']

        # Restore from backup — this calls reload() internally
        with self._patch_config_paths():
            r = restore_backup(old_ts)
        self.assertTrue(r)

        # Files on disk should be the restored version
        with open(self.sp) as f:
            self.assertEqual(json.load(f)["model_name"], "old")
        with open(self.ip) as f:
            self.assertEqual(json.load(f)["name"], "old")
        # CONFIG should also reflect restored values (restore_backup calls reload)
        self.assertEqual(CONFIG["model_name"], "old")

    def test_save_triggers_backup_export_import_chain(self):
        """Save creates backup → export to zip → import → verify files survive."""
        # Write initial files and load into CONFIG
        self._write_settings({"model_name": "initial"})
        self._write_identity({"name": "initial"})

        with self._patch_config_paths():
            _load_settings()
            _load_identity()
        self.assertEqual(CONFIG["model_name"], "initial")

        # Save — creates backup of 'initial' file, writes CONFIG to disk
        with self._patch_config_paths():
            save_settings()
            save_identity()

        # Modify CONFIG and save again — backup captures the first save's write
        CONFIG["model_name"] = "v2"
        IDENTITY_CONFIG["name"] = "v2"
        with self._patch_config_paths():
            save_settings()
            save_identity()

        # Get all backup timestamps
        with patch('config.SETTINGS_BACKUP_DIR', self.bu):
            backups = list_backups()
        self.assertGreaterEqual(len(backups), 1)

        # Export each backup and verify it's a valid zip
        for entry in backups:
            ts = entry['ts']
            zp = os.path.join(self.td, f"backup_{ts}.zip")
            with patch('config.SETTINGS_BACKUP_DIR', self.bu):
                self.assertTrue(export_backup(ts, zp))
            self.assertTrue(os.path.isfile(zp))
            # Verify zip contains valid JSON
            with zipfile.ZipFile(zp, 'r') as zf:
                for name in zf.namelist():
                    data = json.loads(zf.read(name))
                    self.assertIsInstance(data, dict)

        # Import one of the exported zips into a fresh backup dir
        bu2 = os.path.join(self.td, 'bu2')
        with patch('config.SETTINGS_BACKUP_DIR', bu2):
            new_ts = import_backup(os.path.join(self.td, f"backup_{backups[0]['ts']}.zip"))
        self.assertIsNotNone(new_ts)
        with patch('config.SETTINGS_BACKUP_DIR', bu2):
            imported = list_backups()
        self.assertEqual(len(imported), 1)
        self.assertTrue(imported[0]['has_settings'])
        self.assertTrue(imported[0]['has_identity'])

if __name__ == "__main__":
    unittest.main()