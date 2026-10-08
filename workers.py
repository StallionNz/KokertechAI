from PyQt6.QtCore import QThread, pyqtSignal
import cognitive_auditor
import desire_engine

class AIWorker(QThread):
    # Now correctly expects a single dictionary from the new Controller
    reply_signal = pyqtSignal(dict)
    log_signal = pyqtSignal(str)
    stream_signal = pyqtSignal(str)

    def __init__(self, controller, user_text, cancel_event=None, enable_streaming=True):
        super().__init__()
        self.controller = controller
        self.user_text = user_text
        self.enable_streaming = enable_streaming
        # Optional threading.Event for mid-flight cancellation of the HTTP
        # request inside the AI provider. When set, the provider returns
        # immediately with a 'Cancelled by user' error instead of waiting
        # for the in-flight HTTP response. The worker also checks
        # isInterruptionRequested() (Qt-level) for the same purpose.
        self.cancel_event = cancel_event

    def run(self):
        try:
            # ── Streaming path ──
            # When streaming is enabled, pass stream_signal.emit as the
            # stream_callback so that response tokens appear progressively
            # in the thinking display and progress panel in real-time.
            # The controller accumulates all tokens and returns the full
            # parsed result at the end, so the reply_signal flow is unchanged.
            kwargs = {
                "log_callback": self.log_signal.emit,
                "cancel_event": self.cancel_event,
            }
            if self.enable_streaming and self.stream_signal is not None:
                kwargs["stream_callback"] = self.stream_signal.emit

            result_dict = self.controller.process_input(
                self.user_text,
                **kwargs,
            )
            # If the user clicked Stop while the controller was running, discard
            # the response. Note: this does NOT interrupt the in-flight HTTP
            # request — that will continue in the background and its result is
            # simply ignored. The user gets immediate visual feedback via the
            # Stop button state change instead.
            if self.isInterruptionRequested():
                self.reply_signal.emit({
                    "final": "⏹ Generation stopped by user.",
                    "thinking": "",
                    "command": None,
                    "stopped": True,
                })
                return
            self.reply_signal.emit(result_dict)
        except Exception as e:
            # Failsafe to prevent a thread crash from taking down the GUI
            self.reply_signal.emit({
                "final": f"❌ Core Failure: {str(e)}",
                "thinking": "Worker thread crashed during inference.",
                "command": None
            })

class DesireWorker(QThread):
    suggestion_signal = pyqtSignal(dict)

    def __init__(self, history, current_prompt):
        super().__init__()
        self.history = history
        self.current_prompt = current_prompt

    def run(self):
        try:
            suggestions = desire_engine.generate_prediction(self.history, self.current_prompt)
            if not suggestions: return
            
            if isinstance(suggestions, list):
                for sugg in suggestions: self.suggestion_signal.emit(sugg)
            elif isinstance(suggestions, dict):
                self.suggestion_signal.emit(suggestions)
        except Exception as e:
            self.suggestion_signal.emit({"prediction": f"Desire engine unavailable: {e}"})

_WHISPER_CACHE = None

class VoiceRecorderWorker(QThread):
    transcription_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)

    def __init__(self, vad_enabled=None):
        super().__init__()
        self.is_recording = False
        self.vad_enabled = vad_enabled

    def start_recording(self):
        self.is_recording = True
        self.start()

    def stop_recording(self):
        # Cooperative stop: flip the recording flag and request interruption.
        self.is_recording = False
        if hasattr(self, "requestInterruption") and callable(getattr(self, "requestInterruption")):
            try:
                self.requestInterruption()
            except (RuntimeError, TypeError):
                pass

    def run(self):
        try:
            import pyaudio
        except ImportError:
            self.transcription_signal.emit("Error: Missing Audio tools (pip install pyaudio openai-whisper)")
            return

        audio = pyaudio.PyAudio()
        stream = None

        try:
            import time
            from config import CONFIG
            from acoustic_pipeline import compute_audio_rms, transcribe_pcm_audio

            use_vad = (
                self.vad_enabled
                if self.vad_enabled is not None
                else CONFIG.get("voice_vad_enabled", True)
            )
            silence_timeout = float(CONFIG.get("voice_vad_silence_seconds", 1.2))
            energy_thresh = float(CONFIG.get("voice_vad_energy_threshold", 120.0))

            stream = audio.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                frames_per_buffer=1024,
            )
            frames = []

            self.status_signal.emit("🔴 Recording... (Speak or Release to Stop)")
            speech_started = False
            silence_start = None

            while self.is_recording:
                # If interruption is requested during shutdown, stop early.
                if self.isInterruptionRequested():
                    break
                try:
                    chunk = stream.read(1024, exception_on_overflow=False)
                except OSError:
                    break
                frames.append(chunk)

                if use_vad:
                    rms = compute_audio_rms(chunk)
                    if rms >= energy_thresh:
                        if not speech_started:
                            speech_started = True
                            self.status_signal.emit("🔴 Speech active...")
                        silence_start = None
                    elif speech_started:
                        now = time.time()
                        if silence_start is None:
                            silence_start = now
                        elif now - silence_start >= silence_timeout:
                            # Natural trailing silence detected
                            self.is_recording = False
                            break

            # If we stopped early without data, skip transcription.
            if not frames or self.isInterruptionRequested():
                return

            self.status_signal.emit("⏳ Transcribing audio with Whisper...")
            pcm_bytes = b"".join(frames)
            text = transcribe_pcm_audio(pcm_bytes)
            self.transcription_signal.emit(text)

        except (RuntimeError, OSError, ValueError, TypeError) as e:
            self.transcription_signal.emit(f"Error: {e}")
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except (RuntimeError, OSError):
                    pass
            try:
                audio.terminate()
            except (RuntimeError, OSError):
                pass
class MultiModelWorker(QThread):
    """Worker that dispatches a prompt to multiple models/providers in parallel."""
    multi_reply_signal = pyqtSignal(list)  # list of result dicts
    log_signal = pyqtSignal(str)

    def __init__(self, controller, user_text, model_specs):
        super().__init__()
        self.controller = controller
        self.user_text = user_text
        self.model_specs = model_specs

    def run(self):
        try:
            results = self.controller.process_input_multi(
                self.user_text,
                self.model_specs,
                log_callback=self.log_signal.emit,
            )
            self.multi_reply_signal.emit(results)
        except Exception as e:
            self.multi_reply_signal.emit([{
                "provider": "",
                "model": "",
                "label": "Error",
                "ok": False,
                "content": "",
                "error": str(e),
                "ts": "",
            }])


class AuditorWorker(QThread):
    log_signal = pyqtSignal(str)
    
    def __init__(self, user_text, ai_text):
        super().__init__()
        self.user_text = user_text
        self.ai_text = ai_text

    def run(self):
        try:
            # Pass the log_signal.emit function into the auditor so it can talk to the UI
            cognitive_auditor.audit_interaction(self.user_text, self.ai_text, log_callback=self.log_signal.emit)
        except Exception as e:
            self.log_signal.emit(f"⚠️ Auditor failed: {e}")