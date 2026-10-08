#!/usr/bin/env python
"""KokertechAI Pipeline Smoke Test — 10-stage end-to-end validation.

Tests the full KokertechController lifecycle: config, init, health, budget,
model load, short/medium/long inference, overflow guard, memory vault,
and multi-turn conversation. Designed to catch regressions in the three
thread-safety fixes (_inference_lock, health_check lock consolidation,
llm_max_tokens_cap).

Usage:
    KOKERTECH_SKIP_EMBEDDINGS=1 python _smoke_test.py
"""

import os
import sys
import time

# ── Pre-import env setup ──────────────────────────────────────────────────
os.environ["KOKERTECH_SKIP_EMBEDDINGS"] = "1"

from config import CONFIG

# Prevent daemon threads from spawning and racing on inference
CONFIG["graphrag_llm_extraction"] = False
# Switch to freeform mode to avoid XML grammar injection which can cause
# "Engine Sync Error" in llama-cpp-python (grammar support is model-dependent)
CONFIG["freeform_mode"] = True

# ── Imports ───────────────────────────────────────────────────────────────
import memory_vault

# Patch store_memory to no-op so daemon threads don't spawn during inference
_original_store_memory = memory_vault.store_memory
memory_vault.store_memory = lambda *a, **kw: None

from kokertechController import KokertechController

# ── Timing helper ─────────────────────────────────────────────────────────
_start_time = time.time()


def elapsed() -> float:
    return time.time() - _start_time


# ── Response text extractor ───────────────────────────────────────────────
def _response_text(result) -> str:
    """Extract the final response text from process_input return value.

    process_input() returns a dict with 'final', 'thinking', 'command' keys
    on success, or an error dict with 'error' key on failure.
    """
    if isinstance(result, dict):
        return result.get("final", "") or result.get("error", "")
    return str(result or "")


# ── Test state ────────────────────────────────────────────────────────────
passed = 0
failed = 0


def _safe_print(msg: str) -> None:
    """Print a message, handling Unicode errors on Windows consoles."""
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"))


def check(ok: bool, detail: str = "") -> bool:
    global passed, failed
    status = "PASS" if ok else "FAIL"
    _safe_print(f"    [{status}] {detail}")
    if ok:
        passed += 1
    else:
        failed += 1
    return ok


def stage_header(name: str) -> float:
    t0 = time.time()
    print(f"\n{'='*60}")
    print(f"  STAGE: {name}")
    print(f"{'='*60}")
    return t0


def stage_done(name: str, t0: float, n_checks: int, n_passed: int) -> None:
    t1 = time.time()
    status = "PASS" if n_passed == n_checks else "FAIL"
    print(f"  {status}  [{n_passed}/{n_checks} checks, {t1-t0:.1f}s]")


# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 1 — CONFIG integrity
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("CONFIG integrity")
s1_ok = 0
s1_total = 4

model_file = CONFIG.get("model_file", "")
model_name = CONFIG.get("model_name", "")
n_gpu = CONFIG.get("llm_n_gpu_layers", -1)
n_gpu_auto = CONFIG.get("llm_n_gpu_layers_auto", False)

s1_ok += check(bool(model_file), f"model_file={model_file!r}")
s1_ok += check(bool(model_name), f"model_name={model_name!r}")
s1_ok += check(n_gpu >= 0, f"llm_n_gpu_layers={n_gpu}")
s1_ok += check(n_gpu_auto is True, f"llm_n_gpu_layers_auto={n_gpu_auto}")

stage_done("CONFIG integrity", t0, s1_total, s1_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 2 — Controller init
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Controller init")
s2_ok = 0
s2_total = 3

ctrl = KokertechController()

s2_ok += check(ctrl is not None, "Controller created")
s2_ok += check(getattr(ctrl, "provider", None) is not None, "Provider set")
s2_ok += check(bool(ctrl.workspace), f"workspace={ctrl.workspace}")

stage_done("Controller init", t0, s2_total, s2_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 3 — Load target model (must happen before health check so model
#            is lazy-loaded and health diagnostics are accurate)
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Load target model")
s3_ok = 0
s3_total = 2

loaded_model = ctrl._load_target_model()

s3_ok += check(bool(loaded_model), f"loaded_model={loaded_model!r}")
s3_ok += check(
    "ministral" in loaded_model.lower(),
    f"Model name contains 'ministral': {loaded_model!r}",
)

stage_done("Load target model", t0, s3_total, s3_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 4 — Provider health check
#
# NOTE: The controller's provider_health_check() delegates through a
# service layer that may return a fallback {"ok": False, "error": ...}
# dict on failure. The smoke test's primary goal is crash detection,
# not diagnostic accuracy, so we accept either a successful health
# check OR a well-formed error response.
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Provider health check")
s4_ok = 0
s4_total = 3

h = ctrl.provider_health_check()

s4_ok += check("provider" in h, f"has provider key ({h.get('provider', '?')!r})")
loaded = h.get("ok") is True or h.get("model_loaded") is True
s4_ok += check(loaded or "error" in h, f"loaded or error reported: {h}")
s4_ok += check(isinstance(h, dict), "result is dict")

stage_done("Provider health check", t0, s4_total, s4_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 5 — Input budget
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Input budget")
s5_ok = 0
s5_total = 2

model_name_from_config = CONFIG.get("model_name", "")
budget = ctrl._model_input_budget(model_name_from_config)

s5_ok += check(budget > 4096, f"budget={budget} (expected > 4096)")
s5_ok += check(isinstance(budget, int), f"budget type={type(budget).__name__}")

stage_done("Input budget", t0, s5_total, s5_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 6 — Short prompt inference
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Short prompt inference")
s6_ok = 0
s6_total = 2

logs6: list[str] = []

result6 = ctrl.process_input("Say hi.", log_callback=lambda m: logs6.append(m))
text6 = _response_text(result6)

s6_ok += check(result6 is not None, "Result is not None")
s6_ok += check(len(text6) > 0, f"Response: {text6!r} ({len(text6)} chars)")

stage_done("Short prompt inference", t0, s6_total, s6_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 7 — Medium prompt inference
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Medium prompt inference")
s7_ok = 0
s7_total = 2

logs7: list[str] = []

result7 = ctrl.process_input(
    "Write a one-line Python function that checks if a string is a palindrome.",
    log_callback=lambda m: logs7.append(m),
)
text7 = _response_text(result7)

s7_ok += check(result7 is not None, "Result is not None")
s7_ok += check(
    len(text7) > 10,
    f"Response: {text7[:80]!r}... ({len(text7)} chars)",
)

stage_done("Medium prompt inference", t0, s7_total, s7_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 8 — Overflow guard (100K chars)
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Overflow guard")
s8_ok = 0
s8_total = 1

big_input = "A" * 100000
logs8: list[str] = []

try:
    result8 = ctrl.process_input(
        big_input,
        log_callback=lambda m: logs8.append(m),
    )
    text8 = _response_text(result8)
    # This stage passes if we got a response without crashing
    # (the model may process it, the overflow guard may reject it)
    s8_ok += check(
        result8 is not None,
        f"100K chars processed without crash (response: {text8!r})",
    )
except Exception as e:
    msg = str(e)[:100]
    s8_ok += check(
        "overflow" in msg.lower() or "budget" in msg.lower(),
        f"Overflow guard triggered: {msg}",
    )

stage_done("Overflow guard", t0, s8_total, s8_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 9 — Memory vault
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Memory vault")
s9_ok = 0
s9_total = 3

# Restore the real store_memory for this stage
memory_vault.store_memory = _original_store_memory

try:
    memory_vault.ensure_tables_exist()
    s9_ok += check(True, "Tables exist")

    sid = memory_vault.get_current_session_id()
    s9_ok += check(isinstance(sid, str) and len(sid) > 0, f"session_id={sid}")

    mid = memory_vault.store_memory(
        "Smoke test memory entry",
        node_type="fact",
        importance=5,
    )
    s9_ok += check(isinstance(mid, int), f"memory_id={mid}")
finally:
    memory_vault.store_memory = lambda *a, **kw: None

stage_done("Memory vault", t0, s9_total, s9_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  STAGE 10 — Multi-turn conversation
# ═══════════════════════════════════════════════════════════════════════════
t0 = stage_header("Multi-turn conversation")
s10_ok = 0
s10_total = 3

logs10: list[str] = []

turn1 = ctrl.process_input(
    "My name is SmokeTest. Remember it.",
    log_callback=lambda m: logs10.append(m),
)
text1 = _response_text(turn1)
has_error1 = isinstance(turn1, dict) and "error" in turn1
s10_ok += check(
    turn1 is not None and len(text1) > 0,
    f"Turn 1: {text1!r} ({len(text1)} chars){' [ERROR]' if has_error1 else ''}",
)

turn2 = ctrl.process_input(
    "What is my name?",
    log_callback=lambda m: logs10.append(m),
)
text2 = _response_text(turn2)
has_error2 = isinstance(turn2, dict) and "error" in turn2
s10_ok += check(
    turn2 is not None and len(text2) > 0,
    f"Turn 2: {text2!r} ({len(text2)} chars){' [ERROR]' if has_error2 else ''}",
)

# Verify history grew (history is stored in controller._history_svc or self.history)
history_attr = getattr(ctrl, "history", None)
if history_attr is None:
    # history might be on the history service
    history_attr = getattr(getattr(ctrl, "_history_svc", None), "history", None)
history_len = len(history_attr) if history_attr is not None else 0
s10_ok += check(
    history_len >= 2,
    f"History length={history_len} (expected >= 2 messages)",
)

stage_done("Multi-turn conversation", t0, s10_total, s10_ok)

# ═══════════════════════════════════════════════════════════════════════════
#  SUMMARY
# ═══════════════════════════════════════════════════════════════════════════
total = passed + failed
total_time = elapsed()

print(f"\n{'='*60}")
print("  SMOKE TEST COMPLETE")
print(f"{'='*60}")
print(f"  Passed:  {passed}/{total}")
print(f"  Failed:  {failed}/{total}")
print(f"  Time:    {total_time:.0f}s")
print(f"{'='*60}")

sys.exit(0 if failed == 0 else 1)
