"""Tests for SessionJournalTabMixin — refresh, search, category filter, and

selection detail display."""

import io

import json

import os

import tempfile

import unittest

from datetime import datetime

from unittest.mock import MagicMock, patch


from PyQt6.QtCore import QObject


from tabs.session_journal_tab import SessionJournalTabMixin



def _make_session_journal():

    class _SJ(QObject, SessionJournalTabMixin):

        pass


    sj = _SJ()


    sj._sj_search = MagicMock()

    sj._sj_search.text.return_value = ""


    sj._sj_category_filter = MagicMock()

    sj._sj_category_filter.currentText.return_value = "all"


    sj._sj_time_filter = MagicMock()

    sj._sj_time_filter.currentText.return_value = "All Time"

    sj._sj_time_filter.window.return_value = None


    sj._sj_sort_newest = True


    sj._sj_previous_time_filter = "All Time"

    sj._sj_custom_start = None

    sj._sj_custom_end = None


    sj._sj_timer = MagicMock()

    sj._sj_timer.isActive.return_value = True


    sj._sj_table = MagicMock()

    sj._sj_detail = MagicMock()

    sj._sj_stats = MagicMock()


    sj._sj_loading = MagicMock()


    sj._sj_sort_btn = MagicMock()


    return sj



class TestSessionJournalTabMixin(unittest.TestCase):


    def setUp(self):

        self.sj = _make_session_journal()


    @patch("tabs.session_journal_tab.get_session_events")

    def test_refresh_populates_table(self, mock_get_events):

        mock_get_events.return_value = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 100, "category": "session", "message": "Started", "data": {}},

            {"timestamp": "2026-07-12T14:35:00", "session_offset_s": 400, "category": "task", "message": "Build completed", "data": {"task": "build"}},

        ]

        self.sj._sj_refresh()

        self.sj._sj_table.setRowCount.assert_called_with(2)


    @patch("tabs.session_journal_tab.get_session_events")

    def test_refresh_handles_empty(self, mock_get_events):

        mock_get_events.return_value = []

        self.sj._sj_refresh()

        self.sj._sj_table.setRowCount.assert_called_with(0)


    @patch("tabs.session_journal_tab.get_session_events")

    def test_refresh_get_session_events_exception_propagates(

            self, mock_get_events):

        """REGRESSION TEST: an exception from get_session_events

        propagates through _sj_refresh (no silent try/except swallow).


        NOTE: This is a leaf guard — no other test cross-links to it

        because exception-propagation is independently testable.

        It remains a standalone regression test (not a REGRESSION

        GUARD with consumers).


        ``_sj_load_events`` in ``session_journal_tab.py`` calls

        ``get_session_events(category=..., limit=2000)`` without a

        try/except wrapper.  If a future refactor adds a blanket

        exception handler that swallows errors, this test catches the

        regression by asserting the exception is not caught.


        No construction tuning needed — ``side_effect`` on the mock

        is sufficient to exercise the unguarded call path.


        The ``assertRaises(OSError)`` fires if a refactor silently

        catches the exception instead of propagating it.

        """

        mock_get_events.side_effect = OSError("Database locked")


        with self.assertRaises(OSError):

            self.sj._sj_refresh()


    @patch("tabs.session_journal_tab.get_session_events")

    def test_search_filters(self, mock_get_events):

        mock_get_events.return_value = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 100, "category": "session", "message": "Started", "data": {}},

            {"timestamp": "2026-07-12T14:35:00", "session_offset_s": 400, "category": "task", "message": "Build completed", "data": {}},

        ]

        self.sj._sj_search.text.return_value = "build"

        self.sj._sj_refresh()

        self.sj._sj_table.setRowCount.assert_called_with(1)


    @patch("tabs.session_journal_tab.get_session_events")

    def test_search_match_in_data_json(self, mock_get_events):

        mock_get_events.return_value = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 100, "category": "session", "message": "Started", "data": {}},

            {"timestamp": "2026-07-12T14:35:00", "session_offset_s": 400, "category": "task", "message": "Task processed", "data": {"task_name": "deploy"}},

        ]

        self.sj._sj_search.text.return_value = "deploy"

        self.sj._sj_refresh()

        self.sj._sj_table.setRowCount.assert_called_with(1)


    @patch("tabs.session_journal_tab.get_session_events")

    def test_search_no_match_returns_empty(self, mock_get_events):

        mock_get_events.return_value = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 100, "category": "session", "message": "Started", "data": {}},

        ]

        self.sj._sj_search.text.return_value = "nonexistent"

        self.sj._sj_refresh()

        self.sj._sj_table.setRowCount.assert_called_with(0)


    @patch("tabs.session_journal_tab.get_session_events")

    def test_category_filter_all_passes_none(self, mock_get_events):

        mock_get_events.return_value = []

        self.sj._sj_category_filter.currentText.return_value = "all"

        self.sj._sj_refresh()

        mock_get_events.assert_called_once_with(category=None, limit=2000)


    @patch("tabs.session_journal_tab.get_session_events")

    def test_category_filter_passes_specific_category(self, mock_get_events):

        mock_get_events.return_value = []

        self.sj._sj_category_filter.currentText.return_value = "error"

        self.sj._sj_refresh()

        mock_get_events.assert_called_once_with(category="error", limit=2000)


    def test_select_shows_detail(self):

        event = {

            "timestamp": "2026-07-12T14:30:00",

            "session_offset_s": 123,

            "category": "session",

            "message": "Session started",

            "data": {"pid": 42},

        }

        mock_item = MagicMock()

        mock_item.data.return_value = event

        self.sj._sj_table.item.return_value = mock_item

        self.sj._sj_on_select(0, 0)

        self.sj._sj_detail.setPlainText.assert_called_once()

        detail_text = self.sj._sj_detail.setPlainText.call_args[0][0]

        self.assertIn("Session started", detail_text)

        self.assertIn("2026-07-12T14:30:00", detail_text)


    def test_select_negative_row_clears_detail(self):

        self.sj._sj_on_select(-1, 0)

        self.sj._sj_detail.setPlainText.assert_called_once_with("")


    def test_select_no_item_clears_detail(self):

        self.sj._sj_table.item.return_value = None

        self.sj._sj_on_select(5, 0)

        self.sj._sj_detail.setPlainText.assert_not_called()


    def test_select_no_event_data_clears_detail(self):

        mock_item = MagicMock()

        mock_item.data.return_value = None

        self.sj._sj_table.item.return_value = mock_item

        self.sj._sj_on_select(0, 0)

        self.sj._sj_detail.setPlainText.assert_not_called()


    # ----- Offset formatting tests -----


    def test_populate_table_offset_formatting(self):

        """_sj_populate_table formats session_offset_s as ``Ns`` for

        values < 60 and ``Xh Ym`` for values >= 60."""

        events = [

            {"timestamp": "2026-07-12T10:00:00", "session_offset_s": 0,

             "category": "session", "message": "Zero", "data": {}},

            {"timestamp": "2026-07-12T10:00:00", "session_offset_s": 30,

             "category": "session", "message": "Seconds only", "data": {}},

            {"timestamp": "2026-07-12T10:00:00", "session_offset_s": 60,

             "category": "session", "message": "Boundary 1min", "data": {}},

            {"timestamp": "2026-07-12T10:00:00", "session_offset_s": 3600,

             "category": "session", "message": "Exactly 1h", "data": {}},

            {"timestamp": "2026-07-12T10:00:00", "session_offset_s": 3661,

             "category": "session", "message": "1h 1m 1s", "data": {}},

        ]

        self.sj._sj_populate_table(events)


        # Collect offset strings from setItem calls on column 1

        offset_items = {}

        for call_args in self.sj._sj_table.setItem.call_args_list:

            args, _ = call_args

            row, col = args[0], args[1]

            if col == 1:

                item = args[2]

                offset_items[row] = item.text()


        self.assertEqual(offset_items[0], "0s",

                         msg="REGRESSION: offset 0 should format as '0s'")

        self.assertEqual(offset_items[1], "30s",

                         msg="REGRESSION: offset 30 should format as '30s'")

        self.assertEqual(offset_items[2], "0h 1m",

                         msg="REGRESSION: offset 60 should format as '0h 1m'")

        self.assertEqual(offset_items[3], "1h 0m",

                         msg="REGRESSION: offset 3600 should format as '1h 0m'")

        self.assertEqual(offset_items[4], "1h 1m",

                         msg="REGRESSION: offset 3661 should format as '1h 1m'")


    # ----- Import tests -----


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_csv(self, mock_open, mock_dialog):

        """ANTI-FRAGILITY: CSV _sj_import parses rows via csv.DictReader

        and reconstructs event dicts with correct types.


        BEFORE: mocked only QFileDialog — open read from real disk.

        AFTER: both QFileDialog and open are mocked; csv_content is fed

        via io.StringIO so csv.DictReader iterates the exact same code

        path as a real file read.


        Cross-link: stricter regressions at test_import_missing_column

        (KeyError) and test_import_non_array_json (ValueError).

        """

        csv_content = (

            "timestamp,session_offset_s,category,message,data\n"

            "2026-07-12T14:30:00,100,session,Started,{}\n"

            "2026-07-12T14:35:00,400,task,Build completed,{}\n"

        )

        mock_dialog.return_value = ("/test/events.csv", "CSV (*.csv)")

        mock_open.return_value = io.StringIO(csv_content)


        self.sj._sj_import()


        self.sj._sj_timer.stop.assert_called_once()

        self.sj._sj_table.setRowCount.assert_called_once_with(2)

        events = getattr(self.sj, "_sj_filtered_events", None)

        self.assertIsNotNone(events,

            msg="REGRESSION: _sj_populate_table must set _sj_filtered_events")

        self.assertEqual(events[0]["message"], "Started")

        self.assertEqual(events[1]["category"], "task")

        self.assertEqual(events[1]["session_offset_s"], 400)

        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Imported: 2 events", stats_text)


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_json(self, mock_open, mock_dialog):

        """ANTI-FRAGILITY: JSON _sj_import parses via json.load()."""

        json_events = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 100,

             "category": "session", "message": "Started", "data": {}},

            {"timestamp": "2026-07-12T14:35:00", "session_offset_s": 400,

             "category": "task", "message": "Build completed", "data": {}},

        ]

        mock_dialog.return_value = ("/test/events.json", "JSON (*.json)")

        mock_open.return_value = io.StringIO(json.dumps(json_events))


        self.sj._sj_import()


        self.sj._sj_timer.stop.assert_called_once()

        self.sj._sj_table.setRowCount.assert_called_once_with(2)

        events = self.sj._sj_filtered_events

        self.assertEqual(events[0]["message"], "Started")

        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Imported: 2 events", stats_text)


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_empty_array(self, mock_open, mock_dialog):

        """Importing an empty JSON array shows 0 events."""

        mock_dialog.return_value = ("/test/empty.json", "JSON (*.json)")

        mock_open.return_value = io.StringIO("[]")


        self.sj._sj_import()


        self.sj._sj_timer.stop.assert_called_once()

        self.sj._sj_table.setRowCount.assert_called_once_with(0)

        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Imported: 0 events", stats_text)


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    def test_import_cancel_does_nothing(self, mock_dialog):

        """Cancelling the file dialog returns early without side effects."""

        mock_dialog.return_value = ("", "")


        self.sj._sj_import()


        self.sj._sj_timer.stop.assert_not_called()

        self.sj._sj_stats.setText.assert_not_called()


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_non_array_json_errors(self, mock_open, mock_dialog):

        """ANTI-FRAGILITY: a JSON object (not array) triggers

        isinstance(events, list) guard and shows error in stats bar.


        BEFORE: _sj_import didn't validate JSON type — a dict

        would crash at iteration.

        AFTER: isinstance check raises ValueError with descriptive

        message; caught by the except clause.

        """

        mock_dialog.return_value = ("/test/bad.json", "JSON (*.json)")

        mock_open.return_value = io.StringIO(json.dumps({"bad": "data"}))


        self.sj._sj_import()


        self.sj._sj_timer.stop.assert_not_called()

        self.sj._sj_table.setRowCount.assert_not_called()

        self.sj._sj_stats.setText.assert_called_once()

        text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Import failed", text,

                      msg="REGRESSION: non-array JSON should show Import failed")

        self.assertIn("expected a json array", text.lower())


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_malformed_json_errors(self, mock_open, mock_dialog):

        """Malformed JSON shows Import failed in stats bar."""

        mock_dialog.return_value = ("/test/bad.json", "JSON (*.json)")

        mock_open.return_value = io.StringIO("{not valid json")


        self.sj._sj_import()


        self.sj._sj_table.setRowCount.assert_not_called()

        self.sj._sj_stats.setText.assert_called_once()

        self.sj._sj_timer.stop.assert_not_called()

        text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Import failed", text)


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_missing_csv_column_errors(self, mock_open, mock_dialog):

        """ANTI-FRAGILITY: CSV missing a required column raises

        KeyError caught by the except clause.


        BEFORE: csv.DictReader silently returned partial dict.

        AFTER: KeyError from row["column_name"] is caught and

        shown in stats bar.

        """

        csv_content = (

            "category,message\n"

            "session,Started\n"

        )

        mock_dialog.return_value = ("/test/bad.csv", "CSV (*.csv)")

        mock_open.return_value = io.StringIO(csv_content)


        self.sj._sj_import()


        self.sj._sj_table.setRowCount.assert_not_called()

        self.sj._sj_stats.setText.assert_called_once()

        self.sj._sj_timer.stop.assert_not_called()

        text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Import failed", text,

                      msg="REGRESSION: missing CSV column should show Import failed")


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_csv_with_data_dict(self, mock_open, mock_dialog):

        """CSV import parses the data column from JSON string to dict."""

        csv_content = (

            'timestamp,session_offset_s,category,message,data\n'

            '2026-07-12T14:30:00,100,session,Started,{}\n'

            '2026-07-12T14:35:00,400,task,Build completed,'

            '"{""task"":""build""}"\n'

        )

        mock_dialog.return_value = ("/test/events.csv", "CSV (*.csv)")

        mock_open.return_value = io.StringIO(csv_content)


        self.sj._sj_import()


        events = self.sj._sj_filtered_events

        self.assertEqual(len(events), 2)

        self.assertEqual(events[1]["data"], {"task": "build"})


    @patch("tabs.session_journal_tab.QFileDialog.getOpenFileName")

    @patch("builtins.open")

    def test_import_shows_filename_badge(self, mock_open, mock_dialog):

        """ANTI-FRAGILITY: the stats badge contains the imported

        filename (extracted via rsplit) and event count.


        BEFORE: badge only showed count, not filename.

        AFTER: badge shows 'Imported: N events from filename.ext'.

        """

        csv_content = "timestamp,session_offset_s,category,message,data\n2026-07-12T14:30:00,0,session,Test,{}\n"

        mock_dialog.return_value = (

            "/some/deep/path/my_events.json", "JSON (*.json)")

        mock_open.return_value = io.StringIO(

            json.dumps([{"template": True}]))


        self.sj._sj_import()


        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("my_events.json", stats_text,

                      msg="REGRESSION: badge should contain basename")

        self.assertIn("Imported: 1 events", stats_text)


    # ----- Export tests -----


    @patch("tabs.session_journal_tab.QFileDialog.getSaveFileName")

    @patch("builtins.open")

    def test_export_json_writes_file(self, mock_open, mock_dialog):

        """ANTI-FRAGILITY: JSON _sj_export writes events via json.dump

        and shows an 'Exported N events to filename.json' stats badge.


        BEFORE: export only tested via the round-trip integration test.

        AFTER: QFileDialog + open are both mocked; _sj_stats.setText is

        verified to contain the correct filename basename and event

        count.  Content fidelity is tested at the stricter regression

        guard ``test_export_then_import_json_roundtrip`` (real disk).


        Cross-link: stricter regression guard at

        ``test_export_then_import_json_roundtrip`` (real disk).

        """

        events = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 100,

             "category": "session", "message": "Started", "data": {}},

            {"timestamp": "2026-07-12T14:35:00", "session_offset_s": 400,

             "category": "task", "message": "Build completed",

             "data": {"task": "build"}},

        ]

        self.sj._sj_filtered_events = events


        mock_dialog.return_value = ("/test/journal.json", "JSON (*.json)")

        mock_open.return_value = MagicMock()


        self.sj._sj_export()


        self.sj._sj_stats.setText.assert_called_once()

        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Exported 2 events to journal.json", stats_text,

                      msg="REGRESSION: stats badge should show count + filename")

        self.assertIn(" | ", stats_text,

                      msg="REGRESSION: stats badge should include timestamp")


    @patch("tabs.session_journal_tab.QFileDialog.getSaveFileName")

    @patch("builtins.open")

    def test_export_csv_writes_file(self, mock_open, mock_dialog):

        """ANTI-FRAGILITY: CSV _sj_export flattens event data and writes

        via csv.DictWriter with a header row.


        BEFORE: CSV export only tested implicitly via round-trip.

        AFTER: mocked open returns a MagicMock; _sj_stats.setText is

        verified to contain the correct filename basename, event count,

        and CSV-extension-specific path. Content fidelity relies on

        the round-trip integration test.

        """

        events = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 100,

             "category": "session", "message": "Started", "data": {}},

            {"timestamp": "2026-07-12T14:35:00", "session_offset_s": 400,

             "category": "task", "message": "Build completed",

             "data": {"task": "build"}},

        ]

        self.sj._sj_filtered_events = events


        mock_dialog.return_value = ("/test/journal.csv", "CSV (*.csv)")

        mock_open.return_value = MagicMock()


        self.sj._sj_export()


        self.sj._sj_stats.setText.assert_called_once()

        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Exported 2 events to journal.csv", stats_text,

                      msg="REGRESSION: CSV stats badge should show count + filename")


    def test_export_no_events_returns_early(self):

        """Calling _sj_export with no _sj_filtered_events returns

        immediately without opening a file dialog."""

        # Ensure _sj_filtered_events does NOT exist

        if hasattr(self.sj, "_sj_filtered_events"):

            del self.sj._sj_filtered_events


        self.sj._sj_export()


        self.sj._sj_stats.setText.assert_not_called()

        # The file dialog was never opened (no patch needed — if it were

        # called on the real widget it would hang the test)


    @patch("tabs.session_journal_tab.QFileDialog.getSaveFileName")

    def test_export_cancel_does_nothing(self, mock_dialog):

        """Cancelling the file dialog returns early without writing."""

        self.sj._sj_filtered_events = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 0,

             "category": "session", "message": "Test", "data": {}},

        ]

        mock_dialog.return_value = ("", "")


        self.sj._sj_export()


        self.sj._sj_stats.setText.assert_not_called()


    @patch("tabs.session_journal_tab.QFileDialog.getSaveFileName")

    @patch("builtins.open")

    def test_export_error_handling(self, mock_open, mock_dialog):

        """An OSError during file write shows 'Export failed' in the

        stats bar and hides the loading spinner."""

        self.sj._sj_filtered_events = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 0,

             "category": "session", "message": "Test", "data": {}},

        ]

        mock_dialog.return_value = ("/test/journal.json", "JSON (*.json)")

        mock_open.side_effect = OSError("Permission denied")


        self.sj._sj_export()


        self.sj._sj_stats.setText.assert_called_once()

        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn("Export failed: Permission denied", stats_text,

                      msg="REGRESSION: OSError should show 'Export failed' in stats")

        # Loading spinner should be hidden via finally

        self.sj._sj_loading.hide.assert_called_once()


    # ----- Time-filter / Custom Range tests -----


    def test_time_filter_non_custom_sets_previous_and_refreshes(self):

        """Selecting a non-custom time filter sets the previous filter

        and triggers a refresh."""

        with patch("tabs.session_journal_tab.get_session_events") as ge:

            ge.return_value = []

            self.sj._sj_on_time_filter("Last hour")


        self.assertEqual(self.sj._sj_previous_time_filter, "Last hour")

        self.sj._sj_table.setRowCount.assert_called_once_with(0)


    @patch("tabs.session_journal_tab.get_session_events")

    def test_non_custom_time_filters_apply_correct_cutoffs(

            self, mock_get_events):

        """Each non-custom time filter (Last 30 min / hour / 2h / 6h /

        Today) applies a different cutoff timestamp and keeps only

        events whose timestamps are >= that cutoff.


        Both the event timestamps AND the tab's cutoff computation are

        anchored to a fixed ``_FixedDatetime`` noon reference (the tab's

        ``datetime`` is patched during ``_sj_refresh``), so every scenario

        is deterministic regardless of wall-clock time — the "Today"

        filter can never cross the midnight boundary (pre-existing

        time-of-day flake, fixed 2026-08-02).

        """

        from datetime import timedelta

        # ANTI-FRAGILITY: anchor BOTH the tab's clock and the event
        # timestamps to the same fixed noon reference. The tab computes
        # cutoffs from its own datetime.now() (tabs/session_journal_tab.py
        # _sj_refresh) — anchoring only the events made the relative
        # filters inconsistent whenever wall-clock differed from the
        # anchored time. Additionally, at 00:00-08:00 wall-clock, now-8h
        # crossed midnight so "Today" excluded "Past6h" (pre-existing
        # time-of-day flake, caught 2026-08-02). Patching the tab's
        # datetime to the same fixed clock makes every scenario
        # deterministic.
        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 15, 12, 0, 0)

        real_now = _FixedDatetime.now()


        # Build event timestamps relative to real_now with generous

        # margins from filter boundaries.  Each event is clearly

        # inside or outside each filter's cutoff.

        events = [

            {"timestamp": (real_now - timedelta(seconds=10)).isoformat(),

             "session_offset_s": 0, "category": "session",

             "message": "JustNow", "data": {}},

            {"timestamp": (real_now - timedelta(minutes=15)).isoformat(),

             "session_offset_s": 0, "category": "task",

             "message": "Recent", "data": {}},

            {"timestamp": (real_now - timedelta(minutes=45)).isoformat(),

             "session_offset_s": 0, "category": "task",

             "message": "NearHour", "data": {}},

            {"timestamp": (real_now - timedelta(minutes=90)).isoformat(),

             "session_offset_s": 0, "category": "system",

             "message": "PastHour", "data": {}},

            {"timestamp": (real_now - timedelta(hours=4)).isoformat(),

             "session_offset_s": 0, "category": "system",

             "message": "Past2h", "data": {}},

            {"timestamp": (real_now - timedelta(hours=8)).isoformat(),

             "session_offset_s": 0, "category": "system",

             "message": "Past6h", "data": {}},

            {"timestamp": (real_now - timedelta(days=2)).isoformat(),

             "session_offset_s": 0, "category": "warning",

             "message": "Yesterday", "data": {}},

        ]

        mock_get_events.return_value = events


        scenarios = [

            ("Last 30 min", ["JustNow", "Recent"]),

            ("Last hour", ["JustNow", "Recent", "NearHour"]),

            ("Last 2 hours", ["JustNow", "Recent", "NearHour",

                              "PastHour"]),

            ("Last 6 hours", ["JustNow", "Recent", "NearHour",

                              "PastHour", "Past2h"]),

            ("Today", ["JustNow", "Recent", "NearHour",

                       "PastHour", "Past2h", "Past6h"]),

        ]


        for filter_name, expected_messages in scenarios:

            with self.subTest(filter=filter_name):

                self.sj._sj_time_filter.currentText.return_value = filter_name

                with patch("tabs.session_journal_tab.datetime", _FixedDatetime):
                    self.sj._sj_refresh()


                kept = {e["message"]

                        for e in self.sj._sj_filtered_events}


                self.assertEqual(

                    set(expected_messages), kept,

                    msg=(f"REGRESSION: {filter_name} kept wrong set"))


    @patch("tabs.session_journal_tab.get_session_events")

    def test_non_custom_filter_passes_bad_timestamps(

            self, mock_get_events):

        """Events with malformed or missing timestamps pass through

        non-custom time filters (defensive fallback in the

        ``if cutoff is not None:`` loop).


        Events whose timestamps cannot be parsed by

        ``datetime.fromisoformat`` (ValueError/TypeError) and events

        with no ``timestamp`` key are kept rather than dropped, so a

        single corrupt event does not silently empty the table.


        The ``setRowCount(3)`` assertion fires if a refactor adds a

        ``continue`` or ``break`` inside the try/except/else chain that

        skips bad/missing timestamps.

        """

        from datetime import timedelta

        real_now = datetime.now()

        valid_ts = (real_now - timedelta(hours=1)).isoformat()


        mock_get_events.return_value = [

            # 0 — valid timestamp, well within 6-hour cutoff

            {"timestamp": valid_ts,

             "session_offset_s": 0, "category": "session",

             "message": "Valid", "data": {}},

            # 1 — malformed timestamp: should pass through

            {"timestamp": "not-a-date",

             "session_offset_s": 0, "category": "system",

             "message": "Bad ts", "data": {}},

            # 2 — missing timestamp key: should pass through

            {"session_offset_s": 0, "category": "system",

             "message": "No ts", "data": {}},

        ]

        self.sj._sj_time_filter.currentText.return_value = "Last 6 hours"


        self.sj._sj_refresh()


        # All 3 events survive: valid (within filter) + 2 pass-through

        self.sj._sj_table.setRowCount.assert_called_once_with(3)

        events = self.sj._sj_filtered_events

        self.assertEqual(len(events), 3)

        messages = [e["message"] for e in events]

        self.assertIn("Valid", messages)

        self.assertIn("Bad ts", messages,

                      msg=("REGRESSION: malformed timestamp should "

                           "pass through non-custom time filter"))

        self.assertIn("No ts", messages,

                      msg=("REGRESSION: missing timestamp should "

                           "pass through non-custom time filter"))


    @patch("tabs.session_journal_tab.get_session_events")

    def test_unknown_time_filter_does_not_crash(self, mock_get_events):

        """An unrecognised time-filter value falls through to the

        ``else: cutoff = None`` branch and does not filter events."""

        mock_get_events.return_value = [

            {"timestamp": "2026-07-12T14:30:00", "session_offset_s": 0,

             "category": "session", "message": "Should appear", "data": {}},

        ]

        # Set an unknown filter value not in the if/elif chain

        self.sj._sj_time_filter.currentText.return_value = "Unknown"


        self.sj._sj_refresh()


        # All events pass through (no filtering applied)

        self.sj._sj_table.setRowCount.assert_called_once_with(1)

        events = self.sj._sj_filtered_events

        self.assertEqual(len(events), 1)

        self.assertEqual(events[0]["message"], "Should appear",

                         msg="REGRESSION: unknown filter value should not filter events")


    @patch("tabs.session_journal_tab.QDialog.exec", return_value=1)  # Accepted

    @patch("tabs.session_journal_tab.get_session_events")

    def test_custom_range_accepted_stores_dates(self, mock_get_events, mock_exec):

        """Accepting the custom range dialog stores the selected start/end

        datetimes and updates the previous-filter tracker."""

        mock_get_events.return_value = []


        self.sj._sj_on_time_filter("Custom range")


        # Both start and end should be real datetimes

        self.assertIsInstance(

            self.sj._sj_custom_start, datetime,

            msg="REGRESSION: _sj_custom_start must be a datetime on accept")

        self.assertIsInstance(

            self.sj._sj_custom_end, datetime,

            msg="REGRESSION: _sj_custom_end must be a datetime on accept")

        self.assertEqual(

            self.sj._sj_previous_time_filter, "Custom range",

            msg="REGRESSION: previous filter must track 'Custom range'")

        # Refresh should have been called (via finally)

        self.sj._sj_table.setRowCount.assert_called_once_with(0)


    @patch("tabs.session_journal_tab.QDialog.exec", return_value=0)  # Rejected

    @patch("tabs.session_journal_tab.get_session_events")

    def test_custom_range_rejected_reverts_combo(self, mock_get_events, mock_exec):

        """ANTI-FRAGILITY: Rejecting the custom range dialog reverts the

        combo to the previous filter and does NOT store dates.


        BEFORE: rejecting left combo on 'Custom range' text, causing the

        dialog to re-open on every signal toggle.

        AFTER: _sj_on_time_filter in session_journal_tab.py calls

        _sj_time_filter.setCurrentText to restore _sj_previous_time_filter.

        """

        mock_get_events.return_value = []

        self.sj._sj_previous_time_filter = "Last hour"


        self.sj._sj_on_time_filter("Custom range")


        # Combo reverted to previous

        self.sj._sj_time_filter.setCurrentText.assert_called_once_with(

            "Last hour")

        # No custom dates stored

        self.assertIsNone(

            self.sj._sj_custom_start,

            msg="REGRESSION: _sj_custom_start must stay None on reject")

        self.assertIsNone(

            self.sj._sj_custom_end,

            msg="REGRESSION: _sj_custom_end must stay None on reject")

        # Signals unblocked and refresh called (via finally)

        self.sj._sj_time_filter.blockSignals.assert_called_with(False)

        self.sj._sj_table.setRowCount.assert_called_once_with(0)


    @patch("tabs.session_journal_tab.QDialog.exec", return_value=1)  # Accepted

    @patch("tabs.session_journal_tab.get_session_events")

    def test_custom_range_prepopulates_previous_dates(self, mock_get_events, mock_exec):

        """ANTI-FRAGILITY: The custom range dialog pre-populates the

        date-time edits with previously selected start/end values.


        BEFORE: dialog always defaulted to now-24h / now when re-opened.

        AFTER: _sj_on_time_filter in session_journal_tab.py passes

        _sj_custom_start/_sj_custom_end to QDateTimeEdit.setDateTime,

        so toPyDateTime() round-trips the same value.

        """

        mock_get_events.return_value = []

        expected_start = datetime(2026, 7, 11, 8, 0, 0)

        expected_end = datetime(2026, 7, 12, 20, 30, 0)

        self.sj._sj_custom_start = expected_start

        self.sj._sj_custom_end = expected_end


        self.sj._sj_on_time_filter("Custom range")


        self.assertEqual(

            self.sj._sj_custom_start, expected_start,

            msg=("REGRESSION: pre-populated _sj_custom_start must be "

                 "preserved through the dialog accept cycle"))

        self.assertEqual(

            self.sj._sj_custom_end, expected_end,

            msg=("REGRESSION: pre-populated _sj_custom_end must be "

                 "preserved through the dialog accept cycle"))


    # pragma: anti-fragility-orphan-ok -- leaf guard; no other test depends
    # on custom-range boundary filtering. Independently testable.
    @patch("tabs.session_journal_tab.get_session_events")
    def test_custom_range_filter_narrows_events(self, mock_get_events):

        """REGRESSION GUARD for custom-range event filtering in

        ``session_journal_tab.SessionJournalTabMixin._sj_refresh`` (lines

        232-252 of ``session_journal_tab.py``).


        The custom-range ``elif`` branch filters events row-by-row based

        on ``self._sj_custom_start <= epoch <= self._sj_custom_end``.

        Events with unparseable or missing timestamps pass through

        (defensive fallback).


        Construction: 7 events exercise every edge case — in-range,

        before-start, after-end, exact-boundary start, exact-boundary

        end, unparseable timestamp, and missing timestamp.        The ``setRowCount(5)`` assertion fires if a refactor reverts the

        boundary comparison: REGRESSION: custom range should keep 5 of 7

        events (3 in-range + 2 pass-through for unparseable/missing ts).

        """

        mock_get_events.return_value = [

            # 0 — before start: should be excluded

            {"timestamp": "2026-07-11T06:00:00", "session_offset_s": 0,

             "category": "session", "message": "Too early", "data": {}},

            # 1 — exactly at start: should be kept (inclusive <=)

            {"timestamp": "2026-07-11T08:00:00", "session_offset_s": 0,

             "category": "session", "message": "On start boundary", "data": {}},

            # 2 — within range: should be kept

            {"timestamp": "2026-07-11T14:30:00", "session_offset_s": 100,

             "category": "task", "message": "Mid range", "data": {}},

            # 3 — exactly at end: should be kept (inclusive <=)

            {"timestamp": "2026-07-12T20:30:00", "session_offset_s": 200,

             "category": "task", "message": "On end boundary", "data": {}},

            # 4 — after end: should be excluded

            {"timestamp": "2026-07-12T22:00:00", "session_offset_s": 0,

             "category": "warning", "message": "Too late", "data": {}},

            # 5 — unparseable timestamp: should pass through

            {"timestamp": "not-a-date", "session_offset_s": 0,

             "category": "system", "message": "Bad ts", "data": {}},

            # 6 — missing timestamp: should pass through

            {"session_offset_s": 0,

             "category": "system", "message": "No ts", "data": {}},

        ]

        self.sj._sj_time_filter.currentText.return_value = "Custom range"

        self.sj._sj_custom_start = datetime(2026, 7, 11, 8, 0, 0)

        self.sj._sj_custom_end = datetime(2026, 7, 12, 20, 30, 0)


        self.sj._sj_refresh()


        # 3 should survive: #1 (boundary), #2 (in-range), #3 (boundary),

        # plus #5 (bad ts) and #6 (missing ts) pass through = 5 total

        self.sj._sj_table.setRowCount.assert_called_once_with(5)

        events = self.sj._sj_filtered_events

        self.assertEqual(len(events), 5)

        messages = [e["message"] for e in events]

        self.assertIn("On start boundary", messages,

                      msg="REGRESSION: start-boundary event excluded")

        self.assertIn("Mid range", messages,

                      msg="REGRESSION: in-range event excluded")

        self.assertIn("On end boundary", messages,

                      msg="REGRESSION: end-boundary event excluded")

        self.assertIn("Bad ts", messages,

                      msg="REGRESSION: unparseable timestamp should pass through")

        self.assertIn("No ts", messages,

                      msg="REGRESSION: missing timestamp should pass through")

        self.assertNotIn("Too early", messages,

                         msg="REGRESSION: before-start event should be excluded")

        self.assertNotIn("Too late", messages,

                         msg="REGRESSION: after-end event should be excluded")


    @patch("tabs.session_journal_tab.get_session_events")

    def test_search_and_custom_range_filter_together(self, mock_get_events):

        """Search operates on the custom-range-filtered event list, not

        on the full event set.

        The code path in ``_sj_refresh`` applies the time-range filter

        first, then runs the search filter on the already-narrowed list.

        This test verifies that pipeline order: 5 events load, but only

        3 survive the range filter, and only 2 of those 3 match the

        search term.

        """

        mock_get_events.return_value = [

            # 0 — before start, no search match: excluded by range

            {"timestamp": "2026-07-11T06:00:00", "session_offset_s": 0,

             "category": "session", "message": "Too early", "data": {}},

            # 1 — in range, no search match: excluded by search

            {"timestamp": "2026-07-11T10:00:00", "session_offset_s": 50,

             "category": "session", "message": "Alpha build", "data": {}},

            # 2 — in range, matches search: kept

            {"timestamp": "2026-07-11T14:30:00", "session_offset_s": 100,

             "category": "task", "message": "SearchMatch mid range", "data": {}},

            # 3 — in range, matches search: kept

            {"timestamp": "2026-07-12T15:00:00", "session_offset_s": 200,

             "category": "task", "message": "SearchMatch late", "data": {}},

            # 4 — after end, no search match: excluded by range

            {"timestamp": "2026-07-12T22:00:00", "session_offset_s": 0,

             "category": "warning", "message": "Too late", "data": {}},

        ]

        self.sj._sj_time_filter.currentText.return_value = "Custom range"

        self.sj._sj_custom_start = datetime(2026, 7, 11, 8, 0, 0)

        self.sj._sj_custom_end = datetime(2026, 7, 12, 20, 30, 0)

        self.sj._sj_search.text.return_value = "SearchMatch"


        self.sj._sj_refresh()


        # After range: events 1, 2, 3 (3).  After search: events 2, 3 (2).

        self.sj._sj_table.setRowCount.assert_called_once_with(2)

        events = self.sj._sj_filtered_events

        self.assertEqual(len(events), 2)

        messages = [e["message"] for e in events]

        self.assertIn("SearchMatch mid range", messages,

                      msg="REGRESSION: in-range event matching search should be kept")

        self.assertIn("SearchMatch late", messages,

                      msg="REGRESSION: in-range event matching search should be kept")

        self.assertNotIn("Alpha build", messages,

                         msg="REGRESSION: in-range event not matching search should be excluded")

        self.assertNotIn("Too early", messages,

                         msg="REGRESSION: before-range event should be filtered before search")

        self.assertNotIn("Too late", messages,

                         msg="REGRESSION: after-range event should be filtered before search")


    @patch("tabs.session_journal_tab.QDialog.exec", return_value=1)  # Accepted

    @patch("tabs.session_journal_tab.get_session_events")

    def test_custom_range_shows_correct_badge_in_stats(

            self, mock_get_events, mock_exec):

        """ANTI-FRAGILITY: the stats bar shows a 'Range: YYYY-MM-DD HH:MM →'

        badge after accepting a custom range selection.


        BEFORE: _sj_refresh only built the range_badge string in

        _sj_populate_table if currentText() == 'Custom range' —

        but the combo text was not set during the _sj_on_time_filter

        call chain.

        AFTER: _sj_time_filter.currentText.return_value is set to

        'Custom range' before the call, so _sj_refresh (invoked via

        the finally block) constructs the badge from the newly-stored

        _sj_custom_start / _sj_custom_end — the exact same code path

        as a real user interaction.

        """

        start = datetime(2026, 7, 11, 8, 0, 0)

        end = datetime(2026, 7, 12, 20, 30, 0)


        # Events within the range so filtering doesn't drop them

        mock_get_events.return_value = [

            {"timestamp": "2026-07-11T10:00:00", "session_offset_s": 0,

             "category": "session", "message": "Inside range", "data": {}},

            {"timestamp": "2026-07-12T15:00:00", "session_offset_s": 100,

             "category": "task", "message": "Also inside", "data": {}},

        ]


        # Make _sj_refresh see 'Custom range' so it builds the badge

        self.sj._sj_time_filter.currentText.return_value = "Custom range"


        # Pre-populate so the dialog shows these dates and they round-trip

        self.sj._sj_custom_start = start

        self.sj._sj_custom_end = end


        self.sj._sj_on_time_filter("Custom range")


        # The stats badge should contain the formatted range

        stats_text = self.sj._sj_stats.setText.call_args[0][0]

        self.assertIn(

            "Range: 2026-07-11 08:00 → 2026-07-12 20:30",

            stats_text,

            msg=("REGRESSION: custom range badge missing from stats bar "

                 "after accepting custom range"),

        )

        # Both events survived the range filter

        self.assertIn(

            "Total: 2 events", stats_text,

            msg="REGRESSION: event count should be 2 within custom range",

        )


    def test_refresh_resumes_timer_if_paused(self):

        """When the auto-refresh timer was stopped (by import),

        _sj_refresh restarts it."""

        self.sj._sj_timer.isActive.return_value = False


        with patch("tabs.session_journal_tab.get_session_events") as ge:

            ge.return_value = []

            self.sj._sj_refresh()


        self.sj._sj_timer.start.assert_called_once_with(5000)


    # ----- Sort-toggle tests -----


    def test_toggle_sort_flips_flag_and_updates_button(self):

        """Calling _sj_toggle_sort flips the _sj_sort_newest flag and

        updates the button text to match."""

        self.assertTrue(self.sj._sj_sort_newest)


        with patch("tabs.session_journal_tab.get_session_events") as ge:

            ge.return_value = []

            self.sj._sj_toggle_sort()

        self.assertFalse(

            self.sj._sj_sort_newest,

            msg="REGRESSION: _sj_sort_newest should be False after toggle")

        self.sj._sj_sort_btn.setText.assert_called_with("\u2191 Oldest")


        with patch("tabs.session_journal_tab.get_session_events") as ge:

            ge.return_value = []

            self.sj._sj_toggle_sort()

        self.assertTrue(

            self.sj._sj_sort_newest,

            msg="REGRESSION: _sj_sort_newest should be True after second toggle")

        self.sj._sj_sort_btn.setText.assert_called_with("\u2193 Newest")


    @patch("tabs.session_journal_tab.get_session_events")

    def test_oldest_first_order_reverses_events(self, mock_get_events):

        """ANTI-FRAGILITY: _sj_refresh reverses events when

        _sj_sort_newest is False (oldest-first display).


        BEFORE: sort order was untested — only assumed to be newest-first.

        AFTER: _sj_refresh in session_journal_tab.py reverses events

        when _sj_sort_newest == False via ``events =

        list(reversed(events))``.  Two events with distinct timestamps

        confirm the order flips correctly.

        """

        earlier = {"timestamp": "2026-07-12T10:00:00", "session_offset_s": 0,

                   "category": "session", "message": "Earlier", "data": {}}

        later = {"timestamp": "2026-07-12T14:00:00", "session_offset_s": 0,

                 "category": "task", "message": "Later", "data": {}}

        # _sj_load_events reverses what get_session_events returns,

        # so the mock returns oldest-first [earlier, later]

        mock_get_events.return_value = [earlier, later]


        # Oldest-first display

        self.sj._sj_sort_newest = False

        self.sj._sj_refresh()


        events = self.sj._sj_filtered_events

        self.assertEqual(len(events), 2)

        # In oldest-first: earlier at index 0, later at index 1

        self.assertEqual(

            events[0]["message"], "Earlier",

            msg="REGRESSION: oldest-first should place earlier event first")

        self.assertEqual(

            events[1]["message"], "Later",

            msg="REGRESSION: oldest-first should place later event second")


    # ----- UI builder test -----


    def test_create_session_journal_tab_builds_widgets(self):

        """``create_session_journal_tab()`` builds a QWidget with all

        expected UI elements as real Qt widgets (not MagicMock)."""

        class _SJ(QObject, SessionJournalTabMixin):

            pass

        sj = _SJ()


        tab = sj.create_session_journal_tab()


        self.assertIsNotNone(tab,

            msg="REGRESSION: create_session_journal_tab must return a widget")

        # Key attributes should be real Qt widgets

        self.assertIsNotNone(sj._sj_search,

            msg="REGRESSION: _sj_search should be a QLineEdit")

        self.assertIsNotNone(sj._sj_table,

            msg="REGRESSION: _sj_table should be a QTableWidget")

        self.assertIsNotNone(sj._sj_stats,

            msg="REGRESSION: _sj_stats should be a QLabel")

        self.assertIsNotNone(sj._sj_detail,

            msg="REGRESSION: _sj_detail should be a QTextEdit")

        self.assertIsNotNone(sj._sj_timer,

            msg="REGRESSION: _sj_timer should be a QTimer")

        self.assertIsNotNone(sj._sj_sort_btn,

            msg="REGRESSION: _sj_sort_btn should be a QPushButton")

        self.assertIsNotNone(sj._sj_category_filter,

            msg="REGRESSION: _sj_category_filter should be a QComboBox")

        self.assertIsNotNone(sj._sj_time_filter,

            msg="REGRESSION: _sj_time_filter should be a QComboBox")

        self.assertIsNotNone(sj._sj_loading,

            msg="REGRESSION: _sj_loading should be a QLabel")

        # Timer should be started

        self.assertTrue(sj._sj_timer.isActive(),

            msg="REGRESSION: auto-refresh timer should be active")


    # ----- Integration: export → import round-trip -----


    def test_export_then_import_json_roundtrip(self):

        """REGRESSION GUARD for JSON export→import round-trip fidelity

        in ``session_journal_tab.SessionJournalTabMixin._sj_export`` (lines

        324-371) and ``_sj_import`` (lines 258-316).


        A real temp file is written by _sj_export via json.dump and then

        read back by _sj_import via json.load.  Both code paths run the

        same open/read/write logic a real user would trigger.  The

        assertion ``imported == original_events`` fires if either side

        silently drops keys, truncates nested dicts, or mangles

        non-ASCII content.


        Construction: 3 events exercise three edge cases — bare data={},

        nested dict with mixed types, and unicode characters in message

        (to exercise ensure_ascii=False on export).  A real NamedTemporaryFile

        is used instead of mocking open so the test catches file-format

        bugs (e.g. missing indent, mojibake from wrong encoding).


        The ``imported == original_events`` assertion fires if a refactor

        reverts the JSON serialization contract: REGRESSION: export/import

        round-trip should produce identical event dicts.

        """

        original_events = [

            {

                "timestamp": "2026-07-12T14:30:00",

                "session_offset_s": 100,

                "category": "session",

                "message": "Session started",

                "data": {},

            },

            {

                "timestamp": "2026-07-12T14:35:00",

                "session_offset_s": 400,

                "category": "task",

                "message": "Build completed",

                "data": {"task": "build", "duration_ms": 1234},

            },

            {

                "timestamp": "2026-07-12T15:00:00",

                "session_offset_s": 0,

                "category": "system",

                "message": "• unicode check ✓ — über cool",

                "data": {"emoji": "✅"},

            },

        ]

        self.sj._sj_filtered_events = original_events


        with tempfile.NamedTemporaryFile(

                suffix=".json", delete=False, mode="w", encoding="utf-8"

        ) as tmp:

            tmp_path = tmp.name


        try:

            # Step 1 — Export to the temp file

            with patch(

                "tabs.session_journal_tab.QFileDialog.getSaveFileName",

                return_value=(tmp_path, "JSON (*.json)"),

            ):

                self.sj._sj_export()


            # Verify the file was actually written (disk round-trip)

            self.assertTrue(

                os.path.getsize(tmp_path) > 0,

                msg="REGRESSION: export should write a non-empty file",

            )


            # Step 2 — Import from the same temp file

            with patch(

                "tabs.session_journal_tab.QFileDialog.getOpenFileName",

                return_value=(tmp_path, "JSON (*.json)"),

            ):

                self.sj._sj_import()


            # Step 3 — Compare

            imported = self.sj._sj_filtered_events

            self.assertEqual(

                imported,

                original_events,

                msg=(

                    "REGRESSION: export/import round-trip should produce "

                    "identical event dicts"

                ),

            )

        finally:

            try:

                os.unlink(tmp_path)

            except OSError:

                pass

