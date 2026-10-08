"""
app_hotkeys.py - AppHotkeysMixin: global hotkeys, wake word, copilot features.
Extracted from app_core.py.
"""
import os
import threading
import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog

from config import CONFIG, WORKSPACE_DIR, save_settings
from utils.qt_dispatch import run_on_main_thread

# Keyboard hooks are not safe/stable under pytest on Windows in this repo
# (access violations / stdout teardown errors). Disable via:
#   KOKERTECH_DISABLE_KEYBOARD=1
_KOKERTECH_DISABLE_KEYBOARD = os.getenv("KOKERTECH_DISABLE_KEYBOARD", "0") == "1"


def _get_keyboard_module():
    """
    Import keyboard lazily and only when keyboard hooks are enabled.
    Returns None when disabled or when keyboard import fails.
    """
    if _KOKERTECH_DISABLE_KEYBOARD:
        return None
    try:
        import keyboard  # type: ignore
        return keyboard
    except Exception:
        return None

try:
    import wake_word
    _WAKE_WORD_AVAILABLE = wake_word.is_available()
except (ImportError, OSError, RuntimeError):
    _WAKE_WORD_AVAILABLE = False


class AppHotkeysMixin:
    """Mixin providing global hotkeys, wake word, and copilot features."""

    def _safe_log_to_audit(self, msg: str):
        """Hotkey callbacks run in contexts where log_to_audit may be absent
        (e.g., tests constructing mixins via __new__). Fall back to file_logger
        or no-op to avoid crashes.
        """
        try:
            if hasattr(self, "log_to_audit"):
                self.log_to_audit(msg)
                return
        except RuntimeError:
            return
        try:
            if hasattr(self, "file_logger"):
                self.file_logger.info(msg)
        except Exception:
            pass

    def _start_wake_listener(self):
        if not _WAKE_WORD_AVAILABLE:
            return
        try:
            self._wake_stop, self._wake_thread = wake_word.start_wake_listener(
                self._on_wake_word_detected
            )
            self.wake_status_lbl.setText("Listening...")
            self.wake_status_lbl.setStyleSheet("font-size: 8pt; color: #10B981;")
            self._safe_log_to_audit("Wake word listener started")
        except (RuntimeError, OSError, ImportError) as e:
            self._safe_log_to_audit(f"Wake word start failed: {e}")
            self.wake_status_lbl.setText("Error")
            self.wake_status_lbl.setStyleSheet("font-size: 8pt; color: #EF4444;")

    def _stop_wake_listener(self):
        stop = getattr(self, '_wake_stop', None)
        if stop:
            stop.set()
        thread = getattr(self, '_wake_thread', None)
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self._wake_stop = None
        self._wake_thread = None

    def _toggle_wake_word(self, enabled):
        CONFIG["wake_word_enabled"] = enabled
        save_settings()
        if enabled:
            self._start_wake_listener()
        else:
            self._stop_wake_listener()
            if hasattr(self, 'wake_status_lbl'):
                self.wake_status_lbl.setText("")
                self.wake_status_lbl.setStyleSheet("font-size: 8pt; color: #9CA3AF;")

    def _on_wake_word_detected(self):
        try:
            QTimer.singleShot(0, self._do_hotkey_voice)
        except (RuntimeError, TypeError) as e:
            self.file_logger.warning(f"Wake word callback error: {e}")

    def apply_hotkey(self):
        kb = _get_keyboard_module()
        if kb is None:
            return  # keyboard hooks disabled in this environment

        try:
            kb.unhook_all()
            trigger_hk = CONFIG.get("trigger_hotkey", "ctrl+alt+a")
            kb.add_hotkey(trigger_hk, self._hotkey_trigger_analysis)
            read_hk = CONFIG.get("read_hotkey", "ctrl+alt+s")
            kb.add_hotkey(read_hk, self._hotkey_read_selected_text)
            voice_hk = CONFIG.get("voice_hotkey", "ctrl+alt+v")
            kb.add_hotkey(voice_hk, self._hotkey_voice_input)
            hud_hk = CONFIG.get("hud_hotkey")
            if hud_hk:
                kb.add_hotkey(hud_hk, self._hotkey_toggle_hud)
            companion_recovery_hk = "ctrl+alt+shift+x"
            kb.add_hotkey(companion_recovery_hk, self._hotkey_toggle_hud_click_through)
            self.file_logger.info(
                f"Hotkeys: trigger={trigger_hk}, read={read_hk}, voice={voice_hk}, "
                f"hud={hud_hk}, companion_recovery={companion_recovery_hk}"
            )
        except Exception as e:
            # Hotkey registration can fail due to OS permissions, antivirus, etc.
            if hasattr(self, "file_logger"):
                self.file_logger.warning(f"Could not register hotkeys: {e}")

    def _hotkey_toggle_hud(self):
        try:
            QTimer.singleShot(0, self._do_toggle_hud)
        except RuntimeError as e:
            if hasattr(self, "file_logger"):
                self.file_logger.warning(f"HUD hotkey error: {e}")

    def _hotkey_toggle_hud_click_through(self):
        """Marshal the companion's recovery toggle onto Qt's GUI thread."""
        try:
            if not run_on_main_thread(self._do_toggle_hud_click_through):
                if hasattr(self, "file_logger"):
                    self.file_logger.warning("Companion recovery hotkey could not reach the GUI thread")
        except (RuntimeError, ImportError, TypeError) as e:
            if hasattr(self, "file_logger"):
                self.file_logger.warning(f"Companion recovery hotkey error: {e}")

    def _do_toggle_hud_click_through(self):
        try:
            if not hasattr(self, "mini_hud") or self.mini_hud is None:
                self._do_toggle_hud()
                if not hasattr(self, "mini_hud") or self.mini_hud is None:
                    return
            self.mini_hud.set_click_through(not self.mini_hud._click_through)
            self._safe_log_to_audit(
                f"Companion click-through: {'enabled' if self.mini_hud._click_through else 'disabled'}"
            )
        except (RuntimeError, OSError, ValueError, TypeError, AttributeError) as e:
            if hasattr(self, "file_logger"):
                self.file_logger.warning(f"Companion recovery toggle error: {e}")

    def _do_toggle_hud(self, *args, **kwargs):
        try:
            if not hasattr(self, "mini_hud") or self.mini_hud is None:
                from tabs.mini_hud import MiniHudDialog
                parent_widget = self if hasattr(self, "show") else None
                self.mini_hud = MiniHudDialog(
                    parent=parent_widget,
                    controller=getattr(self, "controller", None),
                )
                self.mini_hud.send_to_main_requested.connect(self._on_hud_send_to_main)
            self.mini_hud.toggle_visibility()
            self._safe_log_to_audit("Hotkey: Toggle mini-Kokertechai")
        except (RuntimeError, OSError, ValueError, TypeError) as e:
            if hasattr(self, "file_logger"):
                self.file_logger.warning(f"HUD toggle error: {e}")

    def _on_hud_send_to_main(self, content: str):
        if hasattr(self, "txt_input") and content:
            self.txt_input.setPlainText(content[:2000])
            if hasattr(self.txt_input, "setFocus"):
                self.txt_input.setFocus()
        self._safe_log_to_audit("Mini-HUD: Transferred content to main chat input")

    def _hotkey_trigger_analysis(self):
        try:
            QTimer.singleShot(0, self._do_hotkey_trigger)
        except (RuntimeError) as e:
            self.file_logger.warning(f"Hotkey trigger error: {e}")

    def _do_hotkey_trigger(self):
        last = getattr(self, 'last_user_text', '')
        if last:
            self.txt_input.setPlainText(last)
            self.action_send_prompt()
        self._safe_log_to_audit("Hotkey: Trigger Analysis")

    def _hotkey_read_selected_text(self):
        kb = _get_keyboard_module()
        if kb is None:
            return
        try:
            kb.send('ctrl+c')
            time.sleep(0.1)
            QTimer.singleShot(0, self._do_hotkey_read)
        except (RuntimeError, OSError) as e:
            self.file_logger.warning(f"Hotkey read error: {e}")

    def _do_hotkey_read(self):
        try:
            import pyperclip
            text = pyperclip.paste().strip()
            if text:
                self.voice_output.speak(text[:500])
                self._safe_log_to_audit(f"Hotkey: Read '{text[:60]}...'")
            else:
                self._safe_log_to_audit("Hotkey: Read - no text in clipboard")
        except (RuntimeError, ImportError, OSError) as e:
            self.file_logger.warning(f"Hotkey read error: {e}")

    def _hotkey_voice_input(self):
        try:
            QTimer.singleShot(0, self._do_hotkey_voice)
        except (RuntimeError) as e:
            self.file_logger.warning(f"Hotkey voice error: {e}")

    def _do_hotkey_voice(self):
        try:
            self.start_voice_record()
            QTimer.singleShot(5000, self.stop_voice_record)
            self._safe_log_to_audit("Hotkey: Voice Input")
        except (RuntimeError, OSError) as e:
            self.file_logger.warning(f"Hotkey voice error: {e}")

    def start_voice_record(self):
        self.btn_mic.setStyleSheet("background-color: #EF4444; color: white;")
        from workers import VoiceRecorderWorker
        self.voice_worker = VoiceRecorderWorker()
        self.voice_worker.transcription_signal.connect(lambda t: self.txt_input.setPlainText(t))
        if hasattr(self.voice_worker, "barge_in_signal") and hasattr(self, "show_barge_in_badge"):
            self.voice_worker.barge_in_signal.connect(self.show_barge_in_badge)
        self.voice_worker.start_recording()

    def stop_voice_record(self):
        self.btn_mic.setStyleSheet("")
        if hasattr(self, 'voice_worker'):
            self.voice_worker.stop_recording()

    def _action_capture_screen(self):
        try:
            from copilot_features import ScreenCapture, VisionAnalyzer
        except ImportError:
            self.log_to_audit("Copilot features not available.")
            return

        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(
            button=getattr(self, 'btn_screen', None),
            log_callback=self.log_to_audit,
            chat_callback=lambda html: self._append_chat(html),
        )
        kc.set_btn_text("📷 Capturing...")
        kc.update_chat("<b>Capturing screen...</b><br>")
        kc.log("Capturing screen...")
        kc.start_keepalive("analyzing", 30000)

        def _do():
            try:
                capture = ScreenCapture(save_dir=os.path.join(os.path.dirname(__file__), "data", "screenshots"))
                filepath, window_title = capture.capture()
                kc.log(f"Screen captured from '{window_title}' ({kc.elapsed}s)")
                kc.update_chat(f"<b>✅ Screen captured from:</b> {window_title} <i>({kc.elapsed}s)</i><br>")

                kc.set_btn_text("🧠 Analyzing...")
                kc.update_chat("<b>⏳ Analyzing screenshot with gemma-4-e2b...</b> <i>(this may take 60-120s)</i><br>")

                vision = VisionAnalyzer()
                analysis = vision.analyze(filepath, context=f"Window: {window_title}")

                result = (
                    f"<b>📸 Screen Capture Complete</b> <i>({kc.elapsed}s total)</i><br>"
                    f"<b>Window:</b> {window_title}<br>"
                    f"<b>Saved:</b> {filepath}<br><br>"
                    f"{analysis}"
                )
                kc.log(f"Vision analysis complete ({kc.elapsed}s)")
                kc.update_chat(result)
                capture.cleanup(max_files=10)
            except Exception as e:  # noqa: BLE001
                err = f"Screen capture failed after {kc.elapsed}s: {e}"
                kc.log(err)
                kc.update_chat(f"<b style='color:#EF4444;'>❌ {err}</b><br>")
            finally:
                kc.done()

        threading.Thread(target=_do, daemon=True).start()

    def _action_ocr_file(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Select Image for OCR", WORKSPACE_DIR,
            "Images (*.png *.jpg *.jpeg *.bmp *.tiff *.gif)"
        )
        if not filepath:
            return

        from keepalive_helper import KeepaliveContext
        kc = KeepaliveContext(
            button=getattr(self, 'btn_ocr', None),
            log_callback=self.log_to_audit,
            chat_callback=lambda html: self._append_chat(html),
        )
        kc.set_btn_text("📄 OCR Loading...")
        kc.update_chat(f"<b>Running OCR on:</b> {os.path.basename(filepath)}<br>")
        kc.log("OCR loading image...")
        kc.start_keepalive("processing", 15000)

        def _do():
            try:
                from PIL import Image
                import pytesseract

                kc.log(f"OCR extracting text from '{os.path.basename(filepath)}'")
                kc.set_btn_text("🔍 OCR Extracting...")

                img = Image.open(filepath)
                text = pytesseract.image_to_string(img).strip()

                if not text:
                    result = (
                        f"<b>🔍 OCR Complete</b> <i>({kc.elapsed}s)</i><br>"
                        f"<b>File:</b> {filepath}<br>"
                        f"<b>Result:</b> No text found in {os.path.basename(filepath)}"
                    )
                else:
                    if len(text) > 2000:
                        display_text = text[:2000] + "...[TRUNCATED]"
                    else:
                        display_text = text
                    result = (
                        f"<b>🔍 OCR Complete</b> <i>({kc.elapsed}s)</i><br>"
                        f"<b>File:</b> {filepath}<br>"
                        f"<b>Extracted text ({len(text)} chars):</b><br>"
                        f"<pre style='white-space:pre-wrap;'>{display_text}</pre>"
                    )

                kc.log(f"OCR complete ({kc.elapsed}s)")
                kc.update_chat(result)

            except ImportError:
                err = f"OCR deps missing after {kc.elapsed}s. Run: pip install Pillow pytesseract"
                kc.log(err)
                kc.update_chat(f"<b style='color:#EF4444;'>❌ {err}</b><br>")
            except (RuntimeError, OSError, ValueError) as e:
                msg = str(e)
                if "tesseract" in msg.lower():
                    err = f"Tesseract not installed after {kc.elapsed}s"
                else:
                    err = f"OCR failed after {kc.elapsed}s: {e}"
                kc.log(err)
                kc.update_chat(f"<b style='color:#EF4444;'>❌ {err}</b><br>")
            finally:
                kc.done()

        threading.Thread(target=_do, daemon=True).start()

