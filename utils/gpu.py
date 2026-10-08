"""
utils/gpu.py — Canonical GPU (VRAM) query helper for KokertechAI.

Single source of truth for the ``nvidia-smi`` VRAM probe. Replaces the
three previously-scattered implementations (kokertechController,
services/provider_service, services/context_service) so the
"shell=True console flash + cmd.exe parsing" bug class documented in
MEMORY.md (July 2026 VRAM refactor) cannot regress independently in
one call site again.

Contract (mirrors tests/test_context_service.py::TestGetVramUsage):
- Returns int MB of used VRAM, or 0 on ANY failure.
- Never raises.
- Uses list-args subprocess (never shell=True) + CREATE_NO_WINDOW on
  Windows so no console flash appears during periodic queries.

# Version: 1.0.0 — 2026-09-21 — initial extraction from 3 call sites
"""
from __future__ import annotations

import os
import subprocess

from logging_config import get_logger

logger = get_logger(name="utils.gpu")

# Windows-only flag: suppress the console window that a naive
# subprocess call would flash during every periodic VRAM poll.
# (MEMORY.md invariant: list args + CREATE_NO_WINDOW, never shell=True.)
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_CMD = ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"]


def get_vram_usage() -> int:
    """Run ``nvidia-smi`` and return MB of used VRAM (0 on error).

    ANTI-FRAGILITY: ``FileNotFoundError`` (nvidia-smi not on PATH) is an
    ``OSError`` subclass — NOT a ``SubprocessError`` — so the probe must
    catch ``OSError`` too or the 0-on-failure contract breaks on machines
    without an NVIDIA GPU. Locked by
    tests/test_context_service.py::TestGetVramUsage.
    """
    # S603 justification: argv is the module-level CONSTANT _CMD list — no
    # untrusted input reaches this subprocess; the flag only silences ruff's
    # blanket "subprocess used" heuristic for this hardcoded, read-only probe.
    try:
        output = subprocess.check_output(_CMD, creationflags=_CREATE_NO_WINDOW).decode().strip()  # noqa: S603
        return int(output)
    except (subprocess.SubprocessError, OSError, ValueError) as e:
        logger.debug(f"get_vram_usage probe failed (graceful fallback): {e}")
        return 0
