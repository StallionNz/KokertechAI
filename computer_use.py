"""
computer_use.py — Desktop automation via pyautogui for KokertechAI.

Sprint 5.5: Provides click, type, scroll, screenshot, and coordinate
capture with safety controls and confirmation prompts.
"""

import os

from logging_config import get_logger


logger = get_logger(name="ComputerUse")

# Module-level state
_safety_enabled = True
_confirm_callback = None
_pyautogui = None


def set_safety(enabled: bool) -> None:
    """Enable or disable the safety confirmation gate."""
    global _safety_enabled
    _safety_enabled = enabled


def set_confirm_callback(cb) -> None:
    """Register a callable that receives an action description string
    and returns ``True`` (allow) or ``False`` (block)."""
    global _confirm_callback
    _confirm_callback = cb


def _confirm(action_desc: str) -> bool:
    """Ask for user confirmation before executing an action.

    When safety is disabled, always returns ``True``.
    When safety is enabled and a callback is registered, delegates to it.
    When safety is enabled and NO callback is registered, logs a warning
    and returns ``False`` (blocks the action).
    """
    if not _safety_enabled:
        return True
    if _confirm_callback is not None:
        return _confirm_callback(action_desc)
    logger.warning(
        "Safety is ON but no confirm callback registered — blocking %r", action_desc
    )
    return False


def _check_pyautogui():
    """Return the cached pyautogui module or raise ImportError.

    The module reference is cached in ``_pyautogui`` so subsequent
    calls skip the import.  If pyautogui was never imported or the
    import failed, raises ``ImportError`` immediately.
    """
    global _pyautogui
    if _pyautogui is None:
        raise ImportError("pyautogui is not available")
    return _pyautogui


# -------------------------------------------------------------------
# Action functions
# -------------------------------------------------------------------

def click(x: int, y: int, button: str = "left") -> str:
    """Click at screen coordinates (x, y)."""
    if not _confirm(f"click at ({x}, {y})"):
        return "Click blocked by safety gate."
    try:
        pa = _check_pyautogui()
    except ImportError:
        return "pyautogui is not installed."
    try:
        pa.click(x, y, button=button)
    except (OSError, RuntimeError) as e:
        return f"click failed: {type(e).__name__}: {e}"
    return f"Clicked at ({x}, {y})"


def type_text(text: str, interval: float = 0.05) -> str:
    """Type text at the current cursor position."""
    if not _confirm(f"type text ({len(text)} chars)"):
        return "Type text blocked by safety gate."
    try:
        pa = _check_pyautogui()
    except ImportError:
        return "pyautogui is not installed."
    try:
        pa.typewrite(text, interval=interval)
    except (OSError, RuntimeError) as e:
        return f"type_text failed: {type(e).__name__}: {e}"
    return f"Typed {len(text)} characters"


def scroll(clicks: int) -> str:
    """Scroll the mouse wheel by *clicks* units."""
    if not _confirm(f"scroll ({clicks} clicks)"):
        return "Scroll blocked by safety gate."
    try:
        pa = _check_pyautogui()
    except ImportError:
        return "pyautogui is not installed."
    try:
        pa.scroll(clicks)
    except (OSError, RuntimeError) as e:
        return f"scroll failed: {type(e).__name__}: {e}"
    return f"Scrolled {clicks} clicks"


def move_to(x: int, y: int) -> str:
    """Move the mouse cursor to (x, y)."""
    if not _confirm(f"move mouse to ({x}, {y})"):
        return "Move blocked by safety gate."
    try:
        pa = _check_pyautogui()
    except ImportError:
        return "pyautogui is not installed."
    try:
        pa.moveTo(x, y)
    except (OSError, RuntimeError) as e:
        return f"move_to failed: {type(e).__name__}: {e}"
    return f"Moved to ({x}, {y})"


def screenshot(path: str = None) -> str:
    """Take a screenshot and save it to *path*.

    When *path* is ``None``, saves to ``kokertech_screenshot.png`` in
    the current working directory.
    """
    if path is None:
        path = os.path.join(os.getcwd(), "kokertech_screenshot.png")
    if not _confirm(f"take screenshot → {path}"):
        return "Screenshot blocked by safety gate."
    try:
        pa = _check_pyautogui()
    except ImportError:
        return "pyautogui is not installed."
    try:
        pa.screenshot(path)
    except (OSError, RuntimeError) as e:
        return f"screenshot failed: {type(e).__name__}: {e}"
    return f"Screenshot saved to {path}"


def hotkey(*keys: str) -> str:
    """Press a keyboard shortcut (e.g. ``hotkey('ctrl', 'c')``)."""
    desc = " + ".join(keys)
    if not _confirm(f"press hotkey ({desc})"):
        return "Hotkey blocked by safety gate."
    try:
        pa = _check_pyautogui()
    except ImportError:
        return "pyautogui is not installed."
    try:
        pa.hotkey(*keys)
    except (OSError, RuntimeError) as e:
        return f"hotkey failed: {type(e).__name__}: {e}"
    return f"Pressed {desc}"


def get_position() -> str:
    """Return the current mouse position as a string."""
    try:
        pa = _check_pyautogui()
    except ImportError:
        return "pyautogui is not installed."
    try:
        pos = pa.position()
    except (OSError, RuntimeError) as e:
        return f"get_position failed: {type(e).__name__}: {e}"
    return f"Position: ({pos.x}, {pos.y})"
