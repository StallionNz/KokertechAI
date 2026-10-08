"""Unit tests for workers.py — AIWorker, DesireWorker, VoiceRecorderWorker, AuditorWorker."""
import sys
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

class TestAIWorker(unittest.TestCase):

    def test_run_success(self):
        from workers import AIWorker
        controller = MagicMock()
        controller.process_input.return_value = {"final": "ok", "thinking": "...", "command": None}
        worker = AIWorker(controller, "hello")
        worker.reply_signal = MagicMock()
        worker.log_signal = MagicMock()
        worker.run()
        controller.process_input.assert_called_once()
        args = controller.process_input.call_args
        self.assertEqual(args[0][0], "hello")
        self.assertEqual(args[1]["log_callback"], worker.log_signal.emit)
        worker.reply_signal.emit.assert_called_once_with({"final": "ok", "thinking": "...", "command": None})

    def test_run_crash_failsafe(self):
        from workers import AIWorker
        controller = MagicMock()
        controller.process_input.side_effect = RuntimeError("inference error")
        worker = AIWorker(controller, "hi")
        worker.reply_signal = MagicMock()
        worker.run()
        worker.reply_signal.emit.assert_called_once()
        args = worker.reply_signal.emit.call_args[0][0]
        self.assertIn("Core Failure", args["final"])

class TestDesireWorker(unittest.TestCase):

    @patch("workers.desire_engine.generate_prediction", return_value=[{"prediction": "sug1"}, {"prediction": "sug2"}])
    def test_run_emits_suggestions(self, mock_gen):
        from workers import DesireWorker
        worker = DesireWorker(history=[], current_prompt="test")
        worker.suggestion_signal = MagicMock()
        worker.run()
        self.assertEqual(worker.suggestion_signal.emit.call_count, 2)

    @patch("workers.desire_engine.generate_prediction", return_value={"prediction": "single"})
    def test_run_single_dict(self, mock_gen):
        from workers import DesireWorker
        worker = DesireWorker(history=[], current_prompt="test")
        worker.suggestion_signal = MagicMock()
        worker.run()
        worker.suggestion_signal.emit.assert_called_once_with({"prediction": "single"})

    @patch("workers.desire_engine.generate_prediction", return_value=None)
    def test_run_none_skips(self, mock_gen):
        from workers import DesireWorker
        worker = DesireWorker([], "")
        worker.suggestion_signal = MagicMock()
        worker.run()
        worker.suggestion_signal.emit.assert_not_called()

    @patch("workers.desire_engine.generate_prediction", side_effect=Exception("boom"))
    def test_run_exception_failsafe(self, mock_gen):
        from workers import DesireWorker
        worker = DesireWorker([], "")
        worker.suggestion_signal = MagicMock()
        worker.run()
        worker.suggestion_signal.emit.assert_called_once()
        self.assertIn("unavailable", worker.suggestion_signal.emit.call_args[0][0]["prediction"])

class TestVoiceRecorderWorker(unittest.TestCase):

    def test_init_state(self):
        from workers import VoiceRecorderWorker
        v = VoiceRecorderWorker()
        self.assertFalse(v.is_recording)

    def test_start_recording_sets_flag_and_starts(self):
        from workers import VoiceRecorderWorker
        v = VoiceRecorderWorker()
        v.start = MagicMock()
        v.start_recording()
        self.assertTrue(v.is_recording)
        v.start.assert_called_once()

    def test_stop_recording_clears_flag(self):
        from workers import VoiceRecorderWorker
        v = VoiceRecorderWorker()
        v.is_recording = True
        v.stop_recording()
        self.assertFalse(v.is_recording)

    def test_run_missing_deps_emits_error(self):
        from workers import VoiceRecorderWorker
        v = VoiceRecorderWorker()
        v.transcription_signal = MagicMock()
        v.status_signal = MagicMock()
        v.is_recording = True
        v.run()
        v.transcription_signal.emit.assert_called_once()
        self.assertIn("Missing Audio", v.transcription_signal.emit.call_args[0][0])

class TestAuditorWorker(unittest.TestCase):

    @patch("workers.cognitive_auditor.audit_interaction")
    def test_run_calls_auditor(self, mock_audit):
        from workers import AuditorWorker
        worker = AuditorWorker("user text", "ai text")
        worker.log_signal = MagicMock()
        worker.run()
        mock_audit.assert_called_once_with("user text", "ai text", log_callback=worker.log_signal.emit)

    @patch("workers.cognitive_auditor.audit_interaction", side_effect=Exception("audit crash"))
    def test_run_exception_failsafe(self, mock_audit):
        from workers import AuditorWorker
        worker = AuditorWorker("u", "a")
        worker.log_signal = MagicMock()
        worker.run()
        worker.log_signal.emit.assert_called_once()
        self.assertIn("failed", worker.log_signal.emit.call_args[0][0].lower())

if __name__ == "__main__":
    unittest.main()
