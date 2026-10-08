"""
tabs/theme_builder_tab.py — Custom Theme Builder.

Lets users create, edit, save, and apply custom color themes
alongside the 4 built-in themes. Each theme defines 7 color slots:

  bg       — main background
  bg_alt   — alt/widget background
  bg_tab   — tab/header background
  text     — primary text color
  text_tab — muted/tab text color
  accent1  — primary accent (borders, buttons)
  accent2  — secondary accent (selection highlights)

Sprint 6 backlog item: Custom Theme Builder.
"""

import json
import threading
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QListWidget, QListWidgetItem, QGroupBox,
    QGridLayout, QColorDialog, QMessageBox, QFrame,
)

from config import CONFIG, save_settings
from logging_config import get_logger

if TYPE_CHECKING:
    from tabs.context import DashboardContext

# Thread safety for custom themes file I/O
_THEME_LOCK = threading.Lock()

logger = get_logger(name="ThemeBuilderTab")

# ── Theme slot metadata ───────────────────────────────────────────

THEME_SLOTS = [
    ("bg",       "Main Background",    "#0A0A0C"),
    ("bg_alt",   "Widget Background",  "#050507"),
    ("bg_tab",   "Tab Background",     "#13141C"),
    ("text",     "Primary Text",       "#00E5FF"),
    ("text_tab", "Muted / Tab Text",   "#007A88"),
    ("accent1",  "Primary Accent",     "#FF007F"),
    ("accent2",  "Secondary Accent",   "#00E5FF"),
]

# Built-in themes (same as AppUIMixin.THEMES) for reference
BUILTIN_THEMES = {
    "Cyberpunk (Pink/Cyan)": {"bg": "#0A0A0C", "bg_alt": "#050507", "bg_tab": "#13141C", "text": "#00E5FF", "text_tab": "#007A88", "accent1": "#FF007F", "accent2": "#00E5FF"},
    "Terminal (Amber/Orange)": {"bg": "#0A0800", "bg_alt": "#050400", "bg_tab": "#141000", "text": "#FFB000", "text_tab": "#CC8C00", "accent1": "#FF8C00", "accent2": "#FFB000"},
    "Matrix (Neon Green)": {"bg": "#050A05", "bg_alt": "#020502", "bg_tab": "#081408", "text": "#00FF41", "text_tab": "#008F11", "accent1": "#008F11", "accent2": "#00FF41"},
    "Classic (Charcoal)": {"bg": "#121212", "bg_alt": "#1C1C1C", "bg_tab": "#1C1C1C", "text": "#E0E0E0", "text_tab": "#9CA3AF", "accent1": "#374151", "accent2": "#10B981"},
}

CUSTOM_THEMES_FILE = None  # resolved on first use to SETTINGS_BACKUP_DIR


def _get_custom_themes_path():
    """Return the path to the custom themes JSON file."""
    global CUSTOM_THEMES_FILE
    if CUSTOM_THEMES_FILE is None:
        import os
        from config import WORKSPACE_DIR
        CUSTOM_THEMES_FILE = os.path.join(WORKSPACE_DIR, "custom_themes.json")
    return CUSTOM_THEMES_FILE


def load_custom_themes() -> dict:
    """Load custom themes from the JSON file. Returns {name: colors_dict}."""
    path = _get_custom_themes_path()
    try:
        import os
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to load custom themes: {e}")
    return {}


def save_custom_themes_to_disk(themes: dict):
    """Persist custom themes to the JSON file.
    Thread-safe via _THEME_LOCK.
    """
    path = _get_custom_themes_path()
    with _THEME_LOCK:
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(themes, f, indent=2)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to save custom themes: {e}")


def get_all_themes() -> dict:
    """Return merged dict of built-in + custom themes."""
    themes = dict(BUILTIN_THEMES)
    themes.update(load_custom_themes())
    return themes


def apply_theme_to_widget(widget, theme_colors: dict):
    """Apply a theme dict (7 color keys) to a widget via setStyleSheet.

    This mirrors the logic in AppUIMixin.apply_theme(), including
    the ThinkingDisplay and AuditLog styles.
    """
    if not theme_colors:
        return
    t = theme_colors
    widget.setStyleSheet(f"""
        QMainWindow {{ background-color: {t.get('bg', '#121212')}; }}
        QWidget {{ background-color: {t.get('bg', '#121212')}; color: {t.get('text', '#E0E0E0')}; font-family: 'Consolas', monospace; }}
        QTabWidget::pane {{ border: 1px solid {t.get('accent1', '#374151')}; }}
        QTabBar::tab {{ background-color: {t.get('bg_tab', '#1C1C1C')}; border: 1px solid {t.get('accent1', '#374151')}; padding: 8px 16px; color: {t.get('text_tab', '#9CA3AF')}; }}
        QTabBar::tab:selected {{ background-color: {t.get('bg', '#121212')}; color: {t.get('text', '#E0E0E0')}; border-top: 2px solid {t.get('accent2', '#10B981')}; }}
        QTextEdit, QLineEdit, QListWidget, QComboBox, QTreeWidget, QSpinBox, QTableWidget {{ background-color: {t.get('bg_alt', '#1C1C1C')}; border: 1px solid {t.get('accent1', '#374151')}; padding: 6px; color: {t.get('text', '#E0E0E0')}; gridline-color: {t.get('accent1', '#374151')}; }}
        QHeaderView::section {{ background-color: {t.get('bg_tab', '#1C1C1C')}; color: {t.get('text_tab', '#9CA3AF')}; padding: 4px; border: 1px solid {t.get('accent1', '#374151')}; font-weight: bold; }}
        QPushButton {{ background-color: {t.get('bg_tab', '#1C1C1C')}; color: {t.get('text', '#E0E0E0')}; border: 1px solid {t.get('accent1', '#374151')}; padding: 8px 16px; font-weight: bold; }}
        QPushButton:hover {{ background-color: {t.get('accent1', '#374151')}; color: #000; border: 1px solid {t.get('accent2', '#10B981')}; }}
        QGroupBox {{ border: 1px solid {t.get('accent1', '#374151')}; padding-top: 15px; margin-top: 10px; font-weight: bold; }}
        QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }}
        QFrame#Separator {{ background-color: {t.get('accent1', '#374151')}; }}
        QTextEdit#ThinkingDisplay {{ color: {t.get('text_tab', '#9CA3AF')}; background-color: {t.get('bg_tab', '#1C1C1C')}; font-size: 10pt; }}
        QTextEdit#AuditLog {{ background-color: {t.get('bg_tab', '#1C1C1C')}; font-size: 9pt; color: {t.get('accent2', '#10B981')}; }}
    """)


# ── Theme Builder Tab Mixin ───────────────────────────────────────

class ThemeBuilderTabMixin:
    """Mixin that adds a Custom Theme Builder tab."""

    @property
    def context(self) -> "DashboardContext":
        """Return the shared DashboardContext, falling back to legacy attributes if ctx is unset."""
        if hasattr(self, "ctx") and self.ctx is not None:
            return self.ctx
        from tabs.context import DashboardContext
        return DashboardContext(
            controller=getattr(self, "controller", None),
            file_logger=getattr(self, "file_logger", None),
            log_to_audit=getattr(self, "log_to_audit", None),
            audit_signal=getattr(self, "audit_signal", None),
            config=getattr(self, "config", {}),
            restore_chat_input=getattr(self, "restore_chat_input", None),
            switch_tab=getattr(self, "switch_tab", None),
        )

    @context.setter
    def context(self, value: "DashboardContext") -> None:
        self.ctx = value

    def create_theme_builder_tab(self):
        """Build and return the Theme Builder tab widget."""
        tab = QWidget()
        layout = QVBoxLayout(tab)

        # ── Header ─────────────────────────────────────────────────
        header = QLabel("🎨 Custom Theme Builder")
        header.setStyleSheet("font-weight: bold; font-size: 13pt; color: #10B981; padding: 4px;")
        layout.addWidget(header)

        # ── Theme preset list + controls ───────────────────────────
        preset_group = QGroupBox("Theme Presets")
        preset_layout = QVBoxLayout(preset_group)

        preset_top = QHBoxLayout()
        self.tb_preset_list = QListWidget()
        self.tb_preset_list.setMaximumHeight(120)
        self.tb_preset_list.itemClicked.connect(self._tb_load_preset)
        preset_top.addWidget(self.tb_preset_list)

        # Right side: action buttons for preset list
        preset_actions = QVBoxLayout()
        self.tb_btn_new = QPushButton("New Theme")
        self.tb_btn_new.clicked.connect(self._tb_new_theme)
        preset_actions.addWidget(self.tb_btn_new)

        self.tb_btn_delete = QPushButton("Delete Theme")
        self.tb_btn_delete.setStyleSheet("color: #EF4444;")
        self.tb_btn_delete.clicked.connect(self._tb_delete_theme)
        preset_actions.addWidget(self.tb_btn_delete)

        preset_actions.addStretch()
        preset_top.addLayout(preset_actions)
        preset_layout.addLayout(preset_top)
        layout.addWidget(preset_group)

        # ── Theme name ─────────────────────────────────────────────
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel("Theme Name:"))
        self.tb_theme_name = QLineEdit()
        self.tb_theme_name.setPlaceholderText("My Custom Theme")
        name_layout.addWidget(self.tb_theme_name, 1)
        layout.addLayout(name_layout)

        # ── Color picker grid ──────────────────────────────────────
        color_group = QGroupBox("Theme Colors")
        color_grid = QGridLayout()
        color_grid.setSpacing(8)

        self.tb_color_inputs = {}  # slot_key -> (hex_input, color_swatch, button)
        self.tb_color_swatches = {}  # slot_key -> QFrame swatch

        for i, (slot_key, label, default_hex) in enumerate(THEME_SLOTS):
            # Label
            lbl = QLabel(f"{label} ({slot_key}):")
            lbl.setStyleSheet("font-size: 9pt;")
            color_grid.addWidget(lbl, i, 0)

            # Hex input
            hex_input = QLineEdit()
            hex_input.setText(default_hex)
            hex_input.setPlaceholderText("#RRGGBB")
            hex_input.setMaxLength(7)
            hex_input.textChanged.connect(lambda txt, k=slot_key: self._tb_on_hex_changed(k, txt))
            color_grid.addWidget(hex_input, i, 1)

            # Color swatch (small colored square)
            swatch = QFrame()
            swatch.setFixedSize(28, 28)
            swatch.setStyleSheet(f"background-color: {default_hex}; border: 1px solid #555; border-radius: 3px;")
            color_grid.addWidget(swatch, i, 2)

            # Pick color button
            pick_btn = QPushButton("Pick")
            pick_btn.setFixedWidth(50)
            pick_btn.clicked.connect(lambda checked, k=slot_key: self._tb_pick_color(k))
            color_grid.addWidget(pick_btn, i, 3)

            self.tb_color_inputs[slot_key] = (hex_input, swatch, pick_btn)
            self.tb_color_swatches[slot_key] = swatch

        color_group.setLayout(color_grid)
        layout.addWidget(color_group)

        # ── Swatch preview bar ─────────────────────────────────────
        preview_group = QGroupBox("Live Preview")
        preview_layout = QVBoxLayout(preview_group)

        self.tb_preview_bar = QFrame()
        self.tb_preview_bar.setFixedHeight(30)
        self._tb_update_preview_bar()
        preview_layout.addWidget(self.tb_preview_bar)

        preview_info = QLabel(
            "The preview bar shows accent1 → accent2 → text gradient. "
            "Click 'Apply Preview' to see colors on the whole dashboard."
        )
        preview_info.setWordWrap(True)
        preview_info.setStyleSheet("font-size: 8pt; color: #9CA3AF;")
        preview_layout.addWidget(preview_info)
        layout.addWidget(preview_group)

        # ── Action buttons ─────────────────────────────────────────
        actions_layout = QHBoxLayout()

        self.tb_btn_apply = QPushButton("Apply Theme")
        self.tb_btn_apply.setStyleSheet("font-weight: bold; background-color: #1e3a5f; border-color: #2563eb;")
        self.tb_btn_apply.clicked.connect(self._tb_apply_theme)
        actions_layout.addWidget(self.tb_btn_apply)

        self.tb_btn_save = QPushButton("Save Custom Theme")
        self.tb_btn_save.setStyleSheet("font-weight: bold;")
        self.tb_btn_save.clicked.connect(self._tb_save_theme)
        actions_layout.addWidget(self.tb_btn_save)

        self.tb_btn_reset = QPushButton("Reset to Defaults")
        self.tb_btn_reset.clicked.connect(self._tb_reset_to_defaults)
        actions_layout.addWidget(self.tb_btn_reset)

        layout.addLayout(actions_layout)
        layout.addStretch()

        # ── Populate preset list ───────────────────────────────────
        QTimer.singleShot(50, self._tb_refresh_preset_list)

        return tab

    # ── Preset list ───────────────────────────────────────────────

    def _tb_refresh_preset_list(self):
        """Refresh the preset list with all available themes."""
        self.tb_preset_list.clear()
        all_themes = get_all_themes()
        for name in sorted(all_themes.keys()):
            item = QListWidgetItem(name)
            # Mark custom themes with a badge
            custom_themes = load_custom_themes()
            if name in custom_themes:
                item.setToolTip("Custom theme — editable")
            else:
                item.setToolTip("Built-in theme (read-only reference)")
            self.tb_preset_list.addItem(item)

    def _tb_load_preset(self, item):
        """Load a theme preset into the editor."""
        name = item.text()
        all_themes = get_all_themes()
        colors = all_themes.get(name)
        if not colors:
            return

        self.tb_theme_name.setText(name)
        # tb_color_inputs stores: slot_key -> (hex_input, swatch, pick_btn)
        # Older/broken code attempted to unpack 4 values; keep compatible with both shapes.
        for slot_key, entry in self.tb_color_inputs.items():
            if slot_key not in colors:
                continue
            if isinstance(entry, (list, tuple)):
                hex_input = entry[0] if len(entry) > 0 else None
            else:
                hex_input = None
            if hex_input is not None:
                hex_input.setText(colors[slot_key])

        # Trigger derived visuals
        self._tb_update_all_swatches()


    # ── Color pickers ─────────────────────────────────────────────

    def _tb_pick_color(self, slot_key: str):
        """Open a QColorDialog for the given slot."""
        current_hex = self.tb_color_inputs[slot_key][0].text().strip()
        initial = QColor(current_hex) if QColor.isValidColorName(current_hex) else None

        color = QColorDialog.getColor(
            initial=initial if initial and initial.isValid() else Qt.GlobalColor.white,
            title=f"Pick {slot_key} color",
        )
        if color.isValid():
            hex_str = color.name()
            self.tb_color_inputs[slot_key][0].setText(hex_str)

    def _tb_on_hex_changed(self, slot_key: str, text: str):
        """Update swatch when hex text changes."""
        self._tb_update_swatch(slot_key)
        self._tb_update_preview_bar()

    def _tb_update_swatch(self, slot_key: str):
        """Update a single swatch from its hex input."""
        hex_input, swatch, _btn = self.tb_color_inputs.get(slot_key, (None, None, None))
        if hex_input is None:
            return
        hex_str = hex_input.text().strip()
        if hex_str and len(hex_str) == 7 and hex_str.startswith("#"):
            swatch.setStyleSheet(
                f"background-color: {hex_str}; border: 1px solid #555; border-radius: 3px;"
            )
        else:
            swatch.setStyleSheet(
                "background-color: #333; border: 1px solid #EF4444; border-radius: 3px;"
            )

    def _tb_update_all_swatches(self):
        """Update all swatches from their hex inputs."""
        for slot_key in self.tb_color_inputs:
            self._tb_update_swatch(slot_key)

    def _tb_update_preview_bar(self):
        """Update the gradient preview bar."""
        c1 = self._tb_get_color("accent1", "#374151")
        c2 = self._tb_get_color("accent2", "#10B981")
        c3 = self._tb_get_color("text", "#E0E0E0")
        if hasattr(self, "tb_preview_bar"):
            self.tb_preview_bar.setStyleSheet(
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                f"stop:0 {c1}, stop:0.5 {c2}, stop:1 {c3}); "
                f"border: 1px solid #555; border-radius: 3px;"
            )

    def _tb_get_color(self, slot_key: str, fallback: str) -> str:
        """Get a hex color from the inputs or fallback.
        Validates that characters are valid hex digits (0-9, a-f, A-F).
        """
        entry = self.tb_color_inputs.get(slot_key)
        if entry:
            text = entry[0].text().strip()
            if (len(text) == 7 and text.startswith("#")
                    and all(c in "0123456789abcdefABCDEF" for c in text[1:])):
                return text
        return fallback

    def _tb_get_current_colors(self) -> dict:
        """Collect all 7 color values from the inputs."""
        colors = {}
        for slot_key, _label, _default in THEME_SLOTS:
            colors[slot_key] = self._tb_get_color(slot_key, _default)
        return colors

    # ── Actions ───────────────────────────────────────────────────

    def _tb_apply_theme(self):
        """Apply the current editor colors to the dashboard immediately."""
        colors = self._tb_get_current_colors()
        name = self.tb_theme_name.text().strip() or "Untitled Preview"
        # Apply via the dashboard's apply_theme mechanism
        if hasattr(self, "apply_theme"):
            # Temporarily register as a theme so apply_theme can find it
            all_themes = get_all_themes()
            all_themes[name] = colors
            # Save to custom themes for apply_theme to find via THEMES
            # Since THEMES is a class-level dict, we need a different approach.
            # Instead, directly call apply_theme with the colors.
            if hasattr(self, "setStyleSheet"):
                # Use the shared apply_theme_to_widget function
                apply_theme_to_widget(self, colors)
                # Also update the theme swatch in settings if available
                if hasattr(self, "_update_theme_swatch"):
                    self._update_theme_swatch()
                self.context.log(
                    f"🎨 Theme preview applied: '{name}'"
                )
                logger.info(f"Theme preview applied: {name}")
        else:
            # Fallback: just show a message
            QMessageBox.information(
                self.tb_preset_list, "Theme Preview",
                "Theme colors ready. Use 'Save Custom Theme' first, "
                "then apply from the Settings tab theme dropdown.",
            )

    def _tb_save_theme(self):
        """Save the current editor state as a custom theme."""
        name = self.tb_theme_name.text().strip()
        if not name:
            QMessageBox.warning(
                self.tb_preset_list, "Name Required",
                "Please enter a theme name before saving.",
            )
            return

        colors = self._tb_get_current_colors()

        # Validate colors
        for slot_key, hex_val in colors.items():
            if not (len(hex_val) == 7 and hex_val.startswith("#")):
                QMessageBox.warning(
                    self.tb_preset_list, "Invalid Color",
                    f"'{slot_key}' has an invalid hex color: {hex_val}\n"
                    "Use format #RRGGBB (e.g., #FF007F).",
                )
                return

        # Check for overwrite
        custom_themes = load_custom_themes()
        is_new = name not in custom_themes
        if not is_new:
            reply = QMessageBox.question(
                self.tb_preset_list, "Overwrite Theme?",
                f"A custom theme named '{name}' already exists.\n"
                "Do you want to overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Save
        custom_themes[name] = colors
        save_custom_themes_to_disk(custom_themes)

        # Also sync into CONFIG for persistence in app_settings.json
        CONFIG["custom_themes"] = custom_themes
        save_settings()

        # Refresh preset list
        self._tb_refresh_preset_list()

        # Log
        action = "Saved" if is_new else "Overwritten"
        self.context.log(
            f"🎨 Custom theme '{action}': '{name}' "
            f"({len(colors)} color slots)"
        )
        logger.info(f"Custom theme {action.lower()}: {name}")

        QMessageBox.information(
            self.tb_preset_list, "Theme Saved",
            f"Custom theme '{name}' saved successfully.\n\n"
            "You can now choose it from the Settings → Dashboard Theme dropdown.",
        )

    def _tb_delete_theme(self):
        """Delete the currently selected custom theme."""
        selected = self.tb_preset_list.currentItem()
        if not selected:
            QMessageBox.information(
                self.tb_preset_list, "No Selection",
                "Select a custom theme from the list to delete.",
            )
            return

        name = selected.text()
        custom_themes = load_custom_themes()

        if name not in custom_themes:
            QMessageBox.information(
                self.tb_preset_list, "Cannot Delete",
                f"'{name}' is a built-in theme and cannot be deleted.",
            )
            return

        reply = QMessageBox.warning(
            self.tb_preset_list, "Confirm Delete",
            f"Delete custom theme '{name}'?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        del custom_themes[name]
        save_custom_themes_to_disk(custom_themes)
        CONFIG["custom_themes"] = custom_themes
        save_settings()

        self._tb_refresh_preset_list()
        self._tb_new_theme()
        self.context.log(f"🗑️ Custom theme deleted: '{name}'")

    def _tb_new_theme(self):
        """Reset the editor to default values for a new theme."""
        self.tb_theme_name.clear()
        for slot_key, _label, default_hex in THEME_SLOTS:
            if slot_key in self.tb_color_inputs:
                self.tb_color_inputs[slot_key][0].setText(default_hex)
        self._tb_update_all_swatches()
        self._tb_update_preview_bar()

    def _tb_reset_to_defaults(self):
        """Reset all colors to the 'Classic (Charcoal)' defaults."""
        defaults = BUILTIN_THEMES["Classic (Charcoal)"]
        for slot_key, hex_val in defaults.items():
            if slot_key in self.tb_color_inputs:
                self.tb_color_inputs[slot_key][0].setText(hex_val)
        self._tb_update_all_swatches()
        self._tb_update_preview_bar()
