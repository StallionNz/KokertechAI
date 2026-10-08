"""System Settings tab mixin."""
import os, threading
from contextlib import contextmanager
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QPushButton, QSpinBox, QDoubleSpinBox, QComboBox, QTextEdit, QGroupBox, QLabel, QMessageBox, QCheckBox, QScrollArea, QInputDialog
from config import CONFIG, IDENTITY_CONFIG, WORKSPACE_DIR, save_settings, save_identity, list_backups, restore_backup, export_backup, import_backup, SETTINGS_PATH
from keepalive_helper import MAX_TICK_FIRES, KeepaliveContext
from tabs.theme_builder_tab import get_all_themes
from logging_config import get_logger

logger = get_logger(name="SettingsTab")

# ── Built-in protocol templates ──────────────────────────────────────
BUILTIN_PROTOCOL_TEMPLATES = {
    "Structured with reasoning": (
        "You are KokertechAI, the Executive Core system. "
        "Your responses must use structured XML format.\n\n"
        "CRITICAL PROTOCOL:\n"
        "You must always respond using the following XML structure:\n\n"
        "<thinking>\n"
        "Show your step-by-step reasoning process here. Consider the user's "
        "request, break it down, and plan your approach before responding.\n"
        "</thinking>\n\n"
        "<final_output>\n"
        "Provide your final clear, concise response to the user here.\n"
        "</final_output>"
    ),
    "Freeform Assistant": (
        "You are KokertechAI, a helpful AI assistant. You respond in a "
        "natural, conversational manner without rigid formatting.\n\n"
        "CRITICAL PROTOCOL:\n"
        "- Be warm and conversational in your responses\n"
        "- Use natural language and vary your sentence structure\n"
        "- Be concise but thorough when needed\n"
        "- Avoid XML tags or structured formatting\n"
        "- Let the conversation flow naturally"
    ),
    "Concise Coder": (
        "You are KokertechAI, a precise coding assistant. "
        "Be concise and efficient.\n\n"
        "CRITICAL PROTOCOL:\n"
        "- Provide code solutions with minimal explanation\n"
        "- Focus on working code over verbose commentary\n"
        "- Use clear, idiomatic code patterns\n"
        "- Prefer inline comments over long explanations\n"
        "- Only explain when the user asks for it"
    ),
}


def _coerce_pct(value, default):
    """Coerce a CONFIG percentage value to int, falling back to default.

    Guards QSpinBox.setValue against a hand-edited app_settings.json holding
    a string (e.g. "80" instead of 80) — int() raises TypeError/ValueError,
    which would otherwise crash the Settings tab at construction AND at sync
    time. Mirrors the keepalive safe-coerce contract
    (KeepaliveContext.default_max_ticks).
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class SettingsTabMixin:
    @property
    def context(self):
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
            restore_chat_input=(
                (lambda text: self.txt_input.setPlainText(text))
                if hasattr(self, "txt_input")
                else None
            ),
        )

    @context.setter
    def context(self, value):
        self.ctx = value

    def create_settings_tab(self):
        # Wrap all settings in a scroll area so they remain accessible
        # at any window size. Minimum height prevents the tab from collapsing
        # to zero if the parent QTabWidget mis-sizes during window resize.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(300)
        content = QWidget()
        layout = QVBoxLayout(content)

        # Modularize setup by adding group-specific creation methods
        
        layout.addWidget(self._create_sys_config_group())
        layout.addWidget(self._create_tts_settings_group())
        
        # ... (rest of the setup continues below)
        # For the audit, we'll keep the existing structure but suggest this pattern.
        layout.addWidget(self._create_sandbox_group())
        layout.addWidget(self._create_web_settings_group())
        layout.addWidget(self._create_persona_group())
        layout.addWidget(self._create_protocol_group())
        layout.addWidget(self._create_mode_group())
        layout.addWidget(self._create_hotkey_group())
        layout.addWidget(self._create_registry_group())
        layout.addWidget(self._create_provider_control_group())
        layout.addWidget(self._create_prefs_group())
        layout.addWidget(self._create_identity_group())
        layout.addWidget(self._create_backup_group())

        btn_save = QPushButton("💾 SAVE ALL CONFIGURATIONS")
        btn_save.setStyleSheet("font-weight: bold; margin-top: 15px; padding: 10px;")
        btn_save.clicked.connect(self.save_settings)
        layout.addWidget(btn_save)
        layout.addStretch()
        # Defer network/thread/TTS operations to after the event loop starts.
        # During widget construction, QTimer.singleShot(0, ...) callbacks from
        # background threads can race with the non-running event loop, causing
        # hangs in tests and intermittent startup delays.
        from PyQt6.QtCore import QTimer
        # Tests expect _update_provider_fields(provider_switched=False) to be called
        # during initial create_settings_tab().
        try:
            self._update_provider_fields(provider_switched=False)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.debug(f"Initial provider-field update failed: {e}")
        QTimer.singleShot(0, self._deferred_init_settings_tab)


        scroll.setWidget(content)
        return scroll

    def _create_sys_config_group(self):
        group = QGroupBox('Core System Configuration (app_settings.json)')
        layout = QFormLayout()
        self.vram_spinbox = QSpinBox()
        self.vram_spinbox.setRange(512, 24000)
        self.vram_spinbox.setValue(CONFIG.get('vram_limit_mb', 2048))
        layout.addRow('VRAM Limit (MB):', self.vram_spinbox)
        self.provider_combo = QComboBox()
        self.provider_combo.addItem('Local LLM (GGUF)')
        self.provider_combo.setEnabled(False)
        current_provider = CONFIG.get('active_provider', 'local_llm')
        idx = self.provider_combo.findText(self._PROVIDER_DEFAULTS.get(current_provider, {}).get('label', 'Local LLM (GGUF)'))
        if idx >= 0:
            self.provider_combo.setCurrentIndex(idx)
        self.provider_combo.currentTextChanged.connect(self._on_provider_changed)
        provider_row = QHBoxLayout()
        provider_row.addWidget(self.provider_combo)
        self.provider_status_indicator = QLabel('')
        self.provider_status_indicator.setFixedWidth(60)
        self.provider_status_indicator.setStyleSheet('font-size: 8pt; color: #9CA3AF; padding-left: 4px;')
        provider_row.addWidget(self.provider_status_indicator)
        layout.addRow('AI Provider:', provider_row)

        # ── Local LLM fields (models directory) ──
        self.models_dir_label = QLabel('Models Directory:')
        self.models_dir_input = QLineEdit()
        self.models_dir_input.setPlaceholderText(os.path.join(WORKSPACE_DIR, 'models'))
        btn_browse_models_dir = QPushButton('📂 Browse')
        btn_browse_models_dir.setFixedWidth(80)
        btn_browse_models_dir.clicked.connect(self._browse_models_directory)
        models_dir_row = QHBoxLayout()
        models_dir_row.addWidget(self.models_dir_input, 1)
        models_dir_row.addWidget(btn_browse_models_dir)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setPlaceholderText('Select or type model...')
        btn_refresh_models = QPushButton('🔄')
        btn_refresh_models.setFixedWidth(36)
        btn_refresh_models.setToolTip('Refresh model list from provider')
        btn_refresh_models.clicked.connect(self._refresh_model_dropdown)
        # Activated fires on dropdown selection (not keystroke edits), so typing
        # a custom name then clicking Apply is the intended UX path. The Apply
        # button below handles typed entries.
        self.model_combo.activated.connect(
            lambda idx: self._on_model_changed(self.model_combo.itemText(idx))
        )
        btn_apply_model = QPushButton('⚡ Apply')
        btn_apply_model.setFixedWidth(72)
        btn_apply_model.setToolTip('Apply the selected model (hot-swap without restart)')
        btn_apply_model.setStyleSheet('font-size: 9pt; padding: 4px 8px; font-weight: bold;')
        self._btn_apply_model = btn_apply_model
        btn_apply_model.clicked.connect(lambda: self._on_model_changed(self.model_combo.currentText()))
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(btn_apply_model)
        model_row.addWidget(btn_refresh_models)

        btn_test = QPushButton('🔌 Test Connection')
        btn_test.clicked.connect(self._test_provider_connection)
        btn_test.setStyleSheet('font-size: 9pt; padding: 4px 12px;')

        # ── Store local LLM fields in a widget so we can show/hide as a group ──
        self._local_llm_fields = QWidget()
        local_llm_form = QFormLayout(self._local_llm_fields)
        local_llm_form.setContentsMargins(0, 0, 0, 0)
        local_llm_form.addRow(self.models_dir_label, models_dir_row)
        local_llm_form.addRow('Model:', model_row)
        local_llm_form.addRow('', self._create_local_llm_advanced_group())

        layout.addRow(self._local_llm_fields)
        layout.addRow('', btn_test)
        desire_model = str(CONFIG.get('desire_model_name', 'qwen2.5-0.5b-instruct'))
        self.desire_model_input = QLineEdit(desire_model)
        layout.addRow('Desire Engine Model:', self.desire_model_input)
        auditor_model = str(CONFIG.get('auditor_model_name', 'qwen2.5-0.5b-instruct'))
        self.auditor_model_input = QLineEdit(auditor_model)
        layout.addRow('Auditor Engine Model:', self.auditor_model_input)
        vision_model_row = QHBoxLayout()
        self.vision_model_combo = QComboBox()
        self.vision_model_combo.setEditable(True)
        self.vision_model_combo.setPlaceholderText('(Empty — uses main model)')
        self.vision_model_combo.setToolTip('Vision model for screen capture analysis. If empty, the main chat model is used (may not support vision).\nClick 🔄 to auto-detect vision-capable models from the models directory.')
        saved_vision = CONFIG.get('vision_model', '')
        if saved_vision:
            self.vision_model_combo.addItem(saved_vision)
            self.vision_model_combo.setCurrentText(saved_vision)
        vision_model_row.addWidget(self.vision_model_combo, 1)
        btn_refresh_vision = QPushButton('🔄')
        btn_refresh_vision.setFixedWidth(36)
        btn_refresh_vision.setToolTip('Refresh vision-capable models from the models directory')
        btn_refresh_vision.clicked.connect(self._refresh_vision_models)
        vision_model_row.addWidget(btn_refresh_vision)
        # Activated fires on dropdown selection (not keystroke edits), so typing
        # a custom name then clicking Apply is the intended UX path. The Apply
        # button below handles typed entries.
        self.vision_model_combo.activated.connect(
            lambda idx: self._on_vision_model_changed(
                self.vision_model_combo.itemText(idx)
            )
        )
        btn_apply_vision = QPushButton('⚡ Apply')
        btn_apply_vision.setFixedWidth(72)
        btn_apply_vision.setToolTip('Apply the selected vision model')
        btn_apply_vision.setStyleSheet('font-size: 9pt; padding: 4px 8px; font-weight: bold;')
        self._btn_apply_vision = btn_apply_vision
        btn_apply_vision.clicked.connect(lambda: self._on_vision_model_changed(self.vision_model_combo.currentText()))
        vision_model_row.addWidget(btn_apply_vision)
        layout.addRow('Vision Model:', vision_model_row)
        self.setting_theme_combo = QComboBox()
        all_themes = get_all_themes()
        for name in sorted(all_themes.keys()):
            self.setting_theme_combo.addItem(name)
        saved_theme = CONFIG.get('active_theme', 'Classic (Charcoal)')
        tidx = self.setting_theme_combo.findText(saved_theme)
        if tidx >= 0:
            self.setting_theme_combo.setCurrentIndex(tidx)
        self.setting_theme_combo.currentTextChanged.connect(self._on_theme_changed)
        layout.addRow('Dashboard Theme:', self.setting_theme_combo)
        self.theme_swatch = QWidget()
        self.theme_swatch.setFixedHeight(20)
        self._update_theme_swatch()
        layout.addRow('Preview:', self.theme_swatch)
        group.setLayout(layout)
        return group

    def _create_tts_settings_group(self):
        group = QGroupBox('Text-to-Speech (Piper)')
        layout = QFormLayout()
        self.tts_enabled_cb = QCheckBox('Enable voice output for AI responses')
        self.tts_enabled_cb.setChecked(CONFIG.get('tts_enabled', True))
        layout.addRow('', self.tts_enabled_cb)
        self.tts_speed_spin = QSpinBox()
        self.tts_speed_spin.setRange(50, 300)
        self.tts_speed_spin.setValue(CONFIG.get('tts_speed', 180))
        self.tts_speed_spin.setSuffix(' WPM')
        layout.addRow('Voice Speed:', self.tts_speed_spin)
        self.tts_voice_combo = QComboBox()
        self.tts_voice_combo.setEditable(True)
        self.tts_voice_combo.setPlaceholderText('Loading voices...')
        layout.addRow('Voice:', self.tts_voice_combo)
        btn_tts_test = QPushButton('Test Voice')
        btn_tts_test.clicked.connect(self._test_tts)
        layout.addRow('', btn_tts_test)

        self.duplex_barge_in_cb = QCheckBox('Enable duplex barge-in (interrupt AI speech)')
        self.duplex_barge_in_cb.setChecked(CONFIG.get('duplex_barge_in_enabled', True))
        layout.addRow('', self.duplex_barge_in_cb)

        self.duplex_multiplier_spin = QDoubleSpinBox()
        self.duplex_multiplier_spin.setRange(1.0, 5.0)
        self.duplex_multiplier_spin.setSingleStep(0.1)
        self.duplex_multiplier_spin.setValue(float(CONFIG.get('duplex_energy_multiplier', 2.0)))
        self.duplex_multiplier_spin.setSuffix('x')
        layout.addRow('Barge-in VAD Multiplier:', self.duplex_multiplier_spin)

        self.duplex_min_speech_spin = QDoubleSpinBox()
        self.duplex_min_speech_spin.setRange(0.05, 1.0)
        self.duplex_min_speech_spin.setSingleStep(0.05)
        self.duplex_min_speech_spin.setValue(float(CONFIG.get('duplex_min_speech_duration', 0.2)))
        self.duplex_min_speech_spin.setSuffix('s')
        layout.addRow('Barge-in Speech Duration:', self.duplex_min_speech_spin)

        self.duplex_duck_volume_spin = QDoubleSpinBox()
        self.duplex_duck_volume_spin.setRange(0.05, 0.90)
        self.duplex_duck_volume_spin.setSingleStep(0.05)
        self.duplex_duck_volume_spin.setValue(float(CONFIG.get('duplex_duck_volume', 0.25)))
        layout.addRow('Barge-in Ducking Volume:', self.duplex_duck_volume_spin)

        group.setLayout(layout)
        return group

    def _create_sandbox_group(self):
        group = QGroupBox('Docker Code Sandbox')
        layout = QFormLayout()
        self.sandbox_enabled_cb = QCheckBox('Enable Docker sandbox for code execution')
        self.sandbox_enabled_cb.setChecked(CONFIG.get('docker_sandbox_enabled', True))
        layout.addRow('', self.sandbox_enabled_cb)
        self.sandbox_image_input = QLineEdit(CONFIG.get('docker_sandbox_image', 'python:3.11-slim'))
        self.sandbox_image_input.setPlaceholderText('python:3.11-slim')
        layout.addRow('Docker Image:', self.sandbox_image_input)
        self.sandbox_timeout_spin = QSpinBox()
        self.sandbox_timeout_spin.setRange(5, 300)
        self.sandbox_timeout_spin.setValue(CONFIG.get('docker_sandbox_timeout', 30))
        self.sandbox_timeout_spin.setSuffix('s')
        layout.addRow('Execution Timeout:', self.sandbox_timeout_spin)
        self.sandbox_memory_input = QLineEdit(CONFIG.get('docker_sandbox_memory', '256m'))
        self.sandbox_memory_input.setPlaceholderText('256m')
        layout.addRow('Memory Limit:', self.sandbox_memory_input)
        self.sandbox_status_label = QLabel('')
        layout.addRow('Status:', self.sandbox_status_label)
        btn_sandbox_test = QPushButton('🐳 Test Docker Connection')
        btn_sandbox_test.clicked.connect(self._test_docker_sandbox)
        layout.addRow('', btn_sandbox_test)
        group.setLayout(layout)
        return group

    def _create_web_settings_group(self):
        group = QGroupBox('🌐 Web Server (Flask UI)')
        layout = QFormLayout()
        self.web_server_cb = QCheckBox('Enable web server on startup')
        self.web_server_cb.setChecked(CONFIG.get('web_server_enabled', False))
        layout.addRow('', self.web_server_cb)
        self.web_port_spin = QSpinBox()
        self.web_port_spin.setRange(1024, 65535)
        self.web_port_spin.setValue(CONFIG.get('web_server_port', 5050))
        layout.addRow('Port:', self.web_port_spin)
        self.web_server_status = QLabel('')
        layout.addRow('Status:', self.web_server_status)
        btn_web_open = QPushButton('↗ Open in Browser')
        btn_web_open.setToolTip('Open the web UI in your default system browser')
        btn_web_open.clicked.connect(self._open_web_server_browser)
        layout.addRow('', btn_web_open)
        group.setLayout(layout)
        return group

    def _create_persona_group(self):
        group = QGroupBox('AI Persona')
        layout = QFormLayout()
        self.persona_combo = QComboBox()
        self.persona_combo.setEditable(True)
        self.persona_combo.setPlaceholderText('Type a custom persona or select a preset...')
        saved_personas = CONFIG.get('saved_personas', [])
        if saved_personas:
            self.persona_combo.addItems(saved_personas)
        current_persona = CONFIG.get('active_persona', 'You are a helpful AI assistant.')
        cidx = self.persona_combo.findText(current_persona)
        if cidx >= 0:
            self.persona_combo.setCurrentIndex(cidx)
        else:
            self.persona_combo.setCurrentText(current_persona)
        layout.addRow('Active Persona:', self.persona_combo)
        btn_row = QHBoxLayout()
        btn_rand = QPushButton('🎲 Random')
        btn_rand.clicked.connect(self._pick_random_persona)
        btn_row.addWidget(btn_rand)
        btn_save = QPushButton('💾 Save Preset')
        btn_save.clicked.connect(self._save_persona_preset)
        btn_row.addWidget(btn_save)
        btn_del = QPushButton('🗑️ Delete')
        btn_del.clicked.connect(self._delete_persona_preset)
        btn_row.addWidget(btn_del)

        # ── Sub-agents preset personas (Orchestrator + Synthesizer) ──
        # These are injected into the persona dropdown so the router's
        # dispatch personas are selectable alongside Coder/Auditor/etc.
        # They are NOT stored in CONFIG['saved_personas'] here - that list
        # is persisted to disk by _save_persona_preset.
        default_personas = [
            "Orchestrator: classify user intent and dispatch to the matching sub-agent, then hand raw output to the Synthesizer.",
            "Synthesizer: take raw sub-agent output and any user text, and assemble it into a single coherent, well-structured final response.",
        ]

        saved_personas = CONFIG.get('saved_personas', [])
        preset_personas = [p for p in default_personas if p not in saved_personas]
        if preset_personas:
            self.persona_combo.addItems(preset_personas)
        layout.addRow('', btn_row)
        group.setLayout(layout)
        return group

    def _create_protocol_group(self):
        group = QGroupBox('System Protocol')
        layout = QFormLayout()
        self.protocol_preset_combo = QComboBox()
        self.protocol_preset_count = QLabel()
        self.protocol_preset_count.setStyleSheet('font-size: 9pt; color: #9CA3AF; padding-left: 4px;')
        self._seed_builtin_protocol_presets()
        self._rebuild_protocol_presets()
        self.protocol_preset_combo.currentIndexChanged.connect(self._on_protocol_preset_changed)
        preset_row = QHBoxLayout()
        preset_row.addWidget(self.protocol_preset_combo, 1)
        preset_row.addWidget(self.protocol_preset_count)
        layout.addRow('Protocol Preset:', preset_row)
        protocol_preset_btn_row = QHBoxLayout()
        btn_save_protocol_preset = QPushButton('💾 Save As Preset...')
        btn_save_protocol_preset.setToolTip('Save the current protocol as a named preset for quick switching')
        btn_save_protocol_preset.clicked.connect(self._save_protocol_preset)
        protocol_preset_btn_row.addWidget(btn_save_protocol_preset)
        btn_delete_protocol_preset = QPushButton('🗑️ Delete Preset')
        btn_delete_protocol_preset.setToolTip('Remove the selected protocol preset')
        btn_delete_protocol_preset.clicked.connect(self._delete_protocol_preset)
        protocol_preset_btn_row.addWidget(btn_delete_protocol_preset)
        layout.addRow('', protocol_preset_btn_row)
        self.protocol_edit = QTextEdit()
        self.protocol_edit.setPlaceholderText('(Empty — uses built-in default protocol)\n\nOverride example:\nYou are KokertechAI, the Executive Core for Jacques.\nCRITICAL COGNITIVE PROTOCOL: ...')
        self.protocol_edit.setMinimumHeight(120)
        self.protocol_edit.setMaximumHeight(200)
        self.protocol_edit.textChanged.connect(self._on_protocol_text_changed)
        self.protocol_edit.textChanged.connect(self._validate_protocol)
        saved_protocol = CONFIG.get('protocol_prompt', '')
        if saved_protocol:
            self.protocol_edit.setPlainText(saved_protocol)
        layout.addRow('Protocol Instructions:', self.protocol_edit)
        self.protocol_warning = QLabel()
        self.protocol_warning.setWordWrap(True)
        self.protocol_warning.setStyleSheet('font-size: 9pt; color: #FBBF24; padding: 4px; background: #1A1500; border: 1px solid #FBBF24; border-radius: 3px;')
        self.protocol_warning.hide()
        layout.addRow('', self.protocol_warning)
        btn_reset = QPushButton('↩ Reset to Default')
        btn_reset.clicked.connect(self._reset_protocol)
        layout.addRow('', btn_reset)
        group.setLayout(layout)
        return group

    def _create_mode_group(self):
        group = QGroupBox('Response Mode')
        layout = QFormLayout()
        self.chk_freeform = QCheckBox('Freeform mode — AI responds naturally without XML structure')
        self.chk_freeform.setChecked(CONFIG.get('freeform_mode', False))
        self.chk_freeform.stateChanged.connect(self._validate_protocol)
        layout.addRow('', self.chk_freeform)
        self.chk_mock = QCheckBox('Mock mode — simulated responses (no AI calls)')
        self.chk_mock.setChecked(CONFIG.get('mock_mode', False))
        layout.addRow('', self.chk_mock)
        group.setLayout(layout)
        return group

    def _create_hotkey_group(self):
        group = QGroupBox("Global Hotkeys")
        layout = QFormLayout()
        self.hotkey_trigger_input = QLineEdit(CONFIG.get("trigger_hotkey", "ctrl+alt+a"))
        self.hotkey_trigger_input.setPlaceholderText("e.g. ctrl+alt+a")
        layout.addRow("AI Trigger:", self.hotkey_trigger_input)
        self.hotkey_read_input = QLineEdit(CONFIG.get("read_hotkey", "ctrl+alt+s"))
        self.hotkey_read_input.setPlaceholderText("e.g. ctrl+alt+s")
        layout.addRow("Read Clipboard:", self.hotkey_read_input)
        self.hotkey_voice_input = QLineEdit(CONFIG.get("voice_hotkey", "ctrl+alt+v"))
        self.hotkey_voice_input.setPlaceholderText("e.g. ctrl+alt+v")
        layout.addRow("Voice Input:", self.hotkey_voice_input)
        btn_apply = QPushButton("Apply Hotkeys")
        btn_apply.clicked.connect(self._apply_hotkeys)
        layout.addRow("", btn_apply)
        group.setLayout(layout)
        return group

    def _create_registry_group(self):
        group = QGroupBox('🧪 Model Registry — Test & Benchmark')
        layout = QFormLayout()
        self.registry_provider_combo = QComboBox()
        self.registry_provider_combo.addItem('Local GGUF')
        layout.addRow('Provider:', self.registry_provider_combo)
        registry_btn_row = QHBoxLayout()
        btn_list_models = QPushButton('📋 List Models')
        btn_list_models.clicked.connect(self._list_registry_models)
        registry_btn_row.addWidget(btn_list_models)
        btn_test_model = QPushButton('🔌 Test')
        btn_test_model.clicked.connect(self._test_registry_model)
        registry_btn_row.addWidget(btn_test_model)
        btn_bench_model = QPushButton('⚡ Benchmark')
        btn_bench_model.clicked.connect(self._benchmark_registry_model)
        registry_btn_row.addWidget(btn_bench_model)
        btn_compare_models = QPushButton('🔬 Compare All')
        btn_compare_models.setToolTip('Benchmark all listed models side-by-side')
        btn_compare_models.clicked.connect(self._compare_registry_models)
        registry_btn_row.addWidget(btn_compare_models)
        layout.addRow('', registry_btn_row)
        self.registry_output = QLabel('Select a provider and click List, Test, or Benchmark.')
        self.registry_output.setWordWrap(True)
        self.registry_output.setStyleSheet('font-size: 9pt; color: #9CA3AF; padding: 4px; background: #1C1C1C; border: 1px solid #333;')
        self.registry_output.setMinimumHeight(60)
        history_header = QHBoxLayout()
        history_title = QLabel('📊 Benchmark History')
        history_title.setStyleSheet('font-weight: bold; color: #F59E0B; font-size: 9pt;')
        history_header.addWidget(history_title)
        history_header.addStretch()
        btn_refresh_history = QPushButton('🔄 Refresh')
        btn_refresh_history.setFixedWidth(80)
        btn_refresh_history.setToolTip('Refresh benchmark results from session journal')
        btn_refresh_history.clicked.connect(self._refresh_benchmark_history)
        history_header.addWidget(btn_refresh_history)
        layout.addRow('', history_header)
        self._benchmark_history_count = QLabel('')
        self._benchmark_history_count.setStyleSheet('font-size: 8pt; color: #6B7280; padding-left: 4px;')
        layout.addRow('', self._benchmark_history_count)
        self._benchmark_history_display = QLabel('No benchmark history yet — run a Benchmark or Compare All first.')
        self._benchmark_history_display.setWordWrap(True)
        self._benchmark_history_display.setStyleSheet('font-size: 8pt; color: #6B7280; padding: 4px; background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 3px;')
        self._benchmark_history_display.setMinimumHeight(50)
        self._benchmark_history_display.setMaximumHeight(120)
        layout.addRow('', self._benchmark_history_display)
        group.setLayout(layout)
        return group

    def _create_provider_control_group(self):
        group = QGroupBox('🤖 AI Provider Control')
        layout = QFormLayout()
        btn_k = QPushButton('🛑 Kill Provider Process Now')
        btn_k.clicked.connect(self._kill_provider_process)
        layout.addRow('', btn_k)
        group.setLayout(layout)
        return group

    def _create_prefs_group(self):
        group = QGroupBox('Window & Logging')
        layout = QFormLayout()
        self.chk_always_on_top = QCheckBox('Keep dashboard always on top')
        self.chk_always_on_top.setChecked(CONFIG.get('always_on_top', False))
        self.chk_always_on_top.toggled.connect(self._on_always_on_top_toggle)
        layout.addRow('', self.chk_always_on_top)
        self.chk_debug_logging = QCheckBox('Enable verbose debug logging')
        self.chk_debug_logging.setChecked(CONFIG.get('debug_logging', False))
        self.chk_autosave_logs = QCheckBox('Autosave conversation log')
        self.chk_autosave_logs.setChecked(CONFIG.get('autosave_logs', True))
        layout.addRow('', self.chk_autosave_logs)

        # ── Desktop notifications master toggle ──
        self.chk_notifications = QCheckBox('Enable desktop toast notifications')
        self.chk_notifications.setChecked(CONFIG.get('notifications_enabled', True))
        self.chk_notifications.toggled.connect(self._on_notifications_toggle)
        layout.addRow('', self.chk_notifications)

        # ── Per-resource notification toggles (indented under master) ──
        self._resource_notif_header = QLabel('⚙️ Resource Monitoring')
        self._resource_notif_header.setStyleSheet(
            'font-size: 9pt; font-weight: bold; color: #9CA3AF; padding: 4px 0 0 0;'
        )
        layout.addRow('', self._resource_notif_header)

        self.chk_vram_notifications = QCheckBox('    🖥️ VRAM threshold alerts')
        self.chk_vram_notifications.setChecked(CONFIG.get('vram_notifications_enabled', True))
        self.chk_vram_notifications.toggled.connect(self._on_vram_notifications_toggle)
        layout.addRow('', self.chk_vram_notifications)
        self.chk_ram_notifications = QCheckBox('    💾 RAM threshold alerts')
        self.chk_ram_notifications.setChecked(CONFIG.get('ram_notifications_enabled', True))
        self.chk_ram_notifications.toggled.connect(self._on_ram_notifications_toggle)
        layout.addRow('', self.chk_ram_notifications)
        self.chk_disk_notifications = QCheckBox('    💽 Disk threshold alerts')
        self.chk_disk_notifications.setChecked(CONFIG.get('disk_notifications_enabled', True))
        self.chk_disk_notifications.toggled.connect(self._on_disk_notifications_toggle)
        layout.addRow('', self.chk_disk_notifications)

        # ── Resource threshold percentages (CONFIG-driven, v0.22.11) ──
        # Warning/critical % consumed by app_lifecycle.py monitors
        # (update_ram_monitor / update_disk_monitor / update_vram) and the
        # Health-tab gauge coloring (tabs/health_tab.py). User-editable from
        # the UI instead of only app_settings.json. Each spinbox writes CONFIG
        # on change (runtime monitors + Health tab pick it up on next tick);
        # durability is provided by the main SAVE ALL CONFIGURATIONS button
        # (same pattern as spin_chat_history_debounce). Values are set BEFORE
        # connecting valueChanged so the setValue() emission from a CONFIG
        # value differing from the QSpinBox default (0) does not fire during
        # widget construction.
        self._resource_threshold_header = QLabel('📏 Thresholds (%)')
        self._resource_threshold_header.setStyleSheet(
            'font-size: 9pt; font-weight: bold; color: #9CA3AF; padding: 4px 0 0 0;'
        )
        layout.addRow('', self._resource_threshold_header)

        self.spin_ram_warning_pct = QSpinBox()
        self.spin_ram_warning_pct.setRange(1, 99)
        self.spin_ram_warning_pct.setSuffix(' %')
        self.spin_ram_warning_pct.setValue(
            _coerce_pct(CONFIG.get('ram_warning_pct', 80), 80)
        )
        self.spin_ram_warning_pct.setToolTip('RAM warning threshold — bar turns amber at or above this %. Keep below the critical value.')
        self.spin_ram_warning_pct.valueChanged.connect(
            lambda v: self._on_threshold_pct_changed('ram_warning_pct', v)
        )
        layout.addRow('RAM warning:', self.spin_ram_warning_pct)

        self.spin_ram_critical_pct = QSpinBox()
        self.spin_ram_critical_pct.setRange(1, 100)
        self.spin_ram_critical_pct.setSuffix(' %')
        self.spin_ram_critical_pct.setValue(
            _coerce_pct(CONFIG.get('ram_critical_pct', 95), 95)
        )
        self.spin_ram_critical_pct.setToolTip('RAM critical threshold — bar turns red at or above this %')
        self.spin_ram_critical_pct.valueChanged.connect(
            lambda v: self._on_threshold_pct_changed('ram_critical_pct', v)
        )
        layout.addRow('RAM critical:', self.spin_ram_critical_pct)

        self.spin_disk_warning_pct = QSpinBox()
        self.spin_disk_warning_pct.setRange(1, 99)
        self.spin_disk_warning_pct.setSuffix(' %')
        self.spin_disk_warning_pct.setValue(
            _coerce_pct(CONFIG.get('disk_warning_pct', 85), 85)
        )
        self.spin_disk_warning_pct.setToolTip('Disk warning threshold — bar turns amber at or above this %. Keep below the critical value.')
        self.spin_disk_warning_pct.valueChanged.connect(
            lambda v: self._on_threshold_pct_changed('disk_warning_pct', v)
        )
        layout.addRow('Disk warning:', self.spin_disk_warning_pct)

        self.spin_disk_critical_pct = QSpinBox()
        self.spin_disk_critical_pct.setRange(1, 100)
        self.spin_disk_critical_pct.setSuffix(' %')
        self.spin_disk_critical_pct.setValue(
            _coerce_pct(CONFIG.get('disk_critical_pct', 95), 95)
        )
        self.spin_disk_critical_pct.setToolTip('Disk critical threshold — bar turns red at or above this %')
        self.spin_disk_critical_pct.valueChanged.connect(
            lambda v: self._on_threshold_pct_changed('disk_critical_pct', v)
        )
        layout.addRow('Disk critical:', self.spin_disk_critical_pct)

        self.spin_vram_warning_pct = QSpinBox()
        self.spin_vram_warning_pct.setRange(1, 99)
        self.spin_vram_warning_pct.setSuffix(' %')
        self.spin_vram_warning_pct.setValue(
            _coerce_pct(CONFIG.get('vram_warning_pct', 88), 88)
        )
        self.spin_vram_warning_pct.setToolTip('VRAM warning threshold — bar turns amber at or above this %. Keep below the critical value.')
        self.spin_vram_warning_pct.valueChanged.connect(
            lambda v: self._on_threshold_pct_changed('vram_warning_pct', v)
        )
        layout.addRow('VRAM warning:', self.spin_vram_warning_pct)

        self.spin_vram_critical_pct = QSpinBox()
        self.spin_vram_critical_pct.setRange(1, 100)
        self.spin_vram_critical_pct.setSuffix(' %')
        self.spin_vram_critical_pct.setValue(
            _coerce_pct(CONFIG.get('vram_critical_pct', 95), 95)
        )
        self.spin_vram_critical_pct.setToolTip('VRAM critical threshold — bar turns red at or above this %')
        self.spin_vram_critical_pct.valueChanged.connect(
            lambda v: self._on_threshold_pct_changed('vram_critical_pct', v)
        )
        layout.addRow('VRAM critical:', self.spin_vram_critical_pct)

        # === Chat-history persistence (K1+K2 delivery) ===
        # Reactive UX: toggling the checkbox immediately updates CONFIG,
        # persists via save_settings(), refreshes the sidebar chat-history
        # badge, and offers to delete a stale on-disk file when toggling
        # OFF. See SettingsTabMixin._on_chat_history_toggle docstring
        # for the full order of operations.
        self.chk_chat_history = QCheckBox('Persist chat history across restarts')
        # Set initial state BEFORE connecting the signal so the toggled()
        # emission from setChecked() (when CONFIG['chat_history_enabled']
        # differs from QCheckBox default of False) does NOT trigger
        # _on_chat_history_toggle during widget construction.
        self.chk_chat_history.setChecked(CONFIG.get('chat_history_enabled', True))
        self.chk_chat_history.toggled.connect(self._on_chat_history_toggle)
        layout.addRow('', self.chk_chat_history)

        # Chat-history save debounce (ms). Range 0-60000 ms:
        #   - 0 = write immediately (no debounce)
        #   - 500 = default UX balance (matches module constant)
        #   - 60000 = generous upper bound to prevent UI freezes
        # See SettingsTabMixin._reset_chat_history_debounce docstring
        # for the reset-button contract.
        self.spin_chat_history_debounce = QSpinBox()
        self.spin_chat_history_debounce.setRange(0, 60000)
        self.spin_chat_history_debounce.setSingleStep(50)
        self.spin_chat_history_debounce.setSuffix(' ms')
        # Set initial value BEFORE connecting valueChanged so the
        # valueChanged() emission from setValue() (when CONFIG['chat_history_debounce_ms']
        # differs from QSpinBox default of 0) does NOT fire the lambda
        # during widget construction. Also documented: this lambda writes
        # CONFIG only (no save_settings call); durability is provided by
        # the main SAVE ALL CONFIGURATIONS button. Matches the
        # spin_keepalive_max_ticks pattern at line 478 of this file.
        self.spin_chat_history_debounce.setValue(CONFIG.get('chat_history_debounce_ms', 500))
        self.spin_chat_history_debounce.setToolTip(
            f'How long to wait before writing data/chat_history.json to disk after the last response (0-{60000} ms). Default: 500 ms.'
        )
        self.spin_chat_history_debounce.valueChanged.connect(
            lambda v: __import__('config').CONFIG.__setitem__('chat_history_debounce_ms', int(v))
        )
        self.btn_reset_chat_history_debounce = QPushButton('↩ Reset to 500 ms')
        self.btn_reset_chat_history_debounce.setToolTip(
            'Restore the chat-history debounce window to the safe 500 ms default and re-arm any pending save timer.'
        )
        self.btn_reset_chat_history_debounce.clicked.connect(self._reset_chat_history_debounce)
        debounce_row = QHBoxLayout()
        debounce_row.addWidget(self.spin_chat_history_debounce, 1)
        debounce_row.addWidget(self.btn_reset_chat_history_debounce, 0)
        debounce_container = QWidget()
        debounce_container.setLayout(debounce_row)
        layout.addRow('Chat-history save debounce:', debounce_container)
        # ── Keepalive watchdog cap (power-user tunable) ──
        # Read on the NEXT KeepaliveContext() instantiation; in-flight
        # keepalives keep their original cap until they finish.
        self.spin_keepalive_max_ticks = QSpinBox()
        self.spin_keepalive_max_ticks.setRange(1, 20)
        # Use the helper's default_max_ticks() so a malformed CONFIG
        # value (string, None, etc.) falls back gracefully to MAX_TICK_FIRES
        # instead of crashing _create_prefs_group() at load time.
        self.spin_keepalive_max_ticks.setValue(
            KeepaliveContext.default_max_ticks()
        )
        self.spin_keepalive_max_ticks.setToolTip(
            f'Maximum number of "Still X..." elapsed-time logs the\n'
            f'KeepaliveContext will emit before auto-aborting.\n'
            f'Default = {MAX_TICK_FIRES} ticks (~{MAX_TICK_FIRES * 30}s at 30s intervals).\n'
            f'Lower = faster UI-clear on stuck downloads.\n'
            f'Higher = more patience for slow networks.'
        )
        layout.addRow('Keepalive max ticks:', self.spin_keepalive_max_ticks)
        btn_row = QHBoxLayout()
        btn_a = QPushButton('📁 App Logs')
        btn_a.clicked.connect(self._open_app_logs_folder)
        btn_row.addWidget(btn_a)

        layout.addRow('Log Folders:', btn_row)
        group.setLayout(layout)
        return group

    def _create_identity_group(self):
        group = QGroupBox('Personal Identity Layer (user_identity.json)')
        layout = QFormLayout()
        self.id_name_input = QLineEdit(IDENTITY_CONFIG.get('name', ''))
        layout.addRow('Preferred Name:', self.id_name_input)
        self.id_tone_input = QLineEdit(IDENTITY_CONFIG.get('tone', ''))
        layout.addRow('Tone Instructions:', self.id_tone_input)
        self.id_pref_input = QLineEdit(IDENTITY_CONFIG.get('preferences', ''))
        layout.addRow('Core Preferences:', self.id_pref_input)
        self.id_bg_input = QTextEdit(IDENTITY_CONFIG.get('background', ''))
        self.id_bg_input.setMaximumHeight(60)
        layout.addRow('Background Context:', self.id_bg_input)
        group.setLayout(layout)
        return group

    def _create_backup_group(self):
        group = QGroupBox("Backup & Restore")
        layout = QVBoxLayout()
        self.backup_list = QComboBox()
        self.backup_list.setPlaceholderText("No backups found")
        self.backup_count_label = QLabel()
        top_row = QHBoxLayout()
        top_row.addWidget(self.backup_list, 1)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self._refresh_backups_list)
        top_row.addWidget(btn_refresh)
        layout.addLayout(top_row)
        layout.addWidget(self.backup_count_label)
        self.btn_restore_backup = QPushButton("Restore Backup")
        self.btn_restore_backup.clicked.connect(self._restore_backup)
        layout.addWidget(self.btn_restore_backup)
        self.backup_restore_status = QLabel()
        self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        layout.addWidget(self.backup_restore_status)
        x_row = QHBoxLayout()
        btn_e = QPushButton("Export Selected")
        btn_e.clicked.connect(self._export_backup)
        x_row.addWidget(btn_e)
        btn_i = QPushButton("Import Backup")
        btn_i.clicked.connect(self._import_backup)
        x_row.addWidget(btn_i)
        layout.addLayout(x_row)
        btn_int = QPushButton("Check Save Integrity")
        btn_int.clicked.connect(self._run_integrity_check)
        layout.addWidget(btn_int)
        group.setLayout(layout)
        return group

    def _deferred_init_settings_tab(self):
        """Deferred initialisation that runs after the event loop starts.

        Called once via QTimer.singleShot(0) at the end of
        create_settings_tab(). Performs network, thread, and I/O operations
        that depend on the event loop being active to avoid race conditions
        with QTimer.singleShot callbacks in background threads.
        """
        if getattr(self, '_shutting_down', False):
            return
        # 1. Load provider fields (triggers model refresh in background thread)
        self._update_provider_fields(provider_switched=False)

        # 2. Load TTS voices (creates VoiceOutput, potentially slow)
        try:
            from copilot_features import VoiceOutput, _discover_piper_models
            vo = getattr(self, "voice_output", None)
            if vo is None:
                vo = VoiceOutput()
            voices = vo.get_voices()
            if not voices:
                piper_models = _discover_piper_models()
                voices = [m["name"] for m in piper_models]

            self.tts_voice_combo.clear()
            if voices:
                self.tts_voice_combo.addItems(voices)
                saved_voice = CONFIG.get("tts_voice", "")
                if saved_voice:
                    vi = self.tts_voice_combo.findText(saved_voice)
                    if isinstance(vi, int) and vi >= 0:
                        self.tts_voice_combo.setCurrentIndex(vi)
                    else:
                        self.tts_voice_combo.setCurrentText(saved_voice)
                else:
                    lessac_idx = self.tts_voice_combo.findText("en_US-lessac-medium")
                    if isinstance(lessac_idx, int) and lessac_idx >= 0:
                        self.tts_voice_combo.setCurrentIndex(lessac_idx)
                    else:
                        self.tts_voice_combo.setCurrentIndex(0)
            else:
                self.tts_voice_combo.addItem("Microsoft David Desktop")
                self.tts_voice_combo.addItem("Microsoft Zira Desktop")
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
            logger.debug(f"TTS voice load error: {e}")
            if self.tts_voice_combo.count() == 0:
                self.tts_voice_combo.addItem("Microsoft David Desktop")
                self.tts_voice_combo.addItem("Microsoft Zira Desktop")

        # 3. Test Docker sandbox (contacts Docker daemon)
        self._test_docker_sandbox()

        # 4. Check provider connectivity status on initial load
        try:
            self._refresh_provider_status()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.debug(f"Provider status refresh failed: {e}")

    # Class-level provider maps used by multiple methods
    # Reverse: display label -> internal key
    _PROVIDER_MAP = {"Local LLM (GGUF)": "local_llm"}
    # Forward: internal key -> display label
    _PROVIDER_LABEL_MAP = {"local_llm": "Local LLM (GGUF)"}
    _PROVIDER_DEFAULTS = {
        "local_llm": {
            "model": "gemma-4-E2B-it-GGUF\\gemma-4-E2B-it-Q4_K_M.gguf",
            "label": "Local LLM (GGUF)",
            "is_local": True,
        },
    }

    def _create_local_llm_advanced_group(self):
        """Create advanced settings for the Local LLM provider (context length, GPU layers)."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        self.llm_n_ctx_spin = QSpinBox()
        self.llm_n_ctx_spin.setRange(512, 65536)
        self.llm_n_ctx_spin.setValue(CONFIG.get('llm_n_ctx', 4096))
        self.llm_n_ctx_spin.setToolTip('Context window size in tokens')
        layout.addWidget(QLabel('Context:'))
        layout.addWidget(self.llm_n_ctx_spin)

        self.llm_n_gpu_spin = QSpinBox()
        self.llm_n_gpu_spin.setRange(0, 200)
        self.llm_n_gpu_spin.setValue(CONFIG.get('llm_n_gpu_layers', 0))
        self.llm_n_gpu_spin.setToolTip('Number of layers to offload to GPU (0 = CPU only)')
        layout.addWidget(QLabel('GPU Layers:'))
        layout.addWidget(self.llm_n_gpu_spin)

        self.llm_n_threads_spin = QSpinBox()
        self.llm_n_threads_spin.setRange(0, 64)
        self.llm_n_threads_spin.setValue(CONFIG.get('llm_n_threads', 0) or 0)
        self.llm_n_threads_spin.setToolTip('CPU threads (0 = auto)')
        layout.addWidget(QLabel('Threads:'))
        layout.addWidget(self.llm_n_threads_spin)

        return widget

    def _browse_models_directory(self):
        """Open a directory picker for the models directory."""
        from PyQt6.QtWidgets import QFileDialog
        default_models_dir = os.path.join(WORKSPACE_DIR, 'models')
        current = self.models_dir_input.text().strip() or CONFIG.get('models_dir', default_models_dir)
        if not os.path.isdir(current):
            current = os.path.dirname(current) if os.path.exists(os.path.dirname(current)) else WORKSPACE_DIR
        directory = QFileDialog.getExistingDirectory(
            self, 'Select Models Directory', current)
        if directory:
            self.models_dir_input.setText(directory)
            CONFIG['models_dir'] = directory
            self._refresh_model_dropdown()

    def _update_provider_fields(self, provider_switched=False):
        """Update the local LLM fields (models directory, advanced settings).
        Populates fields from CONFIG if available, falling back to defaults.
        Also triggers a model list refresh.

        Parameters
        ----------
        provider_switched : bool
            True when called from _on_provider_changed (always refresh to default).
            False on initial load or backup restore (preserve saved config).
        """
        provider = self.provider_combo.currentText()
        provider_key = self._PROVIDER_MAP.get(provider, "local_llm")
        defaults = self._PROVIDER_DEFAULTS.get(provider_key, {})
        is_local = defaults.get("is_local", False)

        # Show/hide local LLM fields
        self._local_llm_fields.setVisible(is_local)

        if is_local:
            # ── Local LLM: show models dir fields ──
            saved_dir = CONFIG.get("models_dir", r"C:\KokertechAI\models")
            if provider_switched:
                self.models_dir_input.setText(CONFIG.get("models_dir", saved_dir))
            else:
                self.models_dir_input.setText(
                    CONFIG.get("models_dir", saved_dir))
            self.models_dir_input.setPlaceholderText(saved_dir)

            # Advanced fields
            self.llm_n_ctx_spin.setValue(CONFIG.get('llm_n_ctx', 4096))
            self.llm_n_gpu_spin.setValue(CONFIG.get('llm_n_gpu_layers', 0))
            self.llm_n_threads_spin.setValue(CONFIG.get('llm_n_threads', 0) or 0)


        # Refresh model dropdown for this provider
        self._refresh_model_dropdown()

        # Set the current model text from config
        saved_model = CONFIG.get("model_name", "")
        if is_local:
            # For local LLM: use model_file for model name display
            saved_model = CONFIG.get("model_file", "") or saved_model
        if saved_model:
            idx = self.model_combo.findText(saved_model)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            else:
                self.model_combo.setCurrentText(saved_model)

    def _refresh_provider_status(self):
        """Check the selected provider connectivity and update the status indicator.
        Checks if the local model file exists on disk.
        """
        import time
        now = time.time()
        last = getattr(self, '_last_provider_status_check', 0)
        if now - last < 30:
            return
        self._last_provider_status_check = now

        provider = self.provider_combo.currentText()
        defaults = self._PROVIDER_DEFAULTS.get("local_llm", {})
        self.provider_status_indicator.setText("⏳")

        # Local LLM: check if model file exists on disk
        def _check_local():
            model_path = CONFIG.get("model_file", "")
            models_dir = CONFIG.get("models_dir", r"C:\KokertechAI\models")
            if model_path and not os.path.isabs(model_path):
                resolved = os.path.join(models_dir, model_path)
            else:
                resolved = model_path
            exists = os.path.isfile(resolved) if resolved else False
            if exists:
                size_gb = os.path.getsize(resolved) / (1024**3)
                label = f"Ready ({size_gb:.1f}GB)"
                icon, color = "\U0001f7e2", "#10B981"
            else:
                default_file = defaults.get("model", "")
                if default_file:
                    label = "Model not found"
                else:
                    label = "No model selected"
                icon, color = "\U0001f534", "#EF4444"
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self._set_provider_status(icon, label, color, provider))
        threading.Thread(target=_check_local, daemon=True).start()

    def _set_provider_status(self, icon, label, color, provider):
        """Update provider status indicator on the main thread.
        Guards against shutdown and destroyed widgets (same pattern as _set_provider_badge).
        """
        if getattr(self, '_shutting_down', False):
            return
        try:
            self.provider_status_indicator.setToolTip(f"{provider}: {label}")
            self.provider_status_indicator.setStyleSheet(
                f"font-size: 8pt; color: {color}; padding-left: 4px;")
            self.provider_status_indicator.setText(f"{icon} {label}")
        except RuntimeError:
            pass

    def _on_provider_changed(self):
        """Handle provider dropdown change."""
        # restore-time setCurrentIndex (v0.22.15): sync restores provider
        # fields explicitly — skip the redundant refresh + audit here
        if getattr(self, '_syncing', False):
            return
        self._update_provider_fields(provider_switched=True)
        self._refresh_provider_status()
        self.context.log("🔄 Provider settings reloaded")

    def _on_model_changed(self, model_name):
        """Handle model combo selection change -- hot-swap model.

        Sprint 15: When the user picks a different model from the dropdown,
        trigger an immediate hot_swap_model via the controller so the new
        model loads without restarting the application.

        Shows loading feedback via the provider status indicator and
        reverts to the previous model on failure.
        """
        model_name = (model_name or "").strip()
        if not model_name:
            return
        # Skip during initial population or if we're programmatically updating
        if getattr(self, "_suppress_model_swap", False):
            return
        ctrl = getattr(self, "controller", None)
        if ctrl is None or not hasattr(ctrl, "hot_swap_model"):
            return
        # Don't swap if the model is already the active one
        current = getattr(ctrl, "provider", None)
        if current is not None:
            current_path = getattr(current, "_current_model_path", "")
            if current_path and os.path.basename(current_path) == model_name:
                return
        # Disable Apply button during swap to prevent double-clicks
        btn = getattr(self, "_btn_apply_model", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText('⏳ Applying…')

        # Show loading feedback
        self._set_provider_status("⏳", f"Loading {model_name}...", "#FBBF24", "Local LLM")
        self.context.log(f"🔄 Hot-swapping model to {model_name}")

        def _do_swap():
            from PyQt6.QtCore import QTimer
            try:
                result = ctrl.hot_swap_model(model_name)
                if result.get("ok"):
                    load_ms = result.get("load_time_ms", 0)
                    n_ctx = result.get("n_ctx", 0)
                    QTimer.singleShot(0, lambda: self._on_model_swap_success(model_name, load_ms, n_ctx))
                else:
                    error = result.get("error", "unknown error")
                    QTimer.singleShot(0, lambda: self._on_model_swap_failure(model_name, error))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str: self._on_model_swap_failure(model_name, err))

        threading.Thread(target=_do_swap, daemon=True).start()

    def _on_model_swap_success(self, model_name, load_ms, n_ctx):
        """Handle successful model hot-swap on the main thread."""
        if getattr(self, "_shutting_down", False):
            return
        btn = getattr(self, "_btn_apply_model", None)
        if btn is not None:
            btn.setText('⚡ Apply')
            btn.setEnabled(True)
        self._set_provider_status("🟢", f"Online ({model_name})", "#10B981", "Local LLM")
        self.context.log(f"✅ Model hot-swap complete: {model_name} ({load_ms}ms, n_ctx={n_ctx})")

    def _on_model_swap_failure(self, model_name, error):
        """Handle failed model hot-swap on the main thread."""
        if getattr(self, "_shutting_down", False):
            return
        btn = getattr(self, "_btn_apply_model", None)
        if btn is not None:
            btn.setText('⚡ Apply')
            btn.setEnabled(True)
        self._set_provider_status("🔴", f"Swap failed: {error[:40]}", "#EF4444", "Local LLM")
        self.context.log(f"❌ Model hot-swap failed: {model_name} -- {error}")
        # Revert the combo to the previous model
        # Use try/finally so _suppress_model_swap can never get stuck True
        self._suppress_model_swap = True
        try:
            ctrl = getattr(self, "controller", None)
            if ctrl is not None:
                provider = getattr(ctrl, "provider", None)
                if provider is not None:
                    current_path = getattr(provider, "_current_model_path", "")
                    if current_path:
                        current_name = os.path.basename(current_path)
                        idx = self.model_combo.findText(current_name)
                        if idx >= 0:
                            self.model_combo.setCurrentIndex(idx)
                        else:
                            self.model_combo.setCurrentText(current_name)
        finally:
            self._suppress_model_swap = False

    def _on_vision_model_changed(self, model_name):
        """Save the vision model to CONFIG with immediate feedback."""
        if getattr(self, "_shutting_down", False):
            return
        model_name = (model_name or "").strip()
        # Skip if model has not actually changed
        if model_name == CONFIG.get("vision_model", ""):
            return
        btn = getattr(self, "_btn_apply_vision", None)
        if btn is not None:
            btn.setEnabled(False)
            btn.setText('⏳ Applying…')
        CONFIG["vision_model"] = model_name
        self.context.log(f'🎯 Vision model set to: {model_name or "(main model)"}')
        if btn is not None:
            btn.setText('⚡ Apply')
            btn.setEnabled(True)


    def _on_theme_changed(self, theme_name):
        """Live-preview the selected theme immediately when changed."""
        if getattr(self, '_syncing', False):
            return  # restore-time setCurrentIndex (v0.22.15): sync applies the
            # theme explicitly at its end — skip the double apply here
        if hasattr(self, 'apply_theme'):
            self.apply_theme(theme_name)
        if hasattr(self, 'theme_swatch'):
            self._update_theme_swatch()

    def _update_theme_swatch(self):
        """Update the theme color swatch preview bar."""
        theme_name = self.setting_theme_combo.currentText()
        all_themes = get_all_themes()
        t = all_themes.get(theme_name, {})
        if t:
            # Build a gradient-like bar using accent1, accent2, and text colors
            c1 = t.get("accent1", "#374151")
            c2 = t.get("accent2", "#10B981")
            c3 = t.get("text", "#E0E0E0")
            self.theme_swatch.setStyleSheet(
                f"background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
                f"stop:0 {c1}, stop:0.5 {c2}, stop:1 {c3}); "
                f"border: 1px solid {t.get('text_tab', '#555')}; "
                f"border-radius: 3px;"
            )

    # ----------------------------------------------------------------
    # PERSONA EVENT HANDLERS
    # ----------------------------------------------------------------
    # ----------------------------------------------------------------
    # PROTOCOL PRESET HANDLERS
    # ----------------------------------------------------------------
    def _seed_builtin_protocol_presets(self):
        """Seed built-in protocol presets on first run when none exist.

        Called once from create_settings_tab() before _rebuild_protocol_presets().
        Safe to call multiple times — only seeds when protocol_presets is empty.
        Does NOT call save_settings() — defers persistence to the next explicit save.
        """
        presets = CONFIG.get("protocol_presets", {})
        if not presets and BUILTIN_PROTOCOL_TEMPLATES:
            CONFIG["protocol_presets"] = dict(BUILTIN_PROTOCOL_TEMPLATES)

    def _rebuild_protocol_presets(self):
        """Rebuild the protocol preset combo from CONFIG."""
        presets = CONFIG.get("protocol_presets", {})
        current = CONFIG.get("protocol_prompt", "")
        self.protocol_preset_combo.blockSignals(True)
        self.protocol_preset_combo.clear()
        self.protocol_preset_combo.addItem("Custom...", "__custom__")
        for name in sorted(presets.keys()):
            self.protocol_preset_combo.addItem(name, name)
        # Try to match current protocol to a preset
        matched = False
        if current:
            for name, content in presets.items():
                if content == current:
                    idx = self.protocol_preset_combo.findText(name)
                    if idx >= 0:
                        self.protocol_preset_combo.setCurrentIndex(idx)
                        matched = True
                        break
        if not matched:
            self.protocol_preset_combo.setCurrentIndex(0)
        self.protocol_preset_combo.blockSignals(False)
        # Update preset count label
        count = len(presets)
        if count:
            self.protocol_preset_count.setText(f"{count} preset{'s' if count != 1 else ''}")
        else:
            self.protocol_preset_count.setText("")

    def _on_protocol_preset_changed(self, index):
        """Load the selected preset into the protocol editor."""
        name = self.protocol_preset_combo.currentData()
        if name == "__custom__" or name is None:
            return
        presets = CONFIG.get("protocol_presets", {})
        content = presets.get(name, "")
        if content:
            self.protocol_edit.blockSignals(True)
            self.protocol_edit.setPlainText(content)
            self.protocol_edit.blockSignals(False)
            self._validate_protocol()
            self.context.log(f"📜 Loaded protocol preset: {name}")

    def _on_protocol_text_changed(self):
        """When user manually edits the protocol, switch combo to 'Custom...'."""
        if getattr(self, '_shutting_down', False):
            return
        if self.protocol_preset_combo.currentIndex() == 0:
            return  # Already on Custom..., no need to switch
        self.protocol_preset_combo.blockSignals(True)
        self.protocol_preset_combo.setCurrentIndex(0)
        self.protocol_preset_combo.blockSignals(False)

    def _save_protocol_preset(self):
        """Save the current protocol editor content as a named preset."""
        current = self.protocol_edit.toPlainText().strip()
        if not current:
            QMessageBox.information(
                self, "Empty Protocol",
                "The protocol editor is empty. Enter a protocol first."
            )
            return
        # Suggest a name based on the first line or a default
        first_line = current.split("\n")[0][:40].strip()
        default_name = first_line if first_line else "My Protocol"
        name, ok = QInputDialog.getText(
            self, "Save Protocol Preset",
            "Preset name:",
            text=default_name,
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        presets = dict(CONFIG.get("protocol_presets", {}))
        presets[name] = current
        CONFIG["protocol_presets"] = presets
        self._rebuild_protocol_presets()
        # Select the newly saved preset
        idx = self.protocol_preset_combo.findText(name)
        if idx >= 0:
            self.protocol_preset_combo.setCurrentIndex(idx)
        self.context.log(f"💾 Saved protocol preset: {name}")

    def _delete_protocol_preset(self):
        """Delete the currently selected protocol preset."""
        name = self.protocol_preset_combo.currentData()
        if name == "__custom__" or name is None:
            QMessageBox.information(
                self, "No Preset Selected",
                "Select a saved preset from the dropdown to delete."
            )
            return
        reply = QMessageBox.warning(
            self, "Confirm Delete",
            f"Delete protocol preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        presets = dict(CONFIG.get("protocol_presets", {}))
        if name in presets:
            del presets[name]
            CONFIG["protocol_presets"] = presets
            self._rebuild_protocol_presets()
            self.context.log(f"🗑️ Deleted protocol preset: {name}")

    def _validate_protocol(self):
        """Validate the protocol editor content and show a warning if critical
        instructions are missing for the current response mode.

        - Structured mode: warn if <thinking> or <final_output> tags are missing
        - Freeform mode:  warn if XML tags are present (they'll be ignored)
        - Empty protocol: no warning (built-in default will be used)

        NOTE: All widget accesses are guarded against deleted C++ objects
        (cross-test and cross-widget destruction ordering safety).
        """
        # Early return if dashboard is shutting down (prevents Qt crash during teardown)
        if getattr(self, '_shutting_down', False):
            return

        # Full guard: widget may have been destroyed during cross-test teardown
        protocol = ""
        if hasattr(self, 'protocol_edit'):
            try:
                protocol = self.protocol_edit.toPlainText().strip()
            except RuntimeError:
                protocol = ""
        if not protocol:
            try:
                if hasattr(self, 'protocol_warning'):
                    self.protocol_warning.hide()
            except RuntimeError:
                pass
            return

        freeform = False
        chk = getattr(self, 'chk_freeform', None)
        if chk is not None:
            try:
                freeform = chk.isChecked()
            except RuntimeError:
                freeform = False
        warnings = []

        if freeform:
            # Freeform mode: XML tags will be ignored
            if "<thinking>" in protocol or "<final_output>" in protocol:
                warnings.append(
                    "XML tags (<thinking>/<final_output>) won't be used in freeform mode"
                )
        else:
            # Structured mode: requires XML tags for proper parsing
            if "<thinking>" not in protocol:
                warnings.append(
                    "Missing <thinking> tag — AI won't show reasoning in structured mode"
                )
            if "<final_output>" not in protocol:
                warnings.append(
                    "Missing <final_output> tag — AI responses may not be parsed correctly"
                )

        if warnings:
            if hasattr(self, 'protocol_warning'):
                try:
                    self.protocol_warning.setText("⚠️  " + "\n".join(warnings))
                    self.protocol_warning.show()
                except RuntimeError:
                    pass
        else:
            try:
                if hasattr(self, 'protocol_warning'):
                    self.protocol_warning.hide()
            except RuntimeError:
                pass

    def _reset_protocol(self):
        """Reset the protocol editor to empty (use built-in default)."""
        self.protocol_edit.clear()
        self.protocol_preset_combo.blockSignals(True)
        self.protocol_preset_combo.setCurrentIndex(0)
        self.protocol_preset_combo.blockSignals(False)
        self._validate_protocol()
        self.context.log("↩ System protocol reset to default")

    def _pick_random_persona(self):
        """Pick a random persona from the loaded list."""
        import random
        personas = CONFIG.get("saved_personas", [])
        if personas:
            chosen = random.choice(personas)  # noqa: S311
            self.persona_combo.setCurrentText(chosen)
            self.context.log(f"🎲 Random persona selected: {chosen[:50]}...")

    def _save_persona_preset(self):
        """Save the current persona text as a new preset."""
        current = self.persona_combo.currentText().strip()
        if not current:
            return
        personas = list(CONFIG.get("saved_personas", []))
        if current not in personas:
            personas.append(current)
            CONFIG["saved_personas"] = personas
            # Rebuild combo items
            self.persona_combo.blockSignals(True)
            self.persona_combo.clear()
            self.persona_combo.addItems(personas)
            self.persona_combo.setCurrentText(current)
            self.persona_combo.blockSignals(False)
            self.context.log(f"💾 Saved persona preset ({len(personas)} total)")

    def _delete_persona_preset(self):
        """Delete the currently selected persona preset."""
        current = self.persona_combo.currentText().strip()
        personas = list(CONFIG.get("saved_personas", []))
        if current in personas:
            personas.remove(current)
            CONFIG["saved_personas"] = personas
            self.persona_combo.blockSignals(True)
            self.persona_combo.clear()
            if personas:
                self.persona_combo.addItems(personas)
                self.persona_combo.setCurrentText(personas[0])
            self.persona_combo.blockSignals(False)
            self.context.log(f"🗑️ Deleted persona preset '{current[:40]}...'")

    def _test_tts(self):
        """Test TTS with current settings using the dashboard's VoiceOutput instance."""
        vo = getattr(self, 'voice_output', None)
        if vo is None:
            try:
                from copilot_features import VoiceOutput
                vo = VoiceOutput()
                self.voice_output = vo
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Failed to create VoiceOutput: {e}")
        if vo is None:
            QMessageBox.warning(self, "TTS Unavailable",
                "TTS engine not initialized. Ensure pywin32 is installed.")
            return
        try:
            speed = self.tts_speed_spin.value()
            voice = self.tts_voice_combo.currentText().strip()
            vo.set_rate(speed)
            if voice:
                vo.set_voice(voice)
            vo.speak("This is a voice test. Text to speech is working correctly.")
            self.context.log(f"🔊 TTS test: speed={speed} WPM, voice='{voice}'")
        except (OSError, RuntimeError, ValueError, TypeError) as e:
            self.context.log(f"❌ TTS test error: {e}")

    # ----------------------------------------------------------------
    # WINDOW & LOGGING EVENT HANDLERS
    # ----------------------------------------------------------------
    def _on_notifications_toggle(self, checked):
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_syncing', False):
            return  # restore-time setChecked (v0.22.15): don't re-save/audit a sync
        CONFIG["notifications_enabled"] = bool(checked)
        try:
            save_settings()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.warning(f"Failed to save settings: {e}")
        state_str = "ON" if checked else "OFF"
        if hasattr(self, 'log_to_audit'):
            try:
                self.context.log(f"Desktop notifications: {state_str}")
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
                logger.debug(f"Audit log write failed (notifications): {e}")

    def _on_vram_notifications_toggle(self, checked):
        """Toggle VRAM threshold alerts on/off."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_syncing', False):
            return  # restore-time setChecked (v0.22.15): don't re-save/audit a sync
        CONFIG["vram_notifications_enabled"] = bool(checked)
        try:
            save_settings()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.warning(f"Failed to save settings: {e}")
        state_str = "ON" if checked else "OFF"
        if hasattr(self, 'log_to_audit'):
            try:
                self.context.log(f"VRAM notifications: {state_str}")
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
                logger.debug(f"Audit log write failed (VRAM notifications): {e}")

    def _on_ram_notifications_toggle(self, checked):
        """Toggle RAM threshold alerts on/off."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_syncing', False):
            return  # restore-time setChecked (v0.22.15): don't re-save/audit a sync
        CONFIG["ram_notifications_enabled"] = bool(checked)
        try:
            save_settings()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.warning(f"Failed to save settings: {e}")
        state_str = "ON" if checked else "OFF"
        if hasattr(self, 'log_to_audit'):
            try:
                self.context.log(f"RAM notifications: {state_str}")
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
                logger.debug(f"Audit log write failed (RAM notifications): {e}")

    def _on_disk_notifications_toggle(self, checked):
        """Toggle disk threshold alerts on/off."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_syncing', False):
            return  # restore-time setChecked (v0.22.15): don't re-save/audit a sync
        CONFIG["disk_notifications_enabled"] = bool(checked)
        try:
            save_settings()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.warning(f"Failed to save settings: {e}")
        state_str = "ON" if checked else "OFF"
        if hasattr(self, 'log_to_audit'):
            try:
                self.context.log(f"Disk notifications: {state_str}")
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
                logger.debug(f"Audit log write failed (Disk notifications): {e}")

    def _on_threshold_pct_changed(self, key, value):
        """Persist a Resource Monitoring threshold-spinbox change immediately.

        Mirrors the notification-toggle pattern: write CONFIG, then persist via
        config.save_settings() so an edited threshold survives an unexpected
        close without pressing SAVE ALL (v0.22.12).

        Enforces the warning <= critical invariant (v0.22.14): a warning value
        above its paired critical is clamped down to critical, and a critical
        value below its paired warning is clamped up to warning, so an inverted
        pair can never be persisted. The edited spinbox is re-synced (with
        signals blocked) so the UI shows the enforced value.
        """
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_syncing', False):
            return  # restore-time setValue (v0.22.15): don't re-save/audit a sync
        _val = int(value)
        _paired = self._THRESHOLD_PAIRS.get(key)
        if _paired is not None and _paired in CONFIG:
            if key.endswith("_warning_pct") and _val > CONFIG[_paired]:
                _val = CONFIG[_paired]
            elif key.endswith("_critical_pct") and _val < CONFIG[_paired]:
                _val = CONFIG[_paired]
        CONFIG[key] = _val
        try:
            save_settings()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.warning(f"Failed to save settings: {e}")
        if hasattr(self, 'log_to_audit'):
            _label = self._THRESHOLD_LABELS.get(key, key)
            try:
                self.context.log(f"{_label} threshold: {_val}%")
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
                logger.debug(f"Audit log write failed ({key}): {e}")
        _attr = "spin_" + key
        if _val != int(value) and hasattr(self, _attr):
            _spin = getattr(self, _attr)
            try:
                _spin.blockSignals(True)
                _spin.setValue(_val)
            finally:
                _spin.blockSignals(False)

    # Human-readable labels for threshold audit entries (v0.22.12).
    _THRESHOLD_LABELS = {
        "ram_warning_pct": "RAM warning",
        "ram_critical_pct": "RAM critical",
        "disk_warning_pct": "Disk warning",
        "disk_critical_pct": "Disk critical",
        "vram_warning_pct": "VRAM warning",
        "vram_critical_pct": "VRAM critical",
    }

    # Paired warning<->critical keys for the warning <= critical invariant
    # enforced in _on_threshold_pct_changed (v0.22.14).
    _THRESHOLD_PAIRS = {
        "ram_warning_pct": "ram_critical_pct",
        "ram_critical_pct": "ram_warning_pct",
        "disk_warning_pct": "disk_critical_pct",
        "disk_critical_pct": "disk_warning_pct",
        "vram_warning_pct": "vram_critical_pct",
        "vram_critical_pct": "vram_warning_pct",
    }

    def _on_always_on_top_toggle(self, checked):
        """Toggle the dashboard window's always-on-top state."""
        if getattr(self, '_shutting_down', False):
            return
        if getattr(self, '_syncing', False):
            return  # restore-time setChecked (v0.22.15): don't re-save/audit a sync
        CONFIG["always_on_top"] = checked
        save_settings()
        if hasattr(self, 'setWindowFlags'):
            from PyQt6.QtCore import Qt
            if checked:
                self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            else:
                self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowStaysOnTopHint)
            self.show()
        self.context.log(f"{'🔝' if checked else '⬇️'} Always on Top: {'ON' if checked else 'OFF'}")

    # --- Chat-history reactive controls (K1+K2 delivery) ---
    # These methods are wired up by the chat-history settings group
    # (chk.toggled.connect(...) + reset_button.clicked.connect(...)).
    # They satisfy the 20 previously-failing TestChatHistory tests.
    # Production UI elements are created in `_create_prefs_group`.
    # Shutdown-safe via `_shutting_down` guard (no writes during teardown).

    def _on_chat_history_toggle(self, checked: bool):
        """Reactive handler for Settings → chat-history checkbox toggles.

        Order matters and matches the test contract:
          1. Shutdown guard.
          2. ON→OFF: flush any pending debounce save BEFORE flipping CONFIG
             (so the final ON-state content lands on disk while still ON).
          3. Mutate ``CONFIG["chat_history_enabled"]``.
          4. ``save_settings()`` persists immediately so a crash mid-toggle
             can never lose the user's choice.
          5. Refresh the sidebar badge via ``_update_chat_history_badge()``
             (reactive UX: badge reflects the new state instantly).
          6. Audit log line.
          7. ON→OFF only: if a stale ``data/chat_history.json`` exists,
             prompt the user to delete it. Yes → call ``_clear_chat_history``
             + '🗑️...' audit line. No → 'ℹ️...' audit line.
        """
        if getattr(self, '_shutting_down', False):
            return

        # 1. ON→OFF: flush BEFORE config flip (test ordering: docstring says
        # 'BEFORE applying the toggle'; gated on _is_chat_history_enabled which
        # reads CONFIG — so flush while still ON).
        if not bool(checked):
            timer = getattr(self, '_chat_history_save_timer', None)
            if timer is not None and timer.isActive():
                if hasattr(self, '_flush_chat_history_save'):
                    try:
                        self._flush_chat_history_save()
                        if hasattr(self, 'log_to_audit') and callable(self.log_to_audit):
                            if hasattr(self, 'context') and self.context is not None:
                                self.context.log(
                                    "💾 Flushed pending save before toggle OFF"
                                )
                            else:
                                self.log_to_audit(
                                    "💾 Flushed pending save before toggle OFF"
                                )
                    except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                        logger.debug(f"Chat-history flush failed (must not block toggle): {e}")

        # 2. Mutate state, persist immediately.
        CONFIG["chat_history_enabled"] = bool(checked)
        try:
            save_settings()
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as e:
            logger.warning(f"Failed to save settings: {e}")

        # 3. Sidebar badge — reactive UX.
        if hasattr(self, '_update_chat_history_badge'):
            try:
                self._update_chat_history_badge()
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Chat-history badge update failed: {e}")

        # 4. Audit log.
        state_str = "ON" if checked else "OFF"
        if hasattr(self, 'log_to_audit') and callable(self.log_to_audit):
            try:
                if hasattr(self, 'context') and self.context is not None:
                    self.context.log(
                        f"💾 Chat history persistence: {state_str}"
                    )
                else:
                    self.log_to_audit(
                        f"💾 Chat history persistence: {state_str}"
                    )
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Audit log write failed (chat history): {e}")

        # 5. ON→OFF stale-file cleanup (file exists + user confirms).
        if not bool(checked):
            history_path = None
            if hasattr(self, '_chat_history_path'):
                try:
                    history_path = self._chat_history_path()
                except (OSError, RuntimeError, ValueError, TypeError, AttributeError):
                    history_path = None
            if history_path and os.path.exists(history_path):
                try:
                    reply = QMessageBox.question(
                        self,
                        "Stale chat history file",
                        f"A previously-saved chat_history.json sits dormant at:\n"
                        f"{history_path}\n\n"
                        f"Delete it now? (Yes = delete; No = keep.)\n"
                        f"Keeping is irreversible only on this prompt — use "
                        f"Settings → 'CAN  CHAT HISTORY' again later if you "
                        f"change your mind.",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,  # safe default
                    )
                    if reply == QMessageBox.StandardButton.Yes:
                        if hasattr(self, '_clear_chat_history'):
                            try:
                                self._clear_chat_history()
                            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                                logger.debug(f"Stale chat-history cleanup failed: {e}")
                        if hasattr(self, 'log_to_audit') and callable(self.log_to_audit):
                            try:
                                if hasattr(self, 'context') and self.context is not None:
                                    self.context.log(
                                        "🗑️ Stale chat history file deleted on toggle OFF"
                                    )
                                else:
                                    self.log_to_audit(
                                        "🗑️ Stale chat history file deleted on toggle OFF"
                                    )
                            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                                logger.debug(f"Audit log write failed (stale-file delete): {e}")
                    else:
                        if hasattr(self, 'log_to_audit') and callable(self.log_to_audit):
                            try:
                                if hasattr(self, 'context') and self.context is not None:
                                    self.context.log(
                                        "ℹ️ Stale chat history file kept (user declined deletion)"
                                    )
                                else:
                                    self.log_to_audit(
                                        "ℹ️ Stale chat history file kept (user declined deletion)"
                                    )
                            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                                logger.debug(f"Audit log write failed (stale-file keep): {e}")
                except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                    logger.debug(f"Stale chat-history prompt failed: {e}")

    def _reset_chat_history_debounce(self):
        """Reset the chat-history debounce spinbox to 500 ms + re-arm any
        in-flight timer + write ``CONFIG["chat_history_debounce_ms"]``.

        Wired up by the ↩ Reset button (created in ``_create_prefs_group``)
        next to ``spin_chat_history_debounce``. Three side-effects, in the
        order the tests assert them:
          1. ``spin_chat_history_debounce.setValue(500)`` — visible to user.
          2. ``CONFIG["chat_history_debounce_ms"] = 500`` — next schedule
             picks up the new value even before any further user action.
          3. If a pending save timer is active, ``timer.start(500)`` re-arms
             it so the next write fires 500 ms from NOW (not from the old,
             longer deadline).
          4. Audit log line.
        Shutdown-safe via ``_shutting_down`` guard (no writes during teardown).
        """
        if getattr(self, '_shutting_down', False):
            return

        DEFAULT_DEBOUNCE_MS = 500

        # 1. Spinbox UI sync.
        spin = getattr(self, 'spin_chat_history_debounce', None)
        if spin is not None:
            try:
                spin.setValue(DEFAULT_DEBOUNCE_MS)
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Debounce spinbox set failed: {e}")

        # 2. CONFIG write — takes effect on the next schedule immediately.
        CONFIG["chat_history_debounce_ms"] = DEFAULT_DEBOUNCE_MS

        # 3. Re-arm any in-flight timer (only if active; do NOT allocate a
        # new timer just to set its interval).
        timer = getattr(self, '_chat_history_save_timer', None)
        if timer is not None and timer.isActive():
            try:
                timer.start(DEFAULT_DEBOUNCE_MS)
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Debounce timer re-arm failed: {e}")

        # 4. Audit log.
        if hasattr(self, 'log_to_audit') and callable(self.log_to_audit):
            try:
                if hasattr(self, 'context') and self.context is not None:
                    self.context.log(
                        f"↩ Chat-history debounce reset to {DEFAULT_DEBOUNCE_MS} ms"
                    )
                else:
                    self.log_to_audit(
                        f"↩ Chat-history debounce reset to {DEFAULT_DEBOUNCE_MS} ms"
                    )
            except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as e:
                logger.debug(f"Audit log write failed (debounce reset): {e}")

    def _open_app_logs_folder(self):
        """Open the application logs folder in Explorer."""
        logs_dir = os.path.join(os.path.dirname(SETTINGS_PATH), "data", "logs")
        os.makedirs(logs_dir, exist_ok=True)
        try:
            os.startfile(logs_dir)  # noqa: S606
            self.context.log(f"📁 Opened logs folder: {logs_dir}")
        except (OSError, RuntimeError, ValueError) as e:
            self.context.log(f"❌ Could not open logs folder: {e}")

    # ----------------------------------------------------------------
    # MODEL REGISTRY — LIST / TEST / BENCHMARK
    # ----------------------------------------------------------------
    def _list_registry_models(self):
        """List available models for the selected provider."""
        import model_registry
        provider_key = self._PROVIDER_MAP.get(self.registry_provider_combo.currentText(), "local_llm")
        self.registry_output.setText(f"⏳ Querying {self.registry_provider_combo.currentText()} models...")

        def _do():
            try:
                models = model_registry.list_models(provider_key)
                from PyQt6.QtCore import QTimer
                if not models:
                    QTimer.singleShot(0, lambda: self.registry_output.setText(
                        f"⚠️ No models found for {self.registry_provider_combo.currentText()}.\n"
                        "Is the provider running?"))
                    return
                lines = [f"📋 {len(models)} model(s) on {self.registry_provider_combo.currentText()}:"]
                for m in models:
                    name = m.get("name", "?")
                    desc = m.get("description", "")
                    size = m.get("size_bytes", 0)
                    if size:
                        size_str = f" ({size // (1024**3)}GB)"
                    else:
                        size_str = ""
                    line = f"  • {name}{size_str}"
                    if desc:
                        line += f" — {desc}"
                    lines.append(line)
                QTimer.singleShot(0, lambda l=lines: self.registry_output.setText("\n".join(l)))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                from PyQt6.QtCore import QTimer
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str: self.registry_output.setText(f"❌ List failed: {err}"))

        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _test_registry_model(self):
        """Test connectivity to a model from the registry."""
        import model_registry
        from PyQt6.QtCore import QTimer
        provider_key = self._PROVIDER_MAP.get(self.registry_provider_combo.currentText(), "local_llm")

        # Use the active model from config
        model = CONFIG.get("model_name", "")
        if not model:
            self.registry_output.setText("⚠️ No model configured. Set a model in the provider fields above.")
            return

        self.registry_output.setText(f"⏳ Testing {model} on {self.registry_provider_combo.currentText()}...")

        def _do():
            result = model_registry.test_model(provider_key, model, timeout=15)
            if result.get("ok"):
                msg = (f"✅ Connection OK — {model}\n"
                       f"   Response: \"{result.get('response', '')}\"\n"
                       f"   Time: {result.get('response_time_ms', 0)}ms")
            else:
                msg = (f"❌ Test failed — {model}\n"
                       f"   Error: {result.get('error', 'Unknown')}\n"
                       f"   Time: {result.get('response_time_ms', 0)}ms")
            QTimer.singleShot(0, lambda m=msg: self.registry_output.setText(m))

        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _benchmark_registry_model(self):
        """Benchmark a model for speed."""
        import model_registry
        from PyQt6.QtCore import QTimer
        provider_key = self._PROVIDER_MAP.get(self.registry_provider_combo.currentText(), "local_llm")

        model = CONFIG.get("model_name", "")
        if not model:
            self.registry_output.setText("⚠️ No model configured. Set a model in the provider fields above.")
            return

        self.registry_output.setText(f"⚡ Benchmarking {model} on {self.registry_provider_combo.currentText()}...\n"
                                     f"   (Generating 20 numbers — may take up to 60s)")

        def _do():
            result = model_registry.benchmark_model(provider_key, model, timeout=90)
            model_registry.log_benchmark_result(result)
            if result.get("ok"):
                msg = (f"✅ Benchmark complete — {model}\n"
                       f"   Speed: {result.get('tokens_per_second', 0)} tok/s\n"
                       f"   Tokens: {result.get('token_count', 0)}\n"
                       f"   Time: {result.get('response_time_ms', 0)}ms")
            else:
                msg = (f"❌ Benchmark failed — {model}\n"
                       f"   Error: {result.get('error', 'Unknown')}")
            QTimer.singleShot(0, lambda m=msg: self.registry_output.setText(m))

        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _refresh_benchmark_history(self):
        """Refresh the benchmark history display from the session journal.

        Loads up to 20 most recent benchmark results using
        ``model_registry.get_benchmark_history()`` and displays them
        in a compact summary table.
        """
        import model_registry
        from PyQt6.QtCore import QTimer

        # Guard: prevent duplicate refreshes
        if getattr(self, '_benchmark_refreshing', False):
            return
        self._benchmark_refreshing = True

        def _do():
            try:
                history = model_registry.get_benchmark_history()
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                history = []

            def _update(entries):
                if getattr(self, '_shutting_down', False):
                    return
                try:
                    if not entries:
                        self._benchmark_history_display.setText(
                            "No benchmark history yet - run a Benchmark or Compare All first."
                        )
                        self._benchmark_history_display.setStyleSheet(
                            "font-size: 8pt; color: #6B7280; padding: 4px; "
                            "background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 3px;"
                        )
                        self._benchmark_history_count.setText("")
                        return

                    # Show up to 20 most recent entries, most recent first
                    shown = entries[:20]
                    lines = []
                    for e in shown:
                        model = e.get("model", "?")
                        tps = e.get("tokens_per_second", 0)
                        time_ms = e.get("response_time_ms", 0)
                        ok = e.get("ok", False)
                        icon = "✅" if ok else "❌"
                        if ok:
                            lines.append(
                                f"{icon} {model:<30} {tps:>7.1f} tok/s  {time_ms:>5}ms"
                            )
                        else:
                            err = e.get("error", "Unknown")
                            lines.append(
                                f"{icon} {model:<30} {'FAIL':>8}  - {err}"
                            )

                    self._benchmark_history_display.setText("\n".join(lines))
                    self._benchmark_history_display.setStyleSheet(
                        "font-size: 8pt; color: #D1D5DB; padding: 4px; "
                        "font-family: 'Consolas', monospace; "
                        "background: #1A1A1A; border: 1px solid #2A2A2A; border-radius: 3px;"
                    )
                    self._benchmark_history_count.setText(f"{len(entries)} total")
                except RuntimeError:
                    pass
                finally:
                    self._benchmark_refreshing = False

            QTimer.singleShot(0, lambda: _update(history))

        self._benchmark_history_display.setText("⏳ Loading history...")
        import threading
        threading.Thread(target=_do, daemon=True).start()

    def _compare_registry_models(self):
        """Benchmark all known models for the selected provider side-by-side (Sprint 6.3)."""
        import model_registry
        from PyQt6.QtCore import QTimer
        provider_key = self._PROVIDER_MAP.get(self.registry_provider_combo.currentText(), "local_llm")

        # Gather models from the provider
        self.registry_output.setText(f"🔬 Comparing all models on {self.registry_provider_combo.currentText()}...\n"
                                     f"   (Benchmarking each model — this may take a while)")

        def _do():
            try:
                models = model_registry.list_models(provider_key)
                if not models:
                    QTimer.singleShot(0, lambda: self.registry_output.setText(
                        f"⚠️ No models found for {self.registry_provider_combo.currentText()}.\n"
                        "Is the provider running?"))
                    return

                model_names = [m["name"] for m in models]
                results = model_registry.compare_models(provider_key, model_names, timeout=90)

                # Build comparison table
                lines = [f"🔬 Model Comparison — {self.registry_provider_combo.currentText()}",
                         f"{'─' * 55}",
                         f"{'Model':<30} {'Speed':>8} {'Tokens':>7} {'OK':>4}",
                         f"{'─' * 55}"]

                for r in results:
                    if r.get("ok"):
                        lines.append(
                            f"{r['model']:<30} {r['tokens_per_second']:>7.1f} tok/s {r['token_count']:>6}  ✅"
                        )
                    else:
                        lines.append(
                            f"{r['model']:<30} {'FAIL':>8} {'—':>7}  ❌"
                        )

                # Log results to journal
                for r in results:
                    model_registry.log_benchmark_result(r)

                QTimer.singleShot(0, lambda l=lines: self.registry_output.setText("\n".join(l)))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str: self.registry_output.setText(f"❌ Comparison failed: {err}"))

        import threading
        threading.Thread(target=_do, daemon=True).start()

    # ── Vision model auto-detection ────────────────────────────────────
    def _refresh_vision_models(self):
        """Auto-detect vision-capable models and populate the combo.
        """
        from PyQt6.QtCore import QTimer
        import model_registry
        from ai_base import _is_vision_model
        import threading

        def _do():
            try:
                models = model_registry.list_models("local_llm")
                model_names = [m.get("name", "") for m in models if m.get("name", "")]
                vision_models = [m for m in model_names if _is_vision_model(m)]
                QTimer.singleShot(0, lambda: self._populate_vision_combo(vision_models))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str: self.registry_output.setText(f"Vision refresh failed: {err}"))
        threading.Thread(target=_do, daemon=True).start()

    def _populate_vision_combo(self, all_models):
        """Filter models by vision capability and populate the combo.
        Runs on the main thread. Preserves current selection.
        """
        from ai_base import _is_vision_model

        saved = CONFIG.get("vision_model", "")
        # Find vision-capable models
        vision_models = [m for m in all_models if _is_vision_model(m)]

        self.vision_model_combo.blockSignals(True)
        current_text = self.vision_model_combo.currentText()
        self.vision_model_combo.clear()

        # Always offer the empty option (use default model)
        if vision_models:
            self.vision_model_combo.setPlaceholderText(
                f"{len(vision_models)} vision model(s) found"
            )
            self.vision_model_combo.addItems(vision_models)
        else:
            self.vision_model_combo.setPlaceholderText(
                "(No vision models detected)"
            )

        # Restore selection: saved config > previous text > empty
        restore = ""
        if saved and saved in vision_models:
            restore = saved
        elif current_text and current_text in vision_models:
            restore = current_text
        if restore:
            idx = self.vision_model_combo.findText(restore)
            if idx >= 0:
                self.vision_model_combo.setCurrentIndex(idx)
        self.vision_model_combo.blockSignals(False)
        self.context.log(
            f"👁️ Vision model refresh: {len(vision_models)} vision-capable model(s) detected"
        )

    def _refresh_model_dropdown(self):
        """Refresh the main model dropdown by scanning the models directory for .gguf files.
        Called from _update_provider_fields and the refresh button.
        """
        from PyQt6.QtCore import QTimer

        provider = self.provider_combo.currentText()
        provider_key = self._PROVIDER_MAP.get(provider, "local_llm")
        defaults = self._PROVIDER_DEFAULTS.get(provider_key, {})

        # ── Local LLM: scan directory for .gguf files ──
        models_dir = self.models_dir_input.text().strip() or CONFIG.get("models_dir", os.path.join(WORKSPACE_DIR, "models"))

        def _scan():
            from ai_base import LocalLLMProvider
            models = LocalLLMProvider.scan_models(models_dir)
            model_names = [m["name"] for m in models]
            model_names_with_path = [m["path"] for m in models]
            if model_names:
                QTimer.singleShot(0, lambda: self._update_main_model_combo_local(
                    model_names, model_names_with_path))
            else:
                default = defaults.get("model", "")
                names = [default] if default else []
                QTimer.singleShot(0, lambda: self._update_main_model_combo_local(names, names))

        threading.Thread(target=_scan, daemon=True).start()

    def _update_main_model_combo_local(self, display_names, full_paths):
        """Update the model combo with local GGUF model paths.
        Stores the full path in UserRole for persistence.
        """
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        if display_names:
            for display, path in zip(display_names, full_paths, strict=False):
                self.model_combo.addItem(display, path)

        # Restore selection from CONFIG
        saved_file = CONFIG.get("model_file", "")
        if saved_file:
            # Try matching by full path first, then by display name
            idx = self.model_combo.findData(saved_file)
            if idx < 0:
                idx = self.model_combo.findText(saved_file)
            if idx >= 0:
                self.model_combo.setCurrentIndex(idx)
            else:
                self.model_combo.setCurrentText(saved_file)
        elif display_names:
            self.model_combo.setCurrentIndex(0)
        self.model_combo.blockSignals(False)

    # Removed _update_main_model_combo — local LLM uses _update_main_model_combo_local

    def _kill_provider_process(self):
        """Kill the AI provider process."""
        import psutil
        from PyQt6.QtCore import QTimer

        provider = CONFIG.get("active_provider", "local_llm")
        targets = ["python.exe", "llama-server.exe"]

        def _do_kill():
            killed = []
            for proc in psutil.process_iter(["name", "pid"]):
                try:
                    if proc.info["name"] in targets:
                        proc.kill()
                        killed.append(proc.info["name"])
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            QTimer.singleShot(0, lambda: self._on_kill_result(killed, provider))

        threading.Thread(target=_do_kill, daemon=True).start()

    def _on_kill_result(self, killed, provider):
        """Called on main thread after kill attempt."""
        if killed:
            msg = f"🛑 Killed: {', '.join(killed)}"
            self.context.log(msg)
        else:
            msg = f"⚠️ No {provider} processes found"
            self.context.log(msg)

    def _test_docker_sandbox(self):
        """Test Docker sandbox availability and display status."""
        from PyQt6.QtCore import QTimer

        def _do_test():
            try:
                from docker_sandbox import is_docker_available
                available = is_docker_available()
                if available:
                    QTimer.singleShot(0, lambda: self.sandbox_status_label.setText(
                        "✅ Docker daemon running"))
                    QTimer.singleShot(0, lambda: self.sandbox_status_label.setStyleSheet(
                        "font-size: 9pt; color: #10B981;"))
                else:
                    QTimer.singleShot(0, lambda: self.sandbox_status_label.setText(
                        "⚠️ Docker not running (local fallback enabled)"))
                    QTimer.singleShot(0, lambda: self.sandbox_status_label.setStyleSheet(
                        "font-size: 9pt; color: #FBBF24;"))
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                err_str = str(e)
                QTimer.singleShot(0, lambda err=err_str: self.sandbox_status_label.setText(
                    f"❌ Error: {err}"))
                QTimer.singleShot(0, lambda: self.sandbox_status_label.setStyleSheet(
                    "font-size: 9pt; color: #EF4444;"))

        self.sandbox_status_label.setText("⏳ Testing...")
        self.sandbox_status_label.setStyleSheet("font-size: 9pt; color: #FBBF24;")
        threading.Thread(target=_do_test, daemon=True).start()

    def _open_web_server_browser(self):
        """Open the web server URL in the default system browser."""
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        port = self.web_port_spin.value()
        url = f"http://localhost:{port}"
        QDesktopServices.openUrl(QUrl(url))
        self.context.log(f"↗ Opened web server in browser: {url}")

    def _apply_hotkeys(self):
        """Apply hotkeys from the settings fields and save."""
        CONFIG["trigger_hotkey"] = self.hotkey_trigger_input.text().strip()
        CONFIG["read_hotkey"] = self.hotkey_read_input.text().strip()
        CONFIG["voice_hotkey"] = self.hotkey_voice_input.text().strip()
        # Forward to dashboard's apply_hotkey if available
        if hasattr(self, 'apply_hotkey'):
            self.apply_hotkey()
            self.context.log("🔄 Hotkeys reapplied from Settings tab")
        # Save immediately
        save_settings()

    # ----------------------------------------------------------------
    # SETTINGS BACKUP & ROLLBACK
    # ----------------------------------------------------------------
    def _refresh_backups_list(self):
        """Refresh the backup list combo from disk."""
        entries = list_backups()
        self.backup_list.clear()
        if entries:
            for entry in entries:
                self.backup_list.addItem(entry["label"], entry["ts"])
            self.backup_list.setCurrentIndex(-1)
            self.backup_count_label.setText(f"{len(entries)} backup(s) available")
            self.backup_count_label.setStyleSheet("font-size: 9pt; color: #10B981;")
        else:
            self.backup_list.setPlaceholderText("No backups found")
            self.backup_count_label.setText("No backups yet")
            self.backup_count_label.setStyleSheet("font-size: 9pt; color: #9CA3AF;")
        self.btn_restore_backup.setEnabled(len(entries) > 0)
        self.backup_restore_status.setText("")

    def _restore_backup(self):
        """Restore the selected settings backup (both settings + identity if available)."""
        idx = self.backup_list.currentIndex()
        if idx < 0:
            return
        ts_str = self.backup_list.currentData()
        if not ts_str:
            self.backup_restore_status.setText("❌ Invalid backup selection")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #EF4444;")
            return

        display_name = self.backup_list.currentText()
        reply = QMessageBox.question(
            self, "Confirm Restore",
            f"Restore from:\n{display_name}\n\n"
            "This will restore settings and/or identity configuration.\n"
            "Current settings will be backed up automatically when you next save.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        success = restore_backup(ts_str)
        if success:
            self.backup_restore_status.setText("✅ Restored! Refreshing UI...")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #10B981;")
            self._sync_ui_from_config()
            self.context.log(f"↩ Settings restored from backup: {display_name}")
        else:
            self.backup_restore_status.setText("❌ Restore failed — no backup files found")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #EF4444;")
            self.context.log("❌ Settings restore failed", is_debug=True)

    @contextmanager
    def _sync_guard(self):
        """Set the _syncing flag for the duration of a widget restore.

        Restore-time setValue/setChecked fires valueChanged/toggled handlers
        that call save_settings() + audit; the _syncing flag makes those
        handlers no-ops so a sync doesn't write disk or spam the audit log
        (v0.22.15). Restores the prior flag value in finally so nested syncs
        work and an exception can't leave the handler permanently muted.
        """
        _prev_syncing = getattr(self, '_syncing', False)
        self._syncing = True
        try:
            yield
        finally:
            self._syncing = _prev_syncing

    def _sync_ui_from_config(self):
        """Sync all UI fields back from CONFIG after a restore.
        This avoids requiring a full app restart and ensures the
        next Save won't overwrite the restored config with stale values.
        """
        with self._sync_guard():
            # Provider selection
            provider_label = self._PROVIDER_LABEL_MAP.get(
                CONFIG.get("active_provider", "local_llm"), "Local LLM (GGUF)"
            )
            idx = self.provider_combo.findText(provider_label)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)

            # VRAM
            self.vram_spinbox.setValue(CONFIG.get("vram_limit_mb", 2048))

            # Theme
            theme = CONFIG.get("active_theme", "Classic (Charcoal)")
            tidx = self.setting_theme_combo.findText(theme)
            if tidx >= 0:
                self.setting_theme_combo.setCurrentIndex(tidx)

            # TTS
            self.tts_enabled_cb.setChecked(CONFIG.get("tts_enabled", True))
            self.tts_speed_spin.setValue(CONFIG.get("tts_speed", 180))
            saved_voice = CONFIG.get("tts_voice", "")
            if saved_voice:
                vi = self.tts_voice_combo.findText(saved_voice)
                if isinstance(vi, int) and vi >= 0:
                    self.tts_voice_combo.setCurrentIndex(vi)
                else:
                    self.tts_voice_combo.setCurrentText(saved_voice)
            else:
                try:
                    combo_count = self.tts_voice_combo.count()
                    if isinstance(combo_count, int) and combo_count > 0:
                        lessac_idx = self.tts_voice_combo.findText("en_US-lessac-medium")
                        if isinstance(lessac_idx, int) and lessac_idx >= 0:
                            self.tts_voice_combo.setCurrentIndex(lessac_idx)
                        else:
                            self.tts_voice_combo.setCurrentIndex(0)
                except (TypeError, AttributeError):
                    # Graceful fallback: handles mock combo widgets during unit tests
                    pass

            # Duplex Barge-in
            if hasattr(self, 'duplex_barge_in_cb'):
                self.duplex_barge_in_cb.setChecked(CONFIG.get("duplex_barge_in_enabled", True))
            if hasattr(self, 'duplex_multiplier_spin'):
                self.duplex_multiplier_spin.setValue(float(CONFIG.get("duplex_energy_multiplier", 2.0)))
            if hasattr(self, 'duplex_min_speech_spin'):
                self.duplex_min_speech_spin.setValue(float(CONFIG.get("duplex_min_speech_duration", 0.2)))
            if hasattr(self, 'duplex_duck_volume_spin'):
                self.duplex_duck_volume_spin.setValue(float(CONFIG.get("duplex_duck_volume", 0.25)))

            # Mode toggles
            self.chk_freeform.setChecked(CONFIG.get("freeform_mode", False))
            self.chk_mock.setChecked(CONFIG.get("mock_mode", False))

            # Hotkeys
            self.hotkey_trigger_input.setText(CONFIG.get("trigger_hotkey", "ctrl+alt+a"))
            self.hotkey_read_input.setText(CONFIG.get("read_hotkey", "ctrl+alt+s"))
            self.hotkey_voice_input.setText(CONFIG.get("voice_hotkey", "ctrl+alt+v"))

            # Window & Logging preferences — setChecked fires the toggled handlers;
            # the _syncing guard (v0.22.15) keeps them from re-saving/auditing each
            # toggle on every sync (same guard as the threshold spinbox loop below).
            if hasattr(self, 'chk_notifications'):
                self.chk_notifications.setChecked(CONFIG.get('notifications_enabled', True))
            if hasattr(self, 'chk_vram_notifications'):
                self.chk_vram_notifications.setChecked(CONFIG.get('vram_notifications_enabled', True))
            if hasattr(self, 'chk_ram_notifications'):
                self.chk_ram_notifications.setChecked(CONFIG.get('ram_notifications_enabled', True))
            if hasattr(self, 'chk_disk_notifications'):
                self.chk_disk_notifications.setChecked(CONFIG.get('disk_notifications_enabled', True))
            self.chk_debug_logging.setChecked(CONFIG.get("debug_logging", False))
            self.chk_autosave_logs.setChecked(CONFIG.get("autosave_logs", True))
            # chk_always_on_top.toggled -> _on_always_on_top_toggle() calls
            # save_settings() + audit + setWindowFlags, so guard the restore too
            # (v0.22.15) — same pattern as the notification toggles above.
            self.chk_always_on_top.setChecked(CONFIG.get("always_on_top", False))
            # Keepalive cap — helper handles safe-coerce + clamp; QSpinBox
            # range 1-20 is enforced by Qt itself. Using default_max_ticks()
            # keeps this in lock-step with the runtime path that
            # KeepaliveContext.start_keepalive() actually uses.
            # hasattr guard: _sync_ui_from_config() can be invoked from
            # restore_backup() on a freshly-constructed widget tree where
            # _create_prefs_group() may not have run yet (defensive parity
            # with web_server_cb / web_port_spin guards further down).
            if hasattr(self, "spin_keepalive_max_ticks"):
                self.spin_keepalive_max_ticks.setValue(
                    KeepaliveContext.default_max_ticks()
                )
            # Resource threshold percentages (v0.22.11) — restore from CONFIG with
            # defaults matching the spinbox construction in _create_prefs_group().
            # Uses _coerce_pct so a hand-edited app_settings.json holding a string
            # can't crash QSpinBox.setValue at sync time (keepalive safe-coerce
            # contract, see TestKeepaliveMaxTicks).
            # _syncing guard (v0.22.15): restore-time setValue fires valueChanged,
            # which would otherwise trigger a redundant save_settings() disk write
            # and audit entry per spinbox on every sync (flagged in the v0.22.12
            # and v0.22.14 reviews). _sync_guard() clears the flag in its finally
            # so an exception can't leave the handler permanently muted.
            for _attr, _key, _default in (
                ("spin_ram_warning_pct", "ram_warning_pct", 80),
                ("spin_ram_critical_pct", "ram_critical_pct", 95),
                ("spin_disk_warning_pct", "disk_warning_pct", 85),
                ("spin_disk_critical_pct", "disk_critical_pct", 95),
                ("spin_vram_warning_pct", "vram_warning_pct", 88),
                ("spin_vram_critical_pct", "vram_critical_pct", 95),
            ):
                if hasattr(self, _attr):
                    getattr(self, _attr).setValue(
                        _coerce_pct(CONFIG.get(_key, _default), _default)
                    )

            # Docker
            self.sandbox_enabled_cb.setChecked(CONFIG.get("docker_sandbox_enabled", True))
            self.sandbox_image_input.setText(CONFIG.get("docker_sandbox_image", "python:3.11-slim"))
            self.sandbox_timeout_spin.setValue(CONFIG.get("docker_sandbox_timeout", 30))
            self.sandbox_memory_input.setText(CONFIG.get("docker_sandbox_memory", "256m"))

            # Web Server
            if hasattr(self, 'web_server_cb'):
                self.web_server_cb.setChecked(CONFIG.get("web_server_enabled", False))
            if hasattr(self, 'web_port_spin'):
                self.web_port_spin.setValue(CONFIG.get("web_server_port", 5050))

            # Secondary model fields
            self.desire_model_input.setText(CONFIG.get("desire_model_name", "qwen2.5-0.5b-instruct"))
            self.auditor_model_input.setText(CONFIG.get("auditor_model_name", "qwen2.5-0.5b-instruct"))
            self.vision_model_combo.setCurrentText(CONFIG.get("vision_model", ""))
            if hasattr(self, "discovery_url_input"):
                import Discovery
                self.discovery_url_input.setText(
                    CONFIG.get("KOKERTECH_DISCOVERY_URL", Discovery.DISCOVERY_URL)
                )

            # Persona
            self.persona_combo.setCurrentText(CONFIG.get("active_persona", "You are a helpful AI assistant."))

            # Protocol
            saved_protocol = CONFIG.get("protocol_prompt", "")
            if saved_protocol:
                self.protocol_edit.setPlainText(saved_protocol)
            else:
                self.protocol_edit.clear()
            self._rebuild_protocol_presets()
            self._validate_protocol()

            # Identity layer (may have been restored from backup too)
            self.id_name_input.setText(IDENTITY_CONFIG.get("name", ""))
            self.id_tone_input.setText(IDENTITY_CONFIG.get("tone", ""))
            self.id_pref_input.setText(IDENTITY_CONFIG.get("preferences", ""))
            self.id_bg_input.setPlainText(IDENTITY_CONFIG.get("background", ""))

            # Provider fields (updates URL, API key, model dropdown)
            self._update_provider_fields()

            # Refreshes
            self._refresh_backups_list()
            self.apply_theme(CONFIG.get("active_theme", "Classic (Charcoal)"))
            if hasattr(self, '_update_mode_indicator'):
                self._update_mode_indicator()

        # ----------------------------------------------------------------
        # INTEGRITY CHECK (manual trigger)
        # ----------------------------------------------------------------
    def _run_integrity_check(self):
        """Manually run the save integrity check and show results."""
        from config import check_last_save_integrity
        result = check_last_save_integrity()
        if result.get("ok"):
            self.backup_restore_status.setText("✅ Settings and identity files are intact")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #10B981;")
            self.context.log("🔍 Integrity check: OK — all files intact")
        else:
            issues = []
            if not result.get("settings_valid", True):
                issues.append("app_settings.json is corrupted")
            if not result.get("identity_valid", True):
                issues.append("user_identity.json is corrupted")
            msg = "Issues:\n" + "\n".join(f"  • {i}" for i in issues) + "\n\n"
            if result.get("backups_available"):
                msg += f"A backup from {result['latest_backup_label']} is available.\nRestore it from above?"
                reply = QMessageBox.question(
                    self, "Integrity Check — Issues Found",
                    msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    success = restore_backup(result["latest_backup_ts"])
                    if success:
                        self.backup_restore_status.setText(
                            "✅ Auto-restored from latest backup!")
                        self.backup_restore_status.setStyleSheet(
                            "font-size: 9pt; color: #10B981;")
                        self._sync_ui_from_config()
                    else:
                        self.backup_restore_status.setText(
                            "❌ Restore failed — backup files missing")
                        self.backup_restore_status.setStyleSheet(
                            "font-size: 9pt; color: #EF4444;")
            else:
                msg += "No backups available. You may need to reconfigure settings manually."
                QMessageBox.warning(self, "Integrity Check — Issues Found", msg)
                self.backup_restore_status.setText("⚠️ Integrity issues found (no backups)")
                self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #FBBF24;")
            self.context.log(f"🔍 Integrity check: {len(issues)} issue(s) found")

    # ----------------------------------------------------------------
    # BACKUP EXPORT / IMPORT
    # ----------------------------------------------------------------
    def _export_backup(self):
        """Export the selected backup as a .zip file."""
        idx = self.backup_list.currentIndex()
        if idx < 0:
            QMessageBox.information(self, "No Backup Selected",
                "Please select a backup from the list first, then click Export.")
            return

        ts_str = self.backup_list.currentData()
        if not ts_str:
            return

        default_name = f"kokertech_backup_{ts_str}.zip"

        from PyQt6.QtWidgets import QFileDialog
        save_path, _ = QFileDialog.getSaveFileName(
            self, "Export Backup As...", default_name,
            "ZIP Archives (*.zip)"
        )
        if not save_path:
            return

        success = export_backup(ts_str, save_path)
        if success:
            self.backup_restore_status.setText(f"✅ Exported: {os.path.basename(save_path)}")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #10B981;")
            self.context.log(f"📤 Backup exported: {save_path}")
        else:
            self.backup_restore_status.setText("❌ Export failed — backup files not found")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #EF4444;")
            self.context.log("❌ Backup export failed", is_debug=True)

    def _import_backup(self):
        """Import a .zip backup file into the backups directory."""
        from PyQt6.QtWidgets import QFileDialog

        import_path, _ = QFileDialog.getOpenFileName(
            self, "Import Backup from ZIP...", "",
            "ZIP Archives (*.zip)"
        )
        if not import_path:
            return

        new_ts = import_backup(import_path)
        if new_ts:
            self._refresh_backups_list()
            # Auto-select the newly imported backup
            for i in range(self.backup_list.count()):
                if self.backup_list.itemData(i) == new_ts:
                    self.backup_list.setCurrentIndex(i)
                    break
            self.backup_restore_status.setText(f"✅ Imported from {os.path.basename(import_path)}")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #10B981;")
            self.context.log(f"📥 Backup imported: {import_path}")
        else:
            self.backup_restore_status.setText("❌ Import failed — invalid or empty .zip")
            self.backup_restore_status.setStyleSheet("font-size: 9pt; color: #EF4444;")
            self.context.log("❌ Backup import failed", is_debug=True)

    def _test_provider_connection(self):
        """Test connectivity to the selected provider.

        Sprint 15: Now uses provider_health_check() for comprehensive
        diagnostics including VRAM, latency, and context window info.
        """
        provider_name = self._PROVIDER_MAP.get(self.provider_combo.currentText(), "local_llm")
        model = self.model_combo.currentText().strip() or None

        # Sprint 15: Try health check first
        ctrl = getattr(self, 'controller', None)
        if ctrl is not None and hasattr(ctrl, 'provider_health_check'):
            try:
                health = ctrl.provider_health_check()
                if health.get("ok"):
                    model_name = health.get("model_name", "?")
                    vram = health.get("vram_used_mb", 0)
                    latency = health.get("latency_ms", 0)
                    n_ctx = health.get("n_ctx", 0)
                    n_gpu = health.get("n_gpu_layers", 0)
                    msg = (
                        f"Provider: {provider_name}\n"
                        f"Model: {model_name}\n"
                        f"VRAM: {vram} MB\n"
                        f"Latency: {latency}ms (1 token)\n"
                        f"Context: {n_ctx} tokens\n"
                        f"GPU Layers: {n_gpu}"
                    )
                    QMessageBox.information(self, "Health Check OK", msg)
                    self.context.log(f"\u2705 Health check passed: {model_name} ({latency}ms)")
                else:
                    error = health.get("error", "Unknown")
                    QMessageBox.warning(self, "Health Check Failed",
                        f"Provider {provider_name}: {error}")
                    self.context.log(f"\u274c Health check failed: {error}")
                return
            except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
                self.context.log(f"Health check error: {e}")

        # Fallback: basic connectivity test
        from ai_base import get_provider
        try:
            provider = get_provider(name=provider_name, default_model=model)
            result = provider.chat_completion(
                messages=[{"role": "user", "content": "Reply with only: OK"}],
                model=model or "",
                timeout=10,
            )
            if result.get("error"):
                QMessageBox.warning(self, "Connection Failed",
                    f"{provider_name} connection failed:\n{result['error']}")
                self.context.log(f"\u274c Provider {provider_name} test failed: {result['error']}")
            else:
                QMessageBox.information(self, "Connection OK",
                    f"{provider_name} responded successfully!")
                self.context.log(f"\u2705 Provider {provider_name} connection verified")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
            QMessageBox.critical(self, "Connection Error", str(e))
            self.context.log(f"❌ Provider test error: {e}")


    def save_settings(self):
        provider_key = self._PROVIDER_MAP.get(self.provider_combo.currentText(), "local_llm")
        CONFIG["active_provider"] = provider_key
        CONFIG["vram_limit_mb"] = self.vram_spinbox.value()
        CONFIG["model_name"] = self.model_combo.currentText().strip() or CONFIG["model_name"]
        CONFIG["model_file"] = self.model_combo.currentData() or CONFIG["model_name"]
        if hasattr(self, "models_dir_input"):
                CONFIG["models_dir"] = self.models_dir_input.text().strip() or CONFIG.get("models_dir", "")
        if hasattr(self, "llm_n_ctx_spin"):
                CONFIG["llm_n_ctx"] = self.llm_n_ctx_spin.value()
        if hasattr(self, "llm_n_gpu_spin"):
                CONFIG["llm_n_gpu_layers"] = self.llm_n_gpu_spin.value()
        if hasattr(self, "llm_n_threads_spin"):
                CONFIG["llm_n_threads"] = self.llm_n_threads_spin.value()
        CONFIG["desire_model_name"] = self.desire_model_input.text().strip()
        CONFIG["auditor_model_name"] = self.auditor_model_input.text().strip()
        CONFIG["vision_model"] = self.vision_model_combo.currentText().strip()
        if hasattr(self, "discovery_url_input"):
            url = self.discovery_url_input.text().strip()
            if url:
                CONFIG["KOKERTECH_DISCOVERY_URL"] = url
            else:
                import Discovery
                CONFIG["KOKERTECH_DISCOVERY_URL"] = Discovery.DISCOVERY_URL
        CONFIG["active_theme"] = self.setting_theme_combo.currentText()
        CONFIG["tts_enabled"] = self.tts_enabled_cb.isChecked()
        CONFIG["tts_speed"] = self.tts_speed_spin.value()
        CONFIG["tts_voice"] = self.tts_voice_combo.currentText().strip()
        if hasattr(self, 'duplex_barge_in_cb'):
            CONFIG["duplex_barge_in_enabled"] = self.duplex_barge_in_cb.isChecked()
        if hasattr(self, 'duplex_multiplier_spin'):
            CONFIG["duplex_energy_multiplier"] = self.duplex_multiplier_spin.value()
        if hasattr(self, 'duplex_min_speech_spin'):
            CONFIG["duplex_min_speech_duration"] = self.duplex_min_speech_spin.value()
        if hasattr(self, 'duplex_duck_volume_spin'):
            CONFIG["duplex_duck_volume"] = self.duplex_duck_volume_spin.value()
        CONFIG["active_persona"] = self.persona_combo.currentText().strip()
        CONFIG["protocol_prompt"] = self.protocol_edit.toPlainText().strip()
        CONFIG["freeform_mode"] = self.chk_freeform.isChecked()
        CONFIG["mock_mode"] = self.chk_mock.isChecked()
        CONFIG["trigger_hotkey"] = self.hotkey_trigger_input.text().strip()
        CONFIG["read_hotkey"] = self.hotkey_read_input.text().strip()
        CONFIG["voice_hotkey"] = self.hotkey_voice_input.text().strip()
        CONFIG["always_on_top"] = self.chk_always_on_top.isChecked()
        CONFIG["debug_logging"] = self.chk_debug_logging.isChecked()
        CONFIG["autosave_logs"] = self.chk_autosave_logs.isChecked()
        CONFIG["notifications_enabled"] = self.chk_notifications.isChecked()
        # hasattr guard for per-resource notification toggles (may not exist
        # on freshly-constructed widget trees during backup-restore paths)
        if hasattr(self, 'chk_vram_notifications'):
            CONFIG["vram_notifications_enabled"] = self.chk_vram_notifications.isChecked()
        if hasattr(self, 'chk_ram_notifications'):
            CONFIG["ram_notifications_enabled"] = self.chk_ram_notifications.isChecked()
        if hasattr(self, 'chk_disk_notifications'):
            CONFIG["disk_notifications_enabled"] = self.chk_disk_notifications.isChecked()
        # hasattr guard: _create_prefs_group() may not have run yet on a
        # freshly-constructed widget tree (e.g., backup-restore path or a
        # test fixture that exercises settings without first wiring the
        # UI). Defensive parity with web_server_cb / web_port_spin below.
        if hasattr(self, "spin_keepalive_max_ticks"):
            CONFIG["keepalive_max_ticks"] = self.spin_keepalive_max_ticks.value()
        # Resource threshold percentages (v0.22.11) — hasattr-guarded so a
        # backup-restore or older test harness without the spinboxes never
        # crashes save_settings() (same pattern as spin_keepalive_max_ticks).
        for _attr, _key in (
            ("spin_ram_warning_pct", "ram_warning_pct"),
            ("spin_ram_critical_pct", "ram_critical_pct"),
            ("spin_disk_warning_pct", "disk_warning_pct"),
            ("spin_disk_critical_pct", "disk_critical_pct"),
            ("spin_vram_warning_pct", "vram_warning_pct"),
            ("spin_vram_critical_pct", "vram_critical_pct"),
        ):
            if hasattr(self, _attr):
                CONFIG[_key] = getattr(self, _attr).value()
        CONFIG["docker_sandbox_enabled"] = self.sandbox_enabled_cb.isChecked()
        CONFIG["docker_sandbox_image"] = self.sandbox_image_input.text().strip() or "python:3.11-slim"
        CONFIG["docker_sandbox_timeout"] = self.sandbox_timeout_spin.value()
        CONFIG["docker_sandbox_memory"] = self.sandbox_memory_input.text().strip() or "256m"
        # Unit tests may stub SettingsTabMixin without constructing all widgets.
        if hasattr(self, "web_server_cb"):
            CONFIG["web_server_enabled"] = self.web_server_cb.isChecked()
        else:
            CONFIG["web_server_enabled"] = CONFIG.get("web_server_enabled", False)

        if hasattr(self, "web_port_spin"):
            CONFIG["web_server_port"] = self.web_port_spin.value()
        else:
            CONFIG["web_server_port"] = CONFIG.get("web_server_port", 5050)
        IDENTITY_CONFIG["name"] = self.id_name_input.text().strip()
        IDENTITY_CONFIG["tone"] = self.id_tone_input.text().strip()
        IDENTITY_CONFIG["preferences"] = self.id_pref_input.text().strip()
        IDENTITY_CONFIG["background"] = self.id_bg_input.toPlainText().strip()
        try:
            # Note: API keys are saved in plaintext to app_settings.json.
            # For a production environment, consider using environment variables
            # or a system keyring (e.g., keyring.get_password()).
            save_settings()
            save_identity()
            self.apply_theme(CONFIG["active_theme"])
            self.setup_vram_monitor()
            # Update mode indicator if available
            if hasattr(self, '_update_mode_indicator'):
                self._update_mode_indicator()
            self.context.log("✅ Configurations saved to JSON successfully.")
            QMessageBox.information(self, "Configurations Saved", "System Settings and Identity Profile updated successfully.")
        except (OSError, RuntimeError, ValueError, TypeError, KeyError) as e:
            self.context.log(f"❌ Failed to save configurations: {e}")


