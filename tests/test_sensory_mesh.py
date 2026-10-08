"""tests/test_sensory_mesh.py — Unit and regression tests for Local Multimodal Sensory Mesh.

Realm 1: Local Multimodal Sensory Mesh.
Validates:
- In-process desktop framebuffer capture (RAM-only, zero disk writes).
- Perceptual frame hashing and frame diffing cache.
- Explicit and natural language sensory query routing.
- Downsampling and aspect-ratio preservation.
- Multimodal vision model integration with graceful metadata fallback.
- Controller visual context injection.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from config import CONFIG
from services.sensory_service import FrameData, SensoryService


class TestSensoryRouting(unittest.TestCase):
    """REGRESSION GUARD for SensoryService routing in services/sensory_service.py."""

    def setUp(self) -> None:
        self.svc = SensoryService()

    def test_should_route_see_empty(self) -> None:
        """REGRESSION GUARD: <<SEE>> tag triggers sensory capture with default prompt."""
        is_sensory, query, target = self.svc.should_route_sensory("<<SEE>>")
        self.assertTrue(is_sensory)
        self.assertIn("Describe what is on my screen", query)
        self.assertEqual(target, "active_window")

    def test_should_route_see_query(self) -> None:
        """REGRESSION GUARD: <<SEE: ... >> captures custom prompt."""
        is_sensory, query, target = self.svc.should_route_sensory("<<SEE: What color is the submit button?>>")
        self.assertTrue(is_sensory)
        self.assertEqual(query, "What color is the submit button?")
        self.assertEqual(target, "active_window")

    def test_should_route_vision_tag(self) -> None:
        """REGRESSION GUARD: <<VISION: ... >> syntax routing."""
        is_sensory, query, target = self.svc.should_route_sensory("<<VISION: Read the active error dialog>>")
        self.assertTrue(is_sensory)
        self.assertEqual(query, "Read the active error dialog")
        self.assertEqual(target, "active_window")

    def test_should_route_slash_see(self) -> None:
        """REGRESSION GUARD: /see slash command routing."""
        is_sensory, query, target = self.svc.should_route_sensory("/see inspect chart layout")
        self.assertTrue(is_sensory)
        self.assertEqual(query, "inspect chart layout")
        self.assertEqual(target, "active_window")

    def test_should_detect_target_monitors(self) -> None:
        """REGRESSION GUARD: Query specifying full screen or primary switches capture target."""
        _, _, target_full = self.svc.should_route_sensory("<<SEE: check full screen for open tabs>>")
        self.assertEqual(target_full, "full")

        _, _, target_primary = self.svc.should_route_sensory("<<SEE: inspect primary monitor>>")
        self.assertEqual(target_primary, "primary")

    def test_should_route_targeted_window(self) -> None:
        """REGRESSION GUARD: Targeted window clause <<SEE:window(...)>> routing."""
        is_sensory, query, target = self.svc.should_route_sensory('<<SEE:window("Calculator")>>')
        self.assertTrue(is_sensory)
        self.assertEqual(target, "window:Calculator")
        self.assertIn("Calculator", query)

        is_sensory2, query2, target2 = self.svc.should_route_sensory('<<SEE:window("VS Code"): Check terminal errors>>')
        self.assertTrue(is_sensory2)
        self.assertEqual(target2, "window:VS Code")
        self.assertEqual(query2, "Check terminal errors")

    def test_should_route_targeted_monitor(self) -> None:
        """REGRESSION GUARD: Targeted monitor clause <<SEE:monitor(N)>> routing."""
        is_sensory, query, target = self.svc.should_route_sensory("<<SEE:monitor(2)>>")
        self.assertTrue(is_sensory)
        self.assertEqual(target, "monitor:2")
        self.assertIn("monitor 2", query)

        is_sensory2, query2, target2 = self.svc.should_route_sensory("<<SEE:monitor(1): Read error dialog>>")
        self.assertTrue(is_sensory2)
        self.assertEqual(target2, "monitor:1")
        self.assertEqual(query2, "Read error dialog")

    def test_should_route_targeted_region(self) -> None:
        """REGRESSION GUARD: Targeted region clause <<SEE:region(x,y,w,h)>> routing."""
        is_sensory, query, target = self.svc.should_route_sensory("<<SEE:region(100, 200, 400, 300): Check graph>>")
        self.assertTrue(is_sensory)
        self.assertEqual(target, "region:100,200,400,300")
        self.assertEqual(query, "Check graph")

    def test_should_route_natural_language_when_enabled(self) -> None:
        """REGRESSION GUARD: Natural language vision triggers when sensory_mesh_enabled=True."""
        with patch.dict(CONFIG, {"sensory_mesh_enabled": True}):
            is_sensory, query, target = self.svc.should_route_sensory("Please look at my screen and tell me what you see")
            self.assertTrue(is_sensory)
            self.assertEqual(target, "active_window")

    def test_should_not_route_normal_when_disabled(self) -> None:
        """ANTI-FRAGILITY: Standard conversational queries bypass sensory mesh."""
        with patch.dict(CONFIG, {"sensory_mesh_enabled": False}):
            is_sensory, query, target = self.svc.should_route_sensory("What is the capital of New Zealand?")
            self.assertFalse(is_sensory)
            self.assertEqual(query, "")

    def test_should_not_route_empty_or_invalid(self) -> None:
        """ANTI-FRAGILITY: Empty or None input safely returns False."""
        self.assertEqual(self.svc.should_route_sensory(""), (False, "", "active_window"))
        self.assertEqual(self.svc.should_route_sensory("   "), (False, "", "active_window"))
        self.assertEqual(self.svc.should_route_sensory(None), (False, "", "active_window"))  # type: ignore[arg-type]


class TestSensoryFramebuffer(unittest.TestCase):
    """Test suite for in-memory framebuffer capture and perceptual diffing."""

    def setUp(self) -> None:
        self.svc = SensoryService()

    def test_capture_framebuffer_synthetic_fallback(self) -> None:
        """REGRESSION GUARD: Synthetic fallback produces valid in-memory FrameData."""
        # Force fallback by mocking mss and ImageGrab to fail
        with patch("mss.MSS", side_effect=OSError("No GDI display")):
            with patch("PIL.ImageGrab.grab", side_effect=OSError("No screen grab")):
                frame = self.svc.capture_framebuffer(target="active_window", allow_synthetic_fallback=True)

        self.assertIsNotNone(frame)
        assert frame is not None  # type narrowing
        self.assertGreater(frame.width, 0)
        self.assertGreater(frame.height, 0)
        self.assertGreater(len(frame.raw_bytes), 0)
        self.assertGreater(len(frame.b64_data), 0)
        self.assertEqual(len(frame.frame_hash), 16)
        self.assertTrue(frame.timestamp.endswith("+00:00") or "Z" in frame.timestamp or "T" in frame.timestamp)

    def test_frame_hashing_and_diffing(self) -> None:
        """REGRESSION GUARD: Frame diffing accurately detects unchanged frames."""
        dummy_bytes = b"fake-frame-content-123"
        h1 = self.svc.compute_frame_hash(dummy_bytes)
        self.assertEqual(len(h1), 16)

        dummy_frame = FrameData(
            raw_bytes=dummy_bytes,
            b64_data="ZmFrZQ==",
            width=100,
            height=100,
            frame_hash=h1,
            timestamp="2026-09-30T12:00:00Z",
            target="active_window",
        )

        self.assertFalse(self.svc.is_frame_unchanged(h1))
        self.svc.update_last_frame(dummy_frame)
        self.assertTrue(self.svc.is_frame_unchanged(h1))
        self.assertFalse(self.svc.is_frame_unchanged("different-hash-456"))

        self.svc.clear_cache()
        self.assertFalse(self.svc.is_frame_unchanged(h1))

    def test_downsampling_preserves_aspect_ratio(self) -> None:
        """ANTI-FRAGILITY: Frames larger than max_dim are downsampled proportionally."""
        large_img = Image.new("RGB", (2000, 1000), color=(50, 50, 50))
        # mss (capture Strategy 1) must fail so the patched ImageGrab
        # (Strategy 2) is exercised -- otherwise a real monitor screenshot
        # leaks into the assertion on machines with a working mss backend.
        with patch("mss.MSS", side_effect=OSError("No GDI display")):
            with patch("PIL.ImageGrab.grab", return_value=large_img):
                frame = self.svc.capture_framebuffer(max_dim=500)

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.width, 500)
        self.assertEqual(frame.height, 250)

    def test_capture_framebuffer_targeted_region(self) -> None:
        """REGRESSION GUARD: Region capture parses coordinates and crops bounds."""
        with patch("mss.MSS", side_effect=OSError("No GDI display")):
            with patch("PIL.ImageGrab.grab") as mock_grab:
                mock_grab.return_value = Image.new("RGB", (300, 200), color=(10, 20, 30))
                frame = self.svc.capture_framebuffer(target="region:100,150,300,200")

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.target, "region:100,150,300,200")
        self.assertIn("Region", frame.window_title)

    def test_capture_framebuffer_targeted_window(self) -> None:
        """REGRESSION GUARD: Window targeting queries window title and uses bounding rect."""
        with patch.object(
            self.svc,
            "_resolve_window_by_title",
            return_value=({"left": 50, "top": 60, "width": 400, "height": 300}, "Calculator"),
        ):
            with patch("mss.MSS", side_effect=OSError("No GDI display")):
                with patch("PIL.ImageGrab.grab") as mock_grab:
                    mock_grab.return_value = Image.new("RGB", (400, 300), color=(5, 5, 5))
                    frame = self.svc.capture_framebuffer(target="window:Calculator")

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.target, "window:Calculator")
        self.assertEqual(frame.window_title, "Calculator")

    def test_capture_framebuffer_targeted_monitor(self) -> None:
        """REGRESSION GUARD: Monitor index selects the appropriate display monitor."""
        mock_sct = MagicMock()
        mock_sct.monitors = [
            {"left": 0, "top": 0, "width": 3840, "height": 1080},
            {"left": 0, "top": 0, "width": 1920, "height": 1080},
            {"left": 1920, "top": 0, "width": 1920, "height": 1080},
        ]
        grabbed_mock = MagicMock()
        grabbed_mock.size = (1920, 1080)
        grabbed_mock.rgb = b"\x00" * (1920 * 1080 * 3)
        mock_sct.grab.return_value = grabbed_mock

        with patch("mss.MSS", return_value=MagicMock(__enter__=MagicMock(return_value=mock_sct), __exit__=MagicMock())):
            frame = self.svc.capture_framebuffer(target="monitor:2")

        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.target, "monitor:2")
        mock_sct.grab.assert_called_with(mock_sct.monitors[2])


class TestSensoryAnalysis(unittest.TestCase):
    """Test suite for vision analysis and metadata generation."""

    def setUp(self) -> None:
        self.svc = SensoryService()
        self.dummy_frame = FrameData(
            raw_bytes=b"jpeg-bytes",
            b64_data="anBlZy1ieXRlcw==",
            width=800,
            height=600,
            frame_hash="abcd1234efgh5678",
            timestamp="2026-09-30T12:00:00Z",
            target="active_window",
            window_title="Kokertech Dashboard",
        )

    def test_analyze_frame_with_provider_mock(self) -> None:
        """REGRESSION GUARD: Vision model receives multimodal message structure."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.return_value = {
            "content": "I see a dashboard with telemetry charts.",
            "error": None,
        }

        with patch("ai_base.get_provider", return_value=mock_provider):
            res = self.svc.analyze_frame(
                frame=self.dummy_frame,
                prompt="What is open?",
                vision_model="llava-v1.6-7b",
            )

        self.assertEqual(res, "I see a dashboard with telemetry charts.")
        mock_provider.chat_completion.assert_called_once()
        call_kwargs = mock_provider.chat_completion.call_args[1]
        msgs = call_kwargs["messages"]
        self.assertEqual(msgs[0]["content"][0]["text"], "What is open?")
        self.assertIn("data:image/jpeg;base64,", msgs[0]["content"][1]["image_url"]["url"])

    def test_analyze_frame_fallback_on_error(self) -> None:
        """REGRESSION GUARD: When vision provider fails, returns structured metadata description."""
        mock_provider = MagicMock()
        mock_provider.chat_completion.side_effect = RuntimeError("Vision adapter unavailable")

        with patch("ai_base.get_provider", return_value=mock_provider):
            res = self.svc.analyze_frame(frame=self.dummy_frame, prompt="Describe UI")

        self.assertIn("[VISUAL PERCEPTION: Framebuffer captured", res)
        self.assertIn("Kokertech Dashboard", res)
        self.assertIn("Vision adapter unavailable", res)

    def test_ocr_text_extraction_and_fallback_injection(self) -> None:
        """REGRESSION GUARD: Fast-OCR extracts on-screen text and injects into fallback description."""
        buf = io.BytesIO()
        Image.new("RGB", (64, 64), color="white").save(buf, format="PNG")
        valid_bytes = buf.getvalue()

        with patch("pytesseract.image_to_string", return_value="Error: Connection refused\nPort: 8080"):
            extracted = self.svc.extract_ocr_text(valid_bytes)
            self.assertIn("Error: Connection refused", extracted)

        with patch.object(self.svc, "extract_ocr_text", return_value="Error 404 Not Found"):
            desc = self.svc._build_metadata_description(self.dummy_frame, prompt="Check error")
            self.assertIn("[ON-SCREEN OCR TEXT DETECTED]:", desc)
            self.assertIn("Error 404 Not Found", desc)
            self.assertIn("[END OCR TEXT]", desc)


class TestMultimodalNormalization(unittest.TestCase):
    """Test suite for multimodal message preservation and coalescing in ai_base.py."""

    def test_normalize_preserves_multimodal_list(self) -> None:
        """REGRESSION GUARD: _normalize_chat_messages preserves list content blocks."""
        from ai_base import _normalize_chat_messages

        multimodal_content = [
            {"type": "text", "text": "Describe this screenshot"},
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,ZmFrZQ=="}},
        ]
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": multimodal_content},
        ]
        normalized = _normalize_chat_messages(messages)
        self.assertEqual(len(normalized), 2)
        self.assertEqual(normalized[0]["role"], "system")
        self.assertEqual(normalized[1]["role"], "user")
        self.assertIsInstance(normalized[1]["content"], list)
        self.assertEqual(normalized[1]["content"], multimodal_content)

    def test_coalescing_multimodal_messages(self) -> None:
        """REGRESSION GUARD: Consecutive user messages with multimodal content merge cleanly."""
        from ai_base import _normalize_chat_messages

        messages = [
            {"role": "user", "content": "Here is image 1:"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "url1"}}]},
        ]
        normalized = _normalize_chat_messages(messages)
        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["role"], "user")
        self.assertIsInstance(normalized[0]["content"], list)
        self.assertEqual(len(normalized[0]["content"]), 2)
        self.assertEqual(normalized[0]["content"][0]["text"], "Here is image 1:")
        self.assertEqual(normalized[0]["content"][1]["image_url"]["url"], "url1")


class TestControllerSensoryIntegration(unittest.TestCase):
    """Test suite for KokertechController integration with SensoryService."""

    def test_controller_process_input_injects_sensory(self) -> None:
        """REGRESSION GUARD: Controller process_input detects <<SEE>> and injects visual context."""
        with patch("kokertechController.get_provider"):
            from kokertechController import KokertechController
            ctrl = KokertechController()

        mock_sensory = MagicMock()
        mock_sensory.should_route_sensory.return_value = (True, "Inspect my screen", "active_window")
        mock_sensory.capture_framebuffer.return_value = FrameData(
            raw_bytes=b"bytes",
            b64_data="b64",
            width=1024,
            height=768,
            frame_hash="hash123",
            timestamp="2026-09-30T12:00:00Z",
            target="active_window",
        )
        mock_sensory.analyze_frame.return_value = "VS Code editor is open with Python files."
        ctrl._sensory_svc = mock_sensory

        with patch.object(ctrl, "_execute_with_fallback", return_value=({"content": "I see your code editor.", "error": None}, "local_llm")) as mock_exec:
            with patch("memory_vault.store_memory"):
                res = ctrl.process_input("<<SEE: Inspect my screen>>")

        self.assertEqual(res["final"], "I see your code editor.")
        mock_sensory.capture_framebuffer.assert_called_once_with(target="active_window")
        # Check that sensory observation was injected into messages
        call_kwargs = mock_exec.call_args[1]
        system_msg = call_kwargs["messages"][0]["content"]
        self.assertIn("[LOCAL SENSORY MESH OBSERVATION]:", system_msg)
        self.assertIn("VS Code editor is open with Python files.", system_msg)


if __name__ == "__main__":
    unittest.main()
