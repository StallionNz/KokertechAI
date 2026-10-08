"""Tests for tabs/theme_builder_tab.py -- Theme Builder tab and standalone functions."""
import unittest
from unittest.mock import patch, MagicMock
import json
import os
import tempfile


class TestStandaloneFunctions(unittest.TestCase):
    """Test module-level functions: load/save/get_all_themes, get_color, apply_theme."""

    def setUp(self):
        import importlib
        import tabs.theme_builder_tab as tb
        importlib.reload(tb)
        self.tb = tb

    def test_get_all_themes_includes_builtins(self):
        themes = self.tb.get_all_themes()
        self.assertIn("Classic (Charcoal)", themes)
        self.assertIn("Cyberpunk (Pink/Cyan)", themes)
        self.assertEqual(themes["Classic (Charcoal)"]["bg"], "#121212")

    def test_get_all_themes_has_7_color_slots(self):
        themes = self.tb.get_all_themes()
        for name, colors in themes.items():
            for slot in ["bg", "bg_alt", "bg_tab", "text", "text_tab", "accent1", "accent2"]:
                self.assertIn(slot, colors, f"{name} missing {slot}")

    def test_load_custom_themes_no_file_returns_empty(self):
        import tabs.theme_builder_tab as tb
        importlib = __import__("importlib")
        tb2 = importlib.reload(tb)
        tb2.CUSTOM_THEMES_FILE = "/nonexistent/custom_themes.json"
        result = tb2.load_custom_themes()
        self.assertEqual(result, {})

    def test_load_custom_themes_reads_valid_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"My Theme": {"bg": "#111111", "bg_alt": "#222222", "bg_tab": "#333333", "text": "#EEE", "text_tab": "#999", "accent1": "#F00", "accent2": "#0F0"}}, f)
            tmp_path = f.name
        try:
            import tabs.theme_builder_tab as tb
            importlib = __import__("importlib")
            tb2 = importlib.reload(tb)
            tb2.CUSTOM_THEMES_FILE = tmp_path
            result = tb2.load_custom_themes()
            self.assertIn("My Theme", result)
            self.assertEqual(result["My Theme"]["bg"], "#111111")
        finally:
            os.unlink(tmp_path)

    def test_save_and_load_roundtrip(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{}")
            tmp_path = f.name
        try:
            import tabs.theme_builder_tab as tb
            importlib = __import__("importlib")
            tb2 = importlib.reload(tb)
            tb2.CUSTOM_THEMES_FILE = tmp_path
            themes = {"Roundtrip Theme": {"bg": "#AAA", "bg_alt": "#BBB", "bg_tab": "#CCC", "text": "#DDD", "text_tab": "#EEE", "accent1": "#111", "accent2": "#222"}}
            tb2.save_custom_themes_to_disk(themes)
            loaded = tb2.load_custom_themes()
            self.assertEqual(loaded["Roundtrip Theme"]["bg"], "#AAA")
        finally:
            os.unlink(tmp_path)

    def test_apply_theme_to_widget_sets_stylesheet(self):
        widget = MagicMock()
        colors = {"bg": "#111", "bg_alt": "#222", "bg_tab": "#333", "text": "#EEE", "text_tab": "#999", "accent1": "#F00", "accent2": "#0F0"}
        self.tb.apply_theme_to_widget(widget, colors)
        widget.setStyleSheet.assert_called_once()
        stylesheet = widget.setStyleSheet.call_args[0][0]
        self.assertIn("#111", stylesheet)
        self.assertIn("#F00", stylesheet)

    def test_apply_theme_empty_dict_does_nothing(self):
        widget = MagicMock()
        self.tb.apply_theme_to_widget(widget, {})
        widget.setStyleSheet.assert_not_called()

    def test_apply_theme_none_does_nothing(self):
        widget = MagicMock()
        self.tb.apply_theme_to_widget(widget, None)
        widget.setStyleSheet.assert_not_called()


class TestMixinGetColor(unittest.TestCase):
    """Test _tb_get_color -- hex color retrieval with validation."""

    def setUp(self):
        from unittest.mock import patch
        # No pyqtSignal in this module - mixin has no signals
        from tabs.theme_builder_tab import ThemeBuilderTabMixin, THEME_SLOTS
        self.mixin = ThemeBuilderTabMixin()
        self.mixin.tb_color_inputs = {}
        for slot_key, label, default in THEME_SLOTS:
            hex_input = MagicMock()
            hex_input.text.return_value = default
            swatch = MagicMock()
            btn = MagicMock()
            self.mixin.tb_color_inputs[slot_key] = (hex_input, swatch, btn)

    def tearDown(self):
        pass  # No patches to stop

    def test_returns_valid_hex(self):
        self.mixin.tb_color_inputs["bg"][0].text.return_value = "#FF007F"
        result = self.mixin._tb_get_color("bg", "#000000")
        self.assertEqual(result, "#FF007F")

    def test_falls_back_on_invalid_length(self):
        self.mixin.tb_color_inputs["bg"][0].text.return_value = "#FF0"
        result = self.mixin._tb_get_color("bg", "#000000")
        self.assertEqual(result, "#000000")

    def test_falls_back_on_missing_hash(self):
        self.mixin.tb_color_inputs["bg"][0].text.return_value = "FF007F"
        result = self.mixin._tb_get_color("bg", "#000000")
        self.assertEqual(result, "#000000")

    def test_falls_back_on_invalid_hex_chars(self):
        self.mixin.tb_color_inputs["bg"][0].text.return_value = "#ZZZZZZ"
        result = self.mixin._tb_get_color("bg", "#000000")
        self.assertEqual(result, "#000000")

    def test_falls_back_on_missing_slot(self):
        result = self.mixin._tb_get_color("nonexistent", "#000000")
        self.assertEqual(result, "#000000")

    def test_uppercase_hex_accepted(self):
        self.mixin.tb_color_inputs["text"][0].text.return_value = "#ABCDEF"
        result = self.mixin._tb_get_color("text", "#000000")
        self.assertEqual(result, "#ABCDEF")

    def test_mixed_case_hex_accepted(self):
        self.mixin.tb_color_inputs["text"][0].text.return_value = "#AbCdEf"
        result = self.mixin._tb_get_color("text", "#000000")
        self.assertEqual(result, "#AbCdEf")


class TestMixinGetCurrentColors(unittest.TestCase):
    """Test _tb_get_current_colors collects all 7 slot values."""

    def setUp(self):
        # No pyqtSignal in this module - mixin has no signals
        from tabs.theme_builder_tab import ThemeBuilderTabMixin, THEME_SLOTS
        self.mixin = ThemeBuilderTabMixin()
        self.mixin.tb_color_inputs = {}
        for slot_key, label, default in THEME_SLOTS:
            hex_input = MagicMock()
            hex_input.text.return_value = "#FF0000"
            swatch = MagicMock()
            btn = MagicMock()
            self.mixin.tb_color_inputs[slot_key] = (hex_input, swatch, btn)

    def tearDown(self):
        pass  # No patches to stop

    def test_returns_dict_with_7_keys(self):
        colors = self.mixin._tb_get_current_colors()
        self.assertEqual(len(colors), 7)
        for key in ["bg", "bg_alt", "bg_tab", "text", "text_tab", "accent1", "accent2"]:
            self.assertIn(key, colors)


class TestMixinSwatchUpdate(unittest.TestCase):
    """Test _tb_update_swatch and _tb_update_all_swatches."""

    def setUp(self):
        # No pyqtSignal in this module - mixin has no signals
        from tabs.theme_builder_tab import ThemeBuilderTabMixin, THEME_SLOTS
        self.mixin = ThemeBuilderTabMixin()
        self.mixin.tb_color_inputs = {}
        for slot_key, label, default in THEME_SLOTS:
            hex_input = MagicMock()
            hex_input.text.return_value = default
            swatch = MagicMock()
            btn = MagicMock()
            self.mixin.tb_color_inputs[slot_key] = (hex_input, swatch, btn)

    def tearDown(self):
        pass  # No patches to stop

    def test_updates_swatch_for_valid_hex(self):
        self.mixin.tb_color_inputs["bg"][0].text.return_value = "#FF007F"
        self.mixin._tb_update_swatch("bg")
        swatch = self.mixin.tb_color_inputs["bg"][1]
        swatch.setStyleSheet.assert_called_once()
        arg = swatch.setStyleSheet.call_args[0][0]
        
