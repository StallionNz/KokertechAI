"""Tests for tabs/health_tab.py -- Health Dashboard tab mixin."""
import unittest
from unittest.mock import patch, MagicMock

class TestHistorySparkline(unittest.TestCase):
    def test_push_stores_data(self):
        from tabs.health_tab import HistorySparkline
        spark = HistorySparkline(max_points=5)
        spark.push(10.0)
        spark.push(20.0)
        self.assertEqual(spark._data, [10.0, 20.0])

    def test_push_trims_buffer(self):
        from tabs.health_tab import HistorySparkline
        spark = HistorySparkline(max_points=3)
        for v in [1, 2, 3, 4, 5]:
            spark.push(v)
        self.assertEqual(spark._data, [3, 4, 5])

    def test_push_updates_min_max(self):
        from tabs.health_tab import HistorySparkline
        spark = HistorySparkline()
        spark.push(50.0)
        spark.push(120.0)
        self.assertEqual(spark._min_val, 0.0)
        self.assertEqual(spark._max_val, 120.0)

    def test_default_config(self):
        from tabs.health_tab import HistorySparkline
        spark = HistorySparkline()
        self.assertEqual(spark._max_points, 60)
        self.assertEqual(spark._data, [])


class TestHealthTabMixin(unittest.TestCase):
    def setUp(self):
        self._p = patch("tabs.health_tab.QTimer", return_value=MagicMock())
        self._p.start()
    def tearDown(self):
        self._p.stop()
    def _mk(self, obj):
        with patch.object(obj, "_update_health"):
            obj._tab = obj.create_health_tab()

    def test_create_tab_returns_widget(self):
        from PyQt6.QtWidgets import QWidget
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        self.assertIsInstance(obj._tab, QWidget)
        self.assertTrue(hasattr(obj, "health_cpu_bar"))
        self.assertTrue(hasattr(obj, "health_ram_bar"))
        self.assertTrue(hasattr(obj, "health_vram_bar"))
        self.assertTrue(hasattr(obj, "health_disk_bar"))
        self.assertTrue(hasattr(obj, "health_uptime_lbl"))
        self.assertTrue(hasattr(obj, "health_events_table"))
        self.assertTrue(hasattr(obj, "health_process_table"))

    def test_cpu_bar_range(self):
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        self.assertEqual(obj.health_cpu_bar.minimum(), 0)
        self.assertEqual(obj.health_cpu_bar.maximum(), 100)

    def test_process_table_has_4_columns(self):
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        self.assertEqual(obj.health_process_table.columnCount(), 4)
        labels = [obj.health_process_table.horizontalHeaderItem(i).text() for i in range(4)]
        self.assertEqual(labels, ["Process", "PID", "CPU %", "RAM (MB)"])

    def test_events_table_has_3_columns(self):
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        self.assertEqual(obj.health_events_table.columnCount(), 3)
        labels = [obj.health_events_table.horizontalHeaderItem(i).text() for i in range(3)]
        self.assertEqual(labels, ["Time", "Type", "Message"])

    def test_format_uptime_seconds(self):
        from tabs.health_tab import HealthTabMixin
        self.assertEqual(HealthTabMixin._format_uptime(30), "30s")
        self.assertEqual(HealthTabMixin._format_uptime(90), "1m 30s")
        self.assertEqual(HealthTabMixin._format_uptime(3661), "1h 1m 1s")
        self.assertEqual(HealthTabMixin._format_uptime(90061), "1d 1h 1m")

    def test_log_health_event_stores_event(self):
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        obj.log_health_event("info", "test message")
        self.assertEqual(len(obj._health_events), 1)
        self.assertEqual(obj._health_events[0][1], "info")
        self.assertEqual(obj._health_events[0][2], "test message")

    def test_log_health_event_error_tracks_timestamps(self):
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        obj.log_health_event("error", "crash")
        self.assertEqual(len(obj._health_error_timestamps), 1)

    def test_set_health_provider_status(self):
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        obj._set_health_provider_status("🟢", "Test Model", "#10B981")
        self.assertIn("Test Model", obj.health_provider_status.text())

    def test_push_batch_stores_values(self):
        """HistorySparkline.push_batch replaces data and recalculates bounds."""
        from tabs.health_tab import HistorySparkline
        spark = HistorySparkline(max_points=10)
        spark.push_batch([10.0, 20.0, 30.0])
        self.assertEqual(spark._data, [10.0, 20.0, 30.0])
        self.assertEqual(spark._min_val, 10.0)
        self.assertEqual(spark._max_val, 30.0)

    def test_push_batch_empty_returns_early(self):
        """HistorySparkline.push_batch with empty list returns immediately.

        Covers the ``if not values: return`` guard (line ~61).
        """
        from tabs.health_tab import HistorySparkline
        spark = HistorySparkline()
        spark._data = [1.0]
        spark.push_batch([])
        # Data should be unchanged
        self.assertEqual(spark._data, [1.0])

    def test_push_updates_min(self):
        """HistorySparkline.push updates _min_val when value is lower."""
        from tabs.health_tab import HistorySparkline
        spark = HistorySparkline()
        spark.push(50.0)   # _min_val stays 0.0 (initial)
        spark.push(-5.0)   # should update _min_val to -5.0
        self.assertEqual(spark._min_val, -5.0)

    def test_update_health_provider_status_sync_path(self):
        """_update_health_provider_status takes sync path when no controller.

        Covers the no-controller fallback that sets
        red offline status synchronously per Zero-Trust Invariant 5.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        # Ensure throttle is bypassed
        obj._last_health_provider_check = 0
        with patch("time.time", return_value=100.0):
            obj._update_health_provider_status()
        self.assertIn("Offline", obj.health_provider_status.text())

    def test_set_health_provider_status_shutting_down_returns_early(self):
        """_set_health_provider_status returns immediately when
        ``_shutting_down`` is True, covering the guard (line ~366).
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        obj._shutting_down = True
        original_text = obj.health_provider_status.text()
        obj._set_health_provider_status("\U0001f534", "Should Not Appear", "#EF4444")
        self.assertEqual(obj.health_provider_status.text(), original_text,
            msg="REGRESSION: should not update status when shutting down")

    def test_log_health_event_trims_at_200(self):
        """log_health_event keeps only last 200 events.

        Covers lines 380-381: the trim logic.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        for i in range(210):
            obj.log_health_event("info", f"msg {i}")
        self.assertEqual(len(obj._health_events), 200)
        self.assertEqual(obj._health_events[0][2], "msg 10")

    def test_log_health_event_filters_stale_errors(self):
        """log_health_event for error type cleans timestamps older than 1h."""
        from datetime import datetime, timedelta
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        old_ts = datetime.now() - timedelta(hours=2)
        obj._health_error_timestamps.append(old_ts)
        obj.log_health_event("error", "new error")
        # old timestamp should be filtered out, new one added
        self.assertEqual(len(obj._health_error_timestamps), 1)

    def test_update_health_cpu_high_styling(self):
        """_update_health uses red styling when CPU > 80%.

        Covers lines 399-402: the CPU > 80 threshold branch.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 90.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("EF4444", obj.health_cpu_bar.styleSheet())

    def test_update_health_disk_critical_threshold(self):
        """_update_health uses red styling when disk >= critical (95%).

        Covers lines 432-435: the disk >= critical threshold.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 96.0
        mock_disk.free = 5 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("EF4444", obj.health_disk_bar.styleSheet())

    def test_update_health_ram_critical_threshold(self):
        """_update_health uses red styling when RAM >= critical (95%).

        Covers the CONFIG-driven ram_critical_pct threshold branch.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 96.0
        mock_vmem.used = 15 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("EF4444", obj.health_ram_bar.styleSheet())

    def test_update_health_ram_warning_threshold(self):
        """_update_health uses amber styling when RAM >= warning (80%).

        Covers the CONFIG-driven ram_warning_pct threshold branch.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 85.0
        mock_vmem.used = 13 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("FBBF24", obj.health_ram_bar.styleSheet())

    def test_update_health_vram_warning_threshold(self):
        """_update_health uses amber styling when VRAM >= warning (88%).

        Covers the CONFIG-driven vram_warning_pct threshold branch.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run",
                   return_value=MagicMock(
                       returncode=0, stdout="7373, 8192"  # 90% — above warn 88
                   )):
            obj._update_health()
        self.assertIn("FBBF24", obj.health_vram_bar.styleSheet())

    def test_update_health_vram_critical_threshold(self):
        """_update_health uses red styling when VRAM >= critical (95%).

        Covers the CONFIG-driven vram_critical_pct threshold branch.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run",
                   return_value=MagicMock(
                       returncode=0, stdout="7864, 8192"  # 96% — above crit 95
                   )):
            obj._update_health()
        self.assertIn("EF4444", obj.health_vram_bar.styleSheet())

    def test_update_health_vram_na_resets_color(self):
        """_update_health resets VRAM bar to default amber when VRAM N/A.

        Covers the else branch (vram_total == 0): after a prior poll colored
        the bar red, an N/A poll must reset the chunk color to #F59E0B so a
        stale warning color doesn't linger at 0%.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        # Simulate a prior red-colored bar
        obj.health_vram_bar.setStyleSheet(
            "QProgressBar::chunk { background-color: #EF4444; }"
        )
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("F59E0B", obj.health_vram_bar.styleSheet())
        self.assertEqual(obj.health_vram_label.text(), "N/A")

    def test_update_health_disk_warning_threshold(self):
        """_update_health uses amber styling when disk >= warning (85%).

        Covers lines 436-439: the disk >= warning threshold.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 88.0
        mock_disk.free = 20 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("FBBF24", obj.health_disk_bar.styleSheet())

    def test_update_health_vram_available(self):
        """_update_health uses VRAM gauge when _get_vram returns >0 total.

        Covers lines 447-455: the VRAM > 0 path with setValue / label / sparkline.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run",
                   return_value=MagicMock(
                       returncode=0, stdout="2048, 8192"
                   )):
            obj._update_health()
        self.assertIn("2048/8192 MB", obj.health_vram_label.text())

    def test_update_health_error_count_coloring(self):
        """_update_health turns error count red when errors > 0.

        Covers lines 475-478: the error count > 0 styling branch.
        """
        from tabs.health_tab import HealthTabMixin
        from datetime import datetime
        obj = HealthTabMixin()
        self._mk(obj)
        obj._health_error_timestamps.append(datetime.now())
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 10.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 30.0
        mock_vmem.used = 4 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 20.0
        mock_disk.free = 200 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), \
             patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("Errors: 1", obj.health_error_count_lbl.text())
        self.assertIn("EF4444", obj.health_error_count_lbl.styleSheet())

    def test_update_health_import_error_sets_psutil_required(self):
        """_update_health without psutil shows 'psutil required' message.

        Covers lines 501-502: the ImportError handler.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        # Remove psutil from sys.modules to trigger ImportError
        with patch.dict("sys.modules", {"psutil": None}):
            with patch("builtins.__import__") as mock_import:
                def _import_side_effect(name, *args, **kwargs):
                    if name == "psutil":
                        raise ImportError("No module named psutil")
                    return __import__(name, *args, **kwargs)
                mock_import.side_effect = _import_side_effect
                obj._update_health()
        self.assertEqual(obj.health_cpu_label.text(), "psutil required")

    def test_load_resource_history_creates_buffer_and_seeds(self):
        """_load_resource_history creates a ring buffer and seeds sparklines.

        Covers lines 506-533: the entire method.
        """
        from tabs.health_tab import HealthTabMixin, HistorySparkline
        obj = HealthTabMixin()
        self._mk(obj)

        mock_rh = MagicMock()
        mock_rh.get_series.side_effect = lambda m: {
            "cpu": [10, 20, 30],
            "ram": [40, 50, 60],
            "vram": [70, 80],
            "disk": [90],
        }.get(m, [])

        with patch("tabs.health_tab.resource_history.ResourceHistoryRingBuffer.load",
                   return_value=mock_rh):
            obj._load_resource_history()

        self.assertIs(obj._resource_history, mock_rh)
        self.assertEqual(obj._cpu_sparkline._data, [10, 20, 30])
        self.assertEqual(obj._ram_sparkline._data, [40, 50, 60])
        self.assertEqual(obj._disk_sparkline._data, [90])

    def test_save_resource_history_immediate_calls_save(self):
        """_save_resource_history_immediate calls save_immediate on buffer.

        Covers lines 542-544: the method body.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_rh = MagicMock()
        obj._resource_history = mock_rh
        obj._save_resource_history_immediate()
        mock_rh.save_immediate.assert_called_once()

    def test_get_vram_fallback_returns_zero_zero(self):
        """_get_vram returns (0, 0) when all VRAM methods fail.

        Covers line 576: the fallback return.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        with patch("subprocess.run", side_effect=Exception("nvidia-smi not found")), \
             patch("builtins.__import__") as mock_import:
            def _import_side_effect(name, *args, **kwargs):
                if name == "GPUtil":
                    raise ImportError("No GPUtil")
                return __import__(name, *args, **kwargs)
            mock_import.side_effect = _import_side_effect
            used, total = obj._get_vram()
        self.assertEqual(used, 0)
        self.assertEqual(total, 0)

    def test_get_vram_nvidia_smi_success(self):
        """_get_vram parses nvidia-smi output correctly.

        Covers lines 560-562: parsing nvidia-smi output.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        with patch("subprocess.run",
                   return_value=MagicMock(
                       returncode=0, stdout="4096, 8192"
                   )):
            used, total = obj._get_vram()
        self.assertEqual(used, 4096.0)
        self.assertEqual(total, 8192.0)

    def test_get_vram_gputil_fallback(self):
        """_get_vram falls back to GPUtil when nvidia-smi fails.

        Covers lines 568-572: the GPUtil path.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_gpu = MagicMock()
        mock_gpu.memoryUsed = 2048
        mock_gpu.memoryTotal = 4096
        mock_gputil = MagicMock()
        mock_gputil.getGPUs.return_value = [mock_gpu]
        with patch("subprocess.run", side_effect=Exception("nvidia-smi failed")), \
             patch.dict("sys.modules", {"GPUtil": mock_gputil}):
            used, total = obj._get_vram()
        self.assertEqual(used, 2048)
        self.assertEqual(total, 4096)

    def test_update_process_table_no_psutil_returns_early(self):
        """_update_process_table returns immediately when psutil is absent.

        Covers lines 584-588: the ImportError guard.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        obj._psutil_available = False
        with patch.object(obj, "health_process_table") as mock_table:
            obj._update_process_table()
        mock_table.setRowCount.assert_not_called()

    def test_update_process_table_enumerates_processes(self):
        """_update_process_table populates table with matched processes.

        Covers lines 578-621: the full process table logic including
        matching "python.exe" → "Python" and populating rows.

        Note: ``Process`` is set directly on ``mock_psutil`` (not via
        ``patch("psutil.Process")``) because ``import psutil`` inside the
        method returns the mock from ``sys.modules``, bypassing the real
        module-level patch.
        """
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        # Mock process_iter to return two processes
        proc1 = MagicMock()
        proc1.info = {"name": "python.exe", "pid": 1234}
        proc2 = MagicMock()
        proc2.info = {"name": "explorer.exe", "pid": 5678}
        mock_psutil.process_iter.return_value = [proc1, proc2]
        mock_proc1 = MagicMock()
        mock_proc1.cpu_percent.return_value = 12.5
        mock_proc1.memory_info.return_value.rss = 200 * 1024 * 1024
        # Set Process directly on mock_psutil since import psutil returns
        # mock_psutil from sys.modules, bypassing real module-level patches.
        mock_psutil.Process = MagicMock(return_value=mock_proc1)
        with patch.dict("sys.modules", {"psutil": mock_psutil}):
            obj._update_process_table()
        self.assertEqual(obj.health_process_table.rowCount(), 1)
        self.assertEqual(obj.health_process_table.item(0, 0).text(), "Python")
        self.assertEqual(obj.health_process_table.item(0, 2).text(), "12%")

    def test_update_events_table_populates_rows(self):
        """_update_events_table shows last 50 events with coloring.

        Covers lines 623-636: the full events table logic including
        error/warning foreground coloring.
        Events are displayed in reverse order (most recent first).
        """
        from datetime import datetime
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidgetItem

        now = datetime.now()
        obj._health_events = [
            (now, "error", "Something broke"),
            (now, "warning", "Disk nearly full"),
            (now, "info", "All good"),
        ]
        obj._update_events_table()
        self.assertEqual(obj.health_events_table.rowCount(), 3)
        # Events are reversed (most recent first)
        self.assertEqual(
            obj.health_events_table.item(0, 2).text(),
            "All good"
        )
        self.assertEqual(
            obj.health_events_table.item(2, 2).text(),
            "Something broke"
        )
        type_item1 = obj.health_events_table.item(0, 1)
        self.assertIsNotNone(type_item1.foreground())
        self.assertEqual(type_item1.text(), "info")

    def test_update_health_mocked_psutil(self):
        from tabs.health_tab import HealthTabMixin
        obj = HealthTabMixin()
        self._mk(obj)
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 45.0
        mock_vmem = MagicMock()
        mock_vmem.percent = 60.0
        mock_vmem.used = 8 * 1024**3
        mock_vmem.total = 16 * 1024**3
        mock_psutil.virtual_memory.return_value = mock_vmem
        mock_disk = MagicMock()
        mock_disk.percent = 30.0
        mock_disk.free = 100 * 1024**3
        mock_psutil.disk_usage.return_value = mock_disk
        mock_psutil.process_iter.return_value = []
        with patch.dict("sys.modules", {"psutil": mock_psutil}), patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="")):
            obj._update_health()
        self.assertIn("45%", obj.health_cpu_label.text())
        self.assertIn("8.0/16.0 GB", obj.health_ram_label.text())

    def test_health_tab_widgets_are_real_qt_objects(self):
        """create_health_tab() builds a QWidget with all expected
        UI elements as real Qt widgets."""
        from PyQt6.QtCore import QObject
        from PyQt6.QtWidgets import QWidget, QProgressBar, QLabel, QTableWidget
        from tabs.health_tab import HealthTabMixin
        class _T(QObject, HealthTabMixin):
            pass
        obj = _T()
        with patch.object(obj, "_update_health"):
            with patch("tabs.health_tab.QTimer", return_value=MagicMock()):
                tab = obj.create_health_tab()
        self.assertIsInstance(tab, QWidget,
            msg="REGRESSION: create_health_tab must return a QWidget")
        self.assertIsInstance(obj.health_cpu_bar, QProgressBar,
            msg="REGRESSION: health_cpu_bar should be a QProgressBar")
        self.assertIsInstance(obj.health_ram_bar, QProgressBar,
            msg="REGRESSION: health_ram_bar should be a QProgressBar")
        self.assertIsInstance(obj.health_uptime_lbl, QLabel,
            msg="REGRESSION: health_uptime_lbl should be a QLabel")
        self.assertIsInstance(obj.health_events_table, QTableWidget,
            msg="REGRESSION: health_events_table should be a QTableWidget")
        self.assertIsInstance(obj.health_process_table, QTableWidget,
            msg="REGRESSION: health_process_table should be a QTableWidget")
        self.assertIsInstance(obj.health_provider_status, QLabel,
            msg="REGRESSION: health_provider_status should be a QLabel")

if __name__ == "__main__":
    unittest.main()
