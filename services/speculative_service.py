"""services/speculative_service.py — Speculative Drafting & Verification Engine.

Realm 3 (Autonomous Speculative Multi-Agent Hive):
Provides fast preliminary token drafting (via local SLM or fast heuristic model)
coupled with primary model verification and refinement. If no secondary draft
model is configured or available, cascades gracefully to single-pass generation
with zero latency overhead.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from config import CONFIG
from logging_config import get_logger
from ai_base import get_provider

logger = get_logger(name="SpeculativeEngine")


class SpeculativeDraftEngine:
    """Engine for speculative drafting and primary verification."""

    def __init__(
        self,
        default_draft_model: Optional[str] = None,
        default_verifier_model: Optional[str] = None,
    ) -> None:
        self.default_draft_model = default_draft_model
        self.default_verifier_model = default_verifier_model

    def draft_and_verify(
        self,
        prompt: str,
        system_prompt: str = "",
        draft_model: Optional[str] = None,
        verifier_model: Optional[str] = None,
        draft_max_tokens: int = 512,
        draft_temperature: float = 0.2,
        verifier_max_tokens: int = 2048,
        verifier_temperature: float = 0.3,
        timeout: int = 60,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> Dict[str, Any]:
        """Execute speculative drafting followed by verifier refinement.

        Args:
            prompt: User task or prompt to generate for.
            system_prompt: Optional governing system instructions.
            draft_model: Model name for fast drafting (defaults to config).
            verifier_model: Model name for primary verification.
            draft_max_tokens: Token budget for the draft stage.
            draft_temperature: Sampling temperature for drafting.
            verifier_max_tokens: Token budget for verification stage.
            verifier_temperature: Sampling temperature for verification.
            timeout: Execution timeout in seconds per phase.
            cancel_event: Optional threading event for mid-flight cancellation.
            progress_callback: Optional callable for phase status updates.

        Returns:
            Dict containing:
                - 'final': str (the verified output or direct output)
                - 'draft': str (the draft text, if drafted)
                - 'verified': bool (True if speculative verification ran)
                - 'draft_model': str
                - 'verifier_model': str
        """
        def _log(msg: str) -> None:
            if progress_callback:
                try:
                    progress_callback(msg)
                except (RuntimeError, TypeError, AttributeError, OSError):
                    pass

        target_verifier = (
            verifier_model
            or self.default_verifier_model
            or CONFIG.get("model_name", "")
        )
        target_draft = (
            draft_model
            or self.default_draft_model
            or CONFIG.get("speculative_draft_model", "")
        )

        provider_name = CONFIG.get("active_provider", "local_llm")

        if cancel_event and cancel_event.is_set():
            return {
                "final": "⏹ Generation cancelled by user.",
                "draft": "",
                "verified": False,
                "draft_model": target_draft,
                "verifier_model": target_verifier,
                "cancelled": True,
            }

        # If speculative drafting is not enabled or draft model matches verifier:
        speculative_enabled = CONFIG.get("speculative_drafting_enabled", False)
        if not speculative_enabled or not target_draft or target_draft == target_verifier:
            _log("⚡ Single-pass inference (speculative drafting bypass)")
            provider = get_provider(name=provider_name)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            res = provider.chat_completion(
                messages=messages,
                model=target_verifier,
                temperature=verifier_temperature,
                max_tokens=verifier_max_tokens,
                timeout=timeout,
            )
            content = res.get("content", "") if isinstance(res, dict) else str(res)
            return {
                "final": content,
                "draft": "",
                "verified": False,
                "draft_model": target_draft,
                "verifier_model": target_verifier,
            }

        # Stage 1: Fast speculative drafting
        _log(f"⚡ [SPECULATIVE DRAFT] Drafting via {target_draft}...")
        draft_content = ""
        try:
            draft_provider = get_provider(name=provider_name)
            draft_messages = []
            if system_prompt:
                draft_messages.append({"role": "system", "content": system_prompt})
            draft_messages.append({
                "role": "user",
                "content": f"{prompt}\n\n[Generate a fast preliminary draft.]",
            })

            draft_res = draft_provider.chat_completion(
                messages=draft_messages,
                model=target_draft,
                temperature=draft_temperature,
                max_tokens=draft_max_tokens,
                timeout=timeout // 2,
            )
            if isinstance(draft_res, dict):
                draft_content = draft_res.get("content", "")
            else:
                draft_content = str(draft_res)
        except (RuntimeError, ValueError, OSError, TypeError, KeyError, TimeoutError, ConnectionError) as exc:
            logger.warning(f"Speculative draft failed ({exc}); falling back to direct verifier")
            _log(f"⚠️ Draft failed ({exc}); falling back to direct generation")
            draft_content = ""

        if cancel_event and cancel_event.is_set():
            return {
                "final": "⏹ Generation cancelled by user.",
                "draft": draft_content,
                "verified": False,
                "draft_model": target_draft,
                "verifier_model": target_verifier,
                "cancelled": True,
            }

        # If draft failed or returned empty, do single-pass on verifier
        if not draft_content.strip():
            provider = get_provider(name=provider_name)
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            res = provider.chat_completion(
                messages=messages,
                model=target_verifier,
                temperature=verifier_temperature,
                max_tokens=verifier_max_tokens,
                timeout=timeout,
            )
            content = res.get("content", "") if isinstance(res, dict) else str(res)
            return {
                "final": content,
                "draft": "",
                "verified": False,
                "draft_model": target_draft,
                "verifier_model": target_verifier,
            }

        # Stage 2: Verifier refinement
        _log(f"🔍 [SPECULATIVE VERIFY] Verifying and refining via {target_verifier}...")
        try:
            verifier_provider = get_provider(name=provider_name)
            verifier_messages = []
            if system_prompt:
                verifier_messages.append({"role": "system", "content": system_prompt})
            verifier_prompt = (
                f"Task:\n{prompt}\n\n"
                f"Preliminary Draft:\n{draft_content}\n\n"
                f"Instructions:\n"
                f"Review and verify the preliminary draft. Correct any inaccuracies, "
                f"fill in gaps, and deliver the final polished response."
            )
            verifier_messages.append({"role": "user", "content": verifier_prompt})

            v_res = verifier_provider.chat_completion(
                messages=verifier_messages,
                model=target_verifier,
                temperature=verifier_temperature,
                max_tokens=verifier_max_tokens,
                timeout=timeout,
            )
            v_content = v_res.get("content", "") if isinstance(v_res, dict) else str(v_res)
            return {
                "final": v_content,
                "draft": draft_content,
                "verified": True,
                "draft_model": target_draft,
                "verifier_model": target_verifier,
            }
        except (RuntimeError, ValueError, OSError, TypeError, KeyError, TimeoutError, ConnectionError) as exc:
            logger.error(f"Speculative verification failed: {exc}")
            # If verifier crashes but draft exists, return the draft as best-effort
            return {
                "final": draft_content,
                "draft": draft_content,
                "verified": False,
                "draft_model": target_draft,
                "verifier_model": target_verifier,
                "error": str(exc),
            }
