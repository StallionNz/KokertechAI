"""services/sensory_service.py — Local Multimodal Sensory Mesh Service.

Realm 1: Local Multimodal Sensory Mesh.
Provides in-process, zero-disk desktop framebuffer perception and visual context
extraction for KokertechAI.

Features:
- Fast in-memory framebuffer capture via mss / PIL.ImageGrab with zero disk writes.
- Windows active foreground window coordinate detection and safe virtual screen bounding.
- In-memory aspect-ratio-preserving downsampling and base64 JPEG encoding.
- Perceptual frame hashing and frame diffing to eliminate redundant vision inference.
- Autonomous sensory query routing (<<SEE>>, <<VISION:...>>, /see, and natural language).
- Multi-backend vision analysis with structured scene metadata fallback.
"""

from __future__ import annotations

import base64
import hashlib
import io
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from config import CONFIG
from logging_config import get_logger

logger = get_logger(name="SensoryService")

_SENSORY_TAG_PATTERN = re.compile(
    r"^\s*(?:<<SEE>>|<<SEE:(.+)>>|<<VISION:(.+)>>|/see(?:\s+(.+))?)\s*$",
    re.IGNORECASE | re.DOTALL,
)

_WINDOW_CLAUSE_PATTERN = re.compile(
    r"""window\s*\(\s*["']?([^"')]+)["']?\s*\)""",
    re.IGNORECASE,
)
_MONITOR_CLAUSE_PATTERN = re.compile(
    r"""monitor\s*\(\s*(\d+)\s*\)""",
    re.IGNORECASE,
)
_REGION_CLAUSE_PATTERN = re.compile(
    r"""region\s*\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)""",
    re.IGNORECASE,
)

_NATURAL_VISION_KEYWORDS = (
    "screen",
    "screenshot",
    "display",
    "window",
    "desktop",
    "monitor",
)

_NATURAL_VISION_VERBS = (
    "look",
    "see",
    "show",
    "describe",
    "read",
    "inspect",
    "analyze",
    "what is on",
    "what's on",
)


@dataclass
class FrameData:
    """In-memory representation of a captured desktop framebuffer."""

    raw_bytes: bytes
    b64_data: str
    width: int
    height: int
    frame_hash: str
    timestamp: str
    target: str
    window_title: str = ""


class SensoryService:
    """Manages in-process desktop framebuffer perception and multimodal routing."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_frame_hash: Optional[str] = None
        self._last_frame_data: Optional[FrameData] = None

    def compute_frame_hash(self, raw_bytes: bytes) -> str:
        """Compute a compact 16-character SHA-256 hash for frame diffing."""
        if not raw_bytes:
            return ""
        return hashlib.sha256(raw_bytes).hexdigest()[:16]

    def is_frame_unchanged(self, new_hash: str) -> bool:
        """Check if the captured frame is identical to the previously processed frame."""
        with self._lock:
            if not self._last_frame_hash or not new_hash:
                return False
            return self._last_frame_hash == new_hash

    def update_last_frame(self, frame: FrameData) -> None:
        """Cache the most recent frame and its perceptual hash."""
        with self._lock:
            self._last_frame_hash = frame.frame_hash
            self._last_frame_data = frame

    def get_last_frame(self) -> Optional[FrameData]:
        """Return the cached last frame if available."""
        with self._lock:
            return self._last_frame_data

    def clear_cache(self) -> None:
        """Reset the cached frame hash and data."""
        with self._lock:
            self._last_frame_hash = None
            self._last_frame_data = None

    def should_route_sensory(self, text: str) -> Tuple[bool, str, str]:
        """Detect whether a query requests visual screen perception.

        Returns:
            (is_sensory, clean_query, target_mode)
            where target_mode is one of:
            - 'active_window'
            - 'primary'
            - 'full'
            - 'window:<title>'
            - 'monitor:<index>'
            - 'region:<x>,<y>,<w>,<h>'
        """
        if not text or not isinstance(text, str):
            return False, "", "active_window"

        stripped = text.strip()

        def _extract_target_and_clean_query(raw_q: str, default_fallback_prompt: str) -> Tuple[str, str]:
            target = "active_window"
            cleaned = raw_q

            m_win = _WINDOW_CLAUSE_PATTERN.search(cleaned)
            if m_win:
                target = f"window:{m_win.group(1).strip()}"
                cleaned = _WINDOW_CLAUSE_PATTERN.sub("", cleaned).strip(" :,\t\r\n")

            m_mon = _MONITOR_CLAUSE_PATTERN.search(cleaned)
            if m_mon and not m_win:
                target = f"monitor:{m_mon.group(1).strip()}"
                cleaned = _MONITOR_CLAUSE_PATTERN.sub("", cleaned).strip(" :,\t\r\n")

            m_reg = _REGION_CLAUSE_PATTERN.search(cleaned)
            if m_reg and not m_win and not m_mon:
                target = (
                    f"region:{m_reg.group(1).strip()},{m_reg.group(2).strip()},"
                    f"{m_reg.group(3).strip()},{m_reg.group(4).strip()}"
                )
                cleaned = _REGION_CLAUSE_PATTERN.sub("", cleaned).strip(" :,\t\r\n")

            if target == "active_window":
                lower = cleaned.lower()
                if "full screen" in lower or "all monitors" in lower:
                    target = "full"
                elif "primary" in lower:
                    target = "primary"

            if not cleaned:
                if target.startswith("window:"):
                    cleaned = f"Describe what is on window '{target[7:]}' in detail, listing key UI elements and text."
                elif target.startswith("monitor:"):
                    cleaned = f"Describe what is on monitor {target[8:]} in detail, listing key UI elements and text."
                elif target.startswith("region:"):
                    cleaned = f"Describe what is in region {target[7:]} in detail, listing key UI elements and text."
                else:
                    cleaned = default_fallback_prompt

            return target, cleaned

        # 1. Explicit tags or slash commands
        m = _SENSORY_TAG_PATTERN.match(stripped)
        if m:
            raw_query = (m.group(1) or m.group(2) or m.group(3) or "").strip()
            default_prompt = "Describe what is on my screen in detail, listing key UI elements and text."
            target, query = _extract_target_and_clean_query(raw_query, default_prompt)
            return True, query, target

        # 2. Natural language heuristic routing when sensory_mesh_enabled is True
        if CONFIG.get("sensory_mesh_enabled", False):
            lower = stripped.lower()
            has_keyword = any(kw in lower for kw in _NATURAL_VISION_KEYWORDS)
            has_verb = any(v in lower for v in _NATURAL_VISION_VERBS)
            has_target_syntax = bool(
                _WINDOW_CLAUSE_PATTERN.search(stripped)
                or _MONITOR_CLAUSE_PATTERN.search(stripped)
                or _REGION_CLAUSE_PATTERN.search(stripped)
            )
            if (has_keyword and has_verb) or has_target_syntax:
                target, query = _extract_target_and_clean_query(stripped, stripped)
                return True, query, target

        return False, "", "active_window"

    def _resolve_window_by_title(
        self, title_substr: str
    ) -> Tuple[Optional[Dict[str, int]], str]:
        """Inspect visible top-level windows on Windows and return bounds for the first matching title."""
        if not title_substr:
            return None, ""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found_bounds: Optional[Dict[str, int]] = None
            found_title: str = ""
            substr_lower = title_substr.lower().strip()

            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def enum_windows_proc(hwnd, _lparam):
                nonlocal found_bounds, found_title
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                title = buff.value or ""
                if substr_lower in title.lower():
                    rect = wintypes.RECT()
                    if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                        left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
                        width = max(10, right - left)
                        height = max(10, bottom - top)
                        found_bounds = {"left": left, "top": top, "width": width, "height": height}
                        found_title = title
                        return False  # Stop enumeration
                return True

            cb = WNDENUMPROC(enum_windows_proc)
            user32.EnumWindows(cb, 0)
            return found_bounds, found_title
        except (AttributeError, OSError, ValueError, TypeError):
            return None, ""

    def _resolve_active_window_bounds(self) -> Tuple[Optional[Dict[str, int]], str]:
        """Inspect the foreground window on Windows and return its bounding box and title."""
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return None, ""

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return None, ""

            length = user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value or ""

            left, top, right, bottom = rect.left, rect.top, rect.right, rect.bottom
            width = max(10, right - left)
            height = max(10, bottom - top)

            return {"left": left, "top": top, "width": width, "height": height}, title
        except (AttributeError, OSError, ValueError, TypeError):
            return None, ""

    def capture_framebuffer(
        self,
        target: str = "active_window",
        max_dim: Optional[int] = None,
        allow_synthetic_fallback: bool = True,
    ) -> Optional[FrameData]:
        """Capture the desktop framebuffer directly into an in-memory FrameData object.

        Zero disk writes: image bytes and base64 data are produced entirely in RAM.

        Args:
            target: 'active_window', 'primary', 'full', 'window:<title>',
                'monitor:<index>', or 'region:<x>,<y>,<w>,<h>'.
            max_dim: Maximum width/height for downsampling (defaults to config: 1024).
            allow_synthetic_fallback: If True, provides a valid placeholder frame
                when running in headless or non-interactive environments where
                display BitBlt is unavailable.

        Returns:
            FrameData object, or None if capture fails.
        """
        limit_dim = max_dim or CONFIG.get("sensory_max_dimension", 1024)
        target_mode = target or CONFIG.get("sensory_capture_target", "active_window")
        window_title = ""
        bounds: Optional[Dict[str, int]] = None

        if target_mode.startswith("window:"):
            title_search = target_mode[7:].strip()
            bounds, window_title = self._resolve_window_by_title(title_search)
            if not bounds:
                logger.warning(
                    "Target window '%s' not found; falling back to active window.",
                    title_search,
                )
                bounds, window_title = self._resolve_active_window_bounds()
        elif target_mode.startswith("region:"):
            try:
                coords = [int(p.strip()) for p in target_mode[7:].split(",")]
                if len(coords) == 4:
                    bounds = {
                        "left": coords[0],
                        "top": coords[1],
                        "width": max(10, coords[2]),
                        "height": max(10, coords[3]),
                    }
                    window_title = f"Region ({coords[0]},{coords[1]},{coords[2]},{coords[3]})"
            except (ValueError, IndexError):
                bounds = None
        elif target_mode == "active_window":
            bounds, window_title = self._resolve_active_window_bounds()

        try:
            from PIL import Image
        except ImportError:
            logger.warning("Pillow (PIL) is not installed; framebuffer capture unavailable.")
            return None

        pil_image: Optional[Image.Image] = None

        # Strategy 1: Fast in-process capture via mss
        try:
            import mss
            from mss.exception import ScreenShotError

            with mss.MSS() as sct:
                monitors = sct.monitors
                bbox = None

                if bounds:
                    bbox = bounds
                elif target_mode.startswith("monitor:"):
                    try:
                        mon_idx = int(target_mode[8:].strip())
                    except ValueError:
                        mon_idx = 1
                    if 0 <= mon_idx < len(monitors):
                        bbox = monitors[mon_idx]
                    elif len(monitors) > 1:
                        bbox = monitors[1]
                    else:
                        bbox = monitors[0]
                    window_title = f"Monitor {mon_idx}"
                elif target_mode == "full":
                    bbox = monitors[0]
                    window_title = "Full Desktop"
                elif target_mode == "primary":
                    bbox = monitors[1] if len(monitors) > 1 else monitors[0]
                    window_title = "Primary Monitor"
                else:  # Fallback
                    bbox = monitors[1] if len(monitors) > 1 else monitors[0]

                if bbox:
                    sct_img = sct.grab(bbox)
                    pil_image = Image.frombytes("RGB", sct_img.size, sct_img.rgb)
        except (ImportError, OSError, RuntimeError, ValueError, KeyError, ScreenShotError) as exc:
            logger.debug(f"mss framebuffer capture skipped or failed: {exc}")

        # Strategy 2: PIL.ImageGrab fallback
        if pil_image is None:
            try:
                from PIL import ImageGrab

                if bounds:
                    pil_image = ImageGrab.grab(
                        bbox=(
                            bounds["left"],
                            bounds["top"],
                            bounds["left"] + bounds["width"],
                            bounds["top"] + bounds["height"],
                        ),
                        all_screens=True,
                    )
                else:
                    pil_image = ImageGrab.grab(all_screens=True)
            except (ImportError, OSError, RuntimeError, ValueError) as exc:
                logger.debug(f"ImageGrab fallback failed: {exc}")

        # Strategy 3: Qt screen grab if QApplication is active
        if pil_image is None:
            try:
                from PyQt6.QtGui import QGuiApplication

                primary_screen = QGuiApplication.primaryScreen()
                if primary_screen:
                    pixmap = primary_screen.grabWindow(0)
                    if not pixmap.isNull():
                        qimg = pixmap.toImage()
                        buffer = qimg.bits().asstring(qimg.sizeInBytes())
                        pil_image = Image.frombuffer(
                            "RGBA", (qimg.width(), qimg.height()), buffer, "raw", "RGBA", 0, 1
                        ).convert("RGB")
            except (ImportError, RuntimeError, TypeError, AttributeError, OSError) as exc:
                logger.debug(f"Qt screen grab fallback failed: {exc}")

        # Strategy 4: Synthetic placeholder for headless / non-interactive testing
        if pil_image is None and allow_synthetic_fallback:
            logger.debug("Generating synthetic fallback framebuffer for headless/sandbox environment")
            pil_image = Image.new("RGB", (640, 480), color=(24, 28, 36))
            window_title = "Headless Desktop Session"

        if pil_image is None:
            logger.warning("All framebuffer capture strategies failed.")
            return None

        # Downsample preserving aspect ratio if exceeding limit_dim
        w, h = pil_image.size
        if max(w, h) > limit_dim:
            pil_image.thumbnail((limit_dim, limit_dim), Image.Resampling.LANCZOS)
            w, h = pil_image.size

        # Convert to in-memory JPEG bytes
        byte_buffer = io.BytesIO()
        pil_image.save(byte_buffer, format="JPEG", quality=85)
        raw_bytes = byte_buffer.getvalue()
        b64_data = base64.b64encode(raw_bytes).decode("ascii")
        frame_hash = self.compute_frame_hash(raw_bytes)
        now_ts = datetime.now(timezone.utc).isoformat()

        frame = FrameData(
            raw_bytes=raw_bytes,
            b64_data=b64_data,
            width=w,
            height=h,
            frame_hash=frame_hash,
            timestamp=now_ts,
            target=target_mode,
            window_title=window_title,
        )

        self.update_last_frame(frame)
        return frame

    def analyze_frame(
        self,
        frame: FrameData,
        prompt: str = "Describe what you see on this screen in detail.",
        vision_model: Optional[str] = None,
        timeout: int = 45,
    ) -> str:
        """Send the in-memory frame to the local AI provider vision model for analysis.

        Returns:
            Description string from the vision model, or structured scene metadata.
        """
        if not frame or not frame.b64_data:
            return "No valid framebuffer available for analysis."

        target_model = vision_model or CONFIG.get("vision_model", "")
        if not target_model:
            target_model = CONFIG.get("model_name", "")

        try:
            from ai_base import get_provider

            provider_name = CONFIG.get("active_provider", "local_llm")
            provider = get_provider(name=provider_name)

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{frame.b64_data}"
                            },
                        },
                    ],
                }
            ]

            result = provider.chat_completion(
                messages=messages,
                model=target_model,
                max_tokens=600,
                timeout=timeout,
            )

            if isinstance(result, dict):
                if result.get("error"):
                    err = result["error"]
                    logger.debug(f"Vision provider returned error: {err}")
                    return self._build_metadata_description(frame, prompt, error=err)
                content = result.get("content", "")
                if content:
                    return content

            return self._build_metadata_description(frame, prompt)

        except (RuntimeError, ValueError, OSError, TypeError, KeyError) as exc:
            logger.warning(f"Vision analysis failed ({exc}); returning visual metadata descriptor")
            return self._build_metadata_description(frame, prompt, error=str(exc))

    def extract_ocr_text(self, raw_bytes: bytes) -> str:
        """Extract text from in-memory image bytes using optical character recognition.

        Zero disk writes: reads directly from memory buffer.
        """
        if not raw_bytes:
            return ""
        try:
            from PIL import Image
            import pytesseract

            img = Image.open(io.BytesIO(raw_bytes))
            text = pytesseract.image_to_string(img)
            return text.strip()
        except (ImportError, OSError, RuntimeError, ValueError, TypeError) as exc:
            logger.debug("In-process OCR extraction skipped or failed: %s", exc)
            return ""

    def _build_metadata_description(
        self, frame: FrameData, prompt: str, error: Optional[str] = None
    ) -> str:
        """Construct structured visual observation text when direct vision is unavailable."""
        title_info = f" Foreground Window: '{frame.window_title}'." if frame.window_title else ""
        err_info = f" (Vision inference notice: {error})" if error else ""
        ocr_text = self.extract_ocr_text(frame.raw_bytes)
        ocr_section = (
            f"\n[ON-SCREEN OCR TEXT DETECTED]:\n{ocr_text}\n[END OCR TEXT]"
            if ocr_text
            else ""
        )
        return (
            f"[VISUAL PERCEPTION: Framebuffer captured ({frame.target}, {frame.width}x{frame.height}) "
            f"at {frame.timestamp}. Hash: {frame.frame_hash}.{title_info}{err_info} "
            f"User Query: {prompt}]{ocr_section}"
        )
