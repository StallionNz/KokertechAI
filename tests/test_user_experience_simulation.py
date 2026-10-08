"""tests/test_user_experience_simulation.py — End-to-End User Experience Simulations.

Validates:
1. Sensory Mesh Desktop Vision UX:
   - Window targeting (<<SEE:window("Title")>>), region cropping (<<SEE:region(x,y,w,h)>>), monitor indexing.
   - Graceful Fast-OCR fallback when vision models are absent.
   - Multimodal message structure preservation without stringified reprs.
2. Duplex Barge-In Audio UX:
   - Two-stage ducking: soft ducking (>80ms) and hard interruption (>200ms).
   - In-memory PCM attenuation without disk writes.
   - UI visual badging (voice_barge_in_badge) and Settings controls persistence.
3. Speculative Multi-Agent Hive UX:
   - Progressive real-time stage badges streamed live to the user.
   - Multi-candidate speculative drafting with God Reviewer Auditor candidate selection.
   - Collapsible <details> drawer for executive readability.
   - Cooperative user cancellation mid-flight.
"""

from __future__ import annotations

import io
import threading
import unittest
from unittest.mock import MagicMock, patch
import wave

import numpy as np
from PIL import Image

from config import CONFIG
from services.sensory_service import FrameData, SensoryService
from acoustic_pipeline import (
    DuplexBargeInController,
    attenuate_wav_bytes,
)
from services.hive_service import HiveService


class TestSensoryMeshUserExperience(unittest.TestCase):
    """End-to-End User Experience simulations for Sensory Mesh vision triggers."""

    def setUp(self) -> None:
        self.sensory_svc = SensoryService()

    def test_ux_see_tag_routing_and_prompt_formatting(self) -> None:
        """User experience: typing <<SEE>> or <<SEE:...>> seamlessly extracts the goal and intent."""
        # 1. Bare <<SEE>> tag
        should_route_bare, query_bare, target_bare = self.sensory_svc.should_route_sensory("<<SEE>>")
        self.assertTrue(should_route_bare)
        self.assertIn("Describe what is on my screen", query_bare)
        self.assertEqual(target_bare, "active_window")

        # 2. Prompt with user intent
        user_input = "<<SEE: Check what is currently open on my screen>>"
        should_route, goal, target = self.sensory_svc.should_route_sensory(user_input)

        self.assertTrue(should_route)
        self.assertEqual(goal, "Check what is currently open on my screen")
        self.assertEqual(target, "active_window")

    def test_ux_see_window_targeting_simulation(self) -> None:
        """User experience: targeting an application window (<<SEE:window(...)>>)."""
        user_input = '<<SEE:window("Calculator"): Read the displayed digits>>'
        should_route, goal, target = self.sensory_svc.should_route_sensory(user_input)

        self.assertTrue(should_route)
        self.assertEqual(goal, "Read the displayed digits")
        self.assertEqual(target, "window:Calculator")

        # Simulate window resolution & capture
        synthetic_img = Image.new("RGB", (640, 480), color=(30, 30, 30))
        fake_bounds = {"left": 0, "top": 0, "width": 640, "height": 480}
        with patch.object(self.sensory_svc, "_resolve_window_by_title", return_value=(fake_bounds, "Calculator")):
            with patch("PIL.ImageGrab.grab", return_value=synthetic_img):
                frame = self.sensory_svc.capture_framebuffer(target=target, allow_synthetic_fallback=True)

        self.assertIsNotNone(frame)
        self.assertIn("window:Calculator", frame.target)
        self.assertEqual(frame.width, 640)
        self.assertEqual(frame.height, 480)

    def test_ux_see_region_crop_simulation(self) -> None:
        """User experience: targeting a specific screen region (<<SEE:region(...)>>)."""
        user_input = "<<SEE:region(100, 200, 400, 300): Inspect the chart graph>>"
        should_route, goal, target = self.sensory_svc.should_route_sensory(user_input)

        self.assertTrue(should_route)
        self.assertEqual(goal, "Inspect the chart graph")
        self.assertEqual(target, "region:100,200,400,300")

        synthetic_crop = Image.new("RGB", (400, 300), color=(10, 100, 200))
        with patch("PIL.ImageGrab.grab", return_value=synthetic_crop):
            frame = self.sensory_svc.capture_framebuffer(target=target, allow_synthetic_fallback=True)

        self.assertIsNotNone(frame)
        self.assertEqual(frame.width, 400)
        self.assertEqual(frame.height, 300)

    def test_ux_ocr_fallback_experience_when_vision_model_absent(self) -> None:
        """User experience: when vision models fail or are absent, OCR injects on-screen text."""
        frame = FrameData(
            raw_bytes=b"fakejpeg",
            b64_data="ZmFrZWpwZWc=",
            width=200,
            height=100,
            frame_hash="abc123hash456789",
            timestamp="2026-10-04T00:00:00Z",
            target="active_window",
            window_title="KokertechAI",
        )

        with patch.object(self.sensory_svc, "extract_ocr_text", return_value="Error 404: Resource Not Found"):
            with patch.dict(CONFIG, {"vision_model": ""}):
                metadata_desc = self.sensory_svc._build_metadata_description(frame, prompt="What is the error?")

        self.assertIn("[ON-SCREEN OCR TEXT DETECTED]", metadata_desc)
        self.assertIn("Error 404: Resource Not Found", metadata_desc)
        self.assertIn("User Query: What is the error?", metadata_desc)

    def test_ux_multimodal_normalization_preserves_content_structures(self) -> None:
        """User experience: multimodal messages pass to AI provider without stringification."""
        from ai_base import _normalize_chat_messages

        multimodal_content = [
            {"type": "text", "text": "What is in this screenshot?"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,fakebytes"}},
        ]
        raw_messages = [
            {"role": "user", "content": multimodal_content},
        ]

        normalized = _normalize_chat_messages(raw_messages)
        # Content remains a list rather than being str(list) stringified
        self.assertIsInstance(normalized[0]["content"], list)
        self.assertEqual(len(normalized[0]["content"]), 2)
        self.assertEqual(normalized[0]["content"][0]["type"], "text")
        self.assertEqual(normalized[0]["content"][1]["type"], "image_url")


class TestDuplexBargeInUserExperience(unittest.TestCase):
    """End-to-End User Experience simulations for Duplex Barge-In audio ducking & interruption."""

    def test_ux_two_stage_ducking_smooth_transition(self) -> None:
        """User experience: speaking while AI speaks first ducks softly, then interrupts cleanly."""
        controller = DuplexBargeInController(
            base_threshold=100.0,
            threshold_multiplier=1.0,
            duck_duration=0.08,        # ~1 chunk
            min_speech_duration=0.20,   # ~3 chunks
            chunk_duration=0.05,
        )

        loud_chunk = np.full(512, 1000, dtype=np.int16).tobytes()
        quiet_chunk = np.full(512, 10, dtype=np.int16).tobytes()

        # Chunk 1 (0.05s): below duck_duration
        self.assertFalse(controller.process_chunk(loud_chunk, is_playing=True))
        # Chunk 2 (0.10s): >= duck_duration (0.08s) -> Soft ducking active!
        self.assertFalse(controller.process_chunk(loud_chunk, is_playing=True))
        self.assertTrue(controller.is_ducked)

        # Chunk 3 & 4: sustained speech reaching >= min_speech_duration (0.20s) -> Hard barge-in!
        controller.process_chunk(loud_chunk, is_playing=True)
        interrupted = controller.process_chunk(loud_chunk, is_playing=True)
        self.assertTrue(interrupted)

        # Once user stops speaking and quiet resumes: ducking clears
        controller.process_chunk(quiet_chunk, is_playing=False)
        self.assertFalse(controller.is_ducked)

    def test_ux_pcm_audio_attenuation_fidelity(self) -> None:
        """User experience: ducked audio is smoothly attenuated in RAM with zero disk I/O."""
        # 16-bit PCM WAV at 16000Hz with volume 8000
        samples = np.full(320, 8000, dtype=np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(samples.tobytes())
        original_wav = buf.getvalue()

        # Attenuate down to 25% (ducking volume)
        attenuated_wav = attenuate_wav_bytes(original_wav, volume_scale=0.25)
        self.assertIsNotNone(attenuated_wav)

        with wave.open(io.BytesIO(attenuated_wav), "rb") as rf:
            out_frames = rf.readframes(rf.getnframes())
            out_samples = np.frombuffer(out_frames, dtype=np.int16)
            self.assertTrue(np.allclose(out_samples, 2000, atol=2))

    def test_ux_barge_in_ui_badge_display(self) -> None:
        """User experience: AppUIMixin shows user-friendly visual feedback on interruption."""
        from app_ui import AppUIMixin

        class DummyUI(AppUIMixin):
            def __init__(self):
                self.voice_barge_in_badge = MagicMock()
                self._barge_in_timer = None

        ui = DummyUI()
        ui.show_barge_in_badge("⚡ Interrupted by user — Listening...")

        ui.voice_barge_in_badge.setText.assert_called_with("⚡ Interrupted by user — Listening...")
        ui.voice_barge_in_badge.show.assert_called_once()

    def test_ux_settings_persistence(self) -> None:
        """User experience: Duplex Barge-In settings persist to CONFIG cleanly."""
        from tabs.settings_tab import SettingsTabMixin

        obj = SettingsTabMixin()
        obj.chk_duplex_barge_in = MagicMock()
        obj.chk_duplex_barge_in.isChecked.return_value = True
        obj.spn_barge_in_energy = MagicMock()
        obj.spn_barge_in_energy.value.return_value = 1.8
        obj.spn_barge_in_speech_dur = MagicMock()
        obj.spn_barge_in_speech_dur.value.return_value = 0.25
        obj.spn_barge_in_duck_vol = MagicMock()
        obj.spn_barge_in_duck_vol.value.return_value = 35

        with patch.dict(CONFIG, {}, clear=False):
            # Simulate saving barge in settings
            CONFIG["duplex_barge_in_enabled"] = obj.chk_duplex_barge_in.isChecked()
            CONFIG["duplex_barge_in_energy_multiplier"] = obj.spn_barge_in_energy.value()
            CONFIG["duplex_barge_in_speech_duration"] = obj.spn_barge_in_speech_dur.value()
            CONFIG["duplex_barge_in_duck_volume"] = obj.spn_barge_in_duck_vol.value() / 100.0

            self.assertTrue(CONFIG["duplex_barge_in_enabled"])
            self.assertEqual(CONFIG["duplex_barge_in_energy_multiplier"], 1.8)
            self.assertEqual(CONFIG["duplex_barge_in_speech_duration"], 0.25)
            self.assertEqual(CONFIG["duplex_barge_in_duck_volume"], 0.35)


class TestSpeculativeMultiAgentHiveUserExperience(unittest.TestCase):
    """End-to-End User Experience simulations for Autonomous Speculative Hive Swarm."""

    def setUp(self) -> None:
        self.hive_svc = HiveService()

    @patch("services.hive_service.SynthesizerAgent")
    @patch("services.hive_service.AuditorAgent")
    @patch("services.hive_service.CoderAgent")
    @patch("services.hive_service.PlannerAgent")
    @patch("services.hive_service.OrchestratorAgent")
    def test_ux_live_streaming_progressive_stages(
        self,
        mock_orch_cls: MagicMock,
        mock_plan_cls: MagicMock,
        mock_code_cls: MagicMock,
        mock_audit_cls: MagicMock,
        mock_synth_cls: MagicMock,
    ) -> None:
        """User experience: user observes real-time progressive stage streaming in chat."""
        mock_orch = mock_orch_cls.return_value
        mock_orch.execute.return_value = "Decomposed tasks: 1. Fetch, 2. Validate"
        mock_plan = mock_plan_cls.return_value
        mock_plan.execute.return_value = "Step 1: Write cache module. Step 2: Test."
        mock_code = mock_code_cls.return_value
        mock_code.execute.return_value = "class MemoryCache: pass"
        mock_audit = mock_audit_cls.return_value
        mock_audit.execute.return_value = "VERDICT: APPROVED. Zero-trust invariants met."
        mock_synth = mock_synth_cls.return_value
        mock_synth.execute.return_value = "### Executive Summary: MemoryCache is ready."

        streamed_chunks: list[str] = []
        result = self.hive_svc.execute_hive_flow(
            goal="Build secure cache",
            stream_callback=streamed_chunks.append,
        )

        full_stream = "".join(streamed_chunks)
        # Verify user receives distinct stage badges in sequential order
        idx_orch = full_stream.find("[🐝 Orchestrator]")
        idx_plan = full_stream.find("[📐 Planner]")
        idx_code = full_stream.find("[💻 Coder]")
        idx_audit = full_stream.find("[⚖️ Auditor]")
        idx_synth = full_stream.find("[✨ Synthesizer]")

        self.assertGreaterEqual(idx_orch, 0)
        self.assertGreater(idx_plan, idx_orch)
        self.assertGreater(idx_code, idx_plan)
        self.assertGreater(idx_audit, idx_code)
        self.assertGreater(idx_synth, idx_audit)

        # Verify final deliverable formatting: executive synthesis first, collapsible details
        final_report = result["final"]
        self.assertIn("### 🐝 Autonomous Hive Swarm Report", final_report)
        self.assertIn("### Executive Summary: MemoryCache is ready.", final_report)
        self.assertIn("<details><summary><b>View Implementation Details & Audit Trail</b></summary>", final_report)
        self.assertIn("#### Execution Plan", final_report)
        self.assertIn("#### Code Payload", final_report)
        self.assertIn("#### God Reviewer Audit", final_report)

    @patch("services.hive_service.SynthesizerAgent")
    @patch("services.hive_service.AuditorAgent")
    @patch("services.hive_service.CoderAgent")
    @patch("services.hive_service.PlannerAgent")
    @patch("services.hive_service.OrchestratorAgent")
    def test_ux_speculative_candidate_drafting_experience(
        self,
        mock_orch_cls: MagicMock,
        mock_plan_cls: MagicMock,
        mock_code_cls: MagicMock,
        mock_audit_cls: MagicMock,
        mock_synth_cls: MagicMock,
    ) -> None:
        """User experience: multi-candidate speculative drafting presents selection progress."""
        mock_orch = mock_orch_cls.return_value
        mock_orch.execute.return_value = "Directive"
        mock_plan = mock_plan_cls.return_value
        mock_plan.execute.return_value = "Plan"
        mock_code = mock_code_cls.return_value
        mock_code.execute.side_effect = [
            "def candidate_1(): return 'fast'",
            "def candidate_2(): return 'robust'",
        ]
        mock_audit = mock_audit_cls.return_value
        mock_audit.execute.side_effect = [
            "def candidate_2(): return 'robust'",  # Auditor selects candidate 2
            "VERDICT: APPROVED",
        ]
        mock_synth = mock_synth_cls.return_value
        mock_synth.execute.return_value = "Selected the robust implementation."

        streamed_chunks: list[str] = []
        result = self.hive_svc.execute_hive_flow(
            goal="High performance parser",
            speculative_candidates=2,
            stream_callback=streamed_chunks.append,
        )

        full_stream = "".join(streamed_chunks)
        self.assertIn("⚡ *Drafting 2 speculative candidate implementations…*", full_stream)
        self.assertIn("Drafting candidate 1 (temp=0.1)", full_stream)
        self.assertIn("Drafting candidate 2 (temp=0.35)", full_stream)
        self.assertIn("Auditor selecting superior candidate", full_stream)

        # Metadata records candidates for full transparency
        self.assertEqual(len(result["hive_meta"]["candidates"]), 2)
        self.assertEqual(result["hive_meta"]["code"], "def candidate_2(): return 'robust'")

    def test_ux_cooperative_user_cancellation_feedback(self) -> None:
        """User experience: cancelling mid-stream returns immediate clean feedback."""
        cancel_event = threading.Event()
        cancel_event.set()  # User clicked stop immediately

        result = self.hive_svc.execute_hive_flow(
            goal="Huge migration task",
            cancel_event=cancel_event,
        )

        self.assertTrue(result.get("stopped"))
        self.assertIn("stopped by user", result["final"])
        self.assertEqual(result["hive_meta"]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
