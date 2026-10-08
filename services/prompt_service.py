"""PromptService — Sprint 3 (L99).

Owns prompt construction + parsing surface that previously lived on
:class:`kokertechController.KokertechController`:

- ``_pb``                        — :class:`prompt_builder.PromptBuilder` instance
- ``_pending_tool_loads``        — current-turn progressive-disclosure request set
- ``_loaded_tool_categories``    — TTL-keyed persistence counter dict
- ``_tool_persistence_ttl``      — turns a loaded schema survives (default 3)

Methods:
- ``parse_ai(content, freeform=False)`` — structured/freeform tag extractor
- ``build_context_text(**kwargs)``     — 8-section delegated build
- ``build_system_prompt(mode, custom_protocol=None)``
- ``build_summarization_prompt()``
- ``build_messages(system_prompt, context_text, history, user_input, ...)``
- ``decay_pending_tool_loads()`` — decrement TTL, evict expired
- ``inject_progressive_disclosure(system_prompt, log)`` — callback hook
  for the AI's ``<<LOAD_TOOLS:...>>`` requests
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Optional

from logging_config import get_logger

_STRUCTURED_OUTPUT_LOGGER = get_logger(name="StructuredOutput")


class PromptService:
    """Owns prompt construction + parsing + progressive-disclosure state."""

    def __init__(self) -> None:
        # Imported lazily so tests can patch prompt_builder.* before first
        # ``PromptBuilder()`` instantiation — without breaking import chain.
        from prompt_builder import PromptBuilder

        self._pb = PromptBuilder()
        self._pending_tool_loads: set = set()
        self._loaded_tool_categories: dict = {}
        self._tool_persistence_ttl: int = 3

    # -- Lazy-facing delegations to PromptBuilder --------------------------

    def build_system_prompt(self, *, mode: str = "structured", custom_protocol: Optional[str] = None) -> str:
        return self._pb.build_system_prompt(mode=mode, custom_protocol=custom_protocol)

    def build_summarization_prompt(self) -> str:
        return self._pb.build_summarization_prompt()

    def build_context_text(self, **kwargs: str) -> str:
        return self._pb.build_context_text(**kwargs)

    def build_messages(
        self,
        system_prompt: str,
        context_text: str,
        history: list,
        user_input: str,
        truncate_fn=None,
        msg_cap: int = 1500,
    ) -> list:
        return self._pb.build_messages(
            system_prompt=system_prompt,
            context_text=context_text,
            history=history,
            user_input=user_input,
            truncate_fn=truncate_fn,
            msg_cap=msg_cap,
        )

    # -- Progressive disclosure --------------------------------------------

    def decay_pending_tool_loads(self, log=None) -> list:
        """Decrement TTL counters, evict expired.

        Returns the list of category names that were evicted (UI log aid).
        """
        expired = [cat for cat, ttl in self._loaded_tool_categories.items() if ttl <= 0]
        for cat in expired:
            del self._loaded_tool_categories[cat]
            if log:
                log(f"Progressive disclosure: evicted '{cat}' schemas (TTL expired)")
        for cat in self._loaded_tool_categories:
            self._loaded_tool_categories[cat] -= 1
        return expired

    def register_pending_load(self, ai_text: str, log=None) -> None:
        """Scan ``ai_text`` for ``<<LOAD_TOOLS:cat1,cat2>>`` and queue them."""
        load_match = re.findall(r"<<LOAD_TOOLS:\s*([^>]+)>>", ai_text, re.IGNORECASE)
        if not load_match:
            return
        for group in load_match:
            categories = [
                cat.strip().lower() for cat in group.split(",") if cat.strip()
            ]
            self._pending_tool_loads.update(categories)
        if log:
            log(f"AI requested tool categories: {self._pending_tool_loads}")

    def finalize_loaded_categories(self) -> None:
        """Extend TTL for newly requested categories; clear the pending set."""
        for cat in self._pending_tool_loads:
            self._loaded_tool_categories[cat] = self._tool_persistence_ttl
        self._pending_tool_loads.clear()

    def categories_to_load(self) -> set:
        """Combine pending + still-persisting categories into a load set."""
        cats = set()
        if self._pending_tool_loads:
            cats.update(self._pending_tool_loads)
        persisting = [
            cat for cat, ttl in self._loaded_tool_categories.items() if ttl > 0
        ]
        cats.update(persisting)
        return cats

    def inject_progressive_disclosure(
        self, system_prompt: str, plugin_registry, log=None
    ) -> str:
        """Append category summary + loaded schemas to system_prompt.

        ``plugin_registry`` provides:
            - ``progressive_disclosure_system_prompt()``
            - ``get_tools_for_llm(categories)``
        """
        try:
            prompt_addon = plugin_registry.progressive_disclosure_system_prompt()
        except (AttributeError, TypeError):
            prompt_addon = ""

        self.decay_pending_tool_loads(log=log)
        categories_to_load = self.categories_to_load()

        if categories_to_load:
            try:
                tools = plugin_registry.get_tools_for_llm(list(categories_to_load))
            except (AttributeError, TypeError):
                tools = []
            if tools:
                prompt_addon += (
                    f"\n[LOADED TOOL SCHEMAS "
                    f"(will persist for {self._tool_persistence_ttl} turns)]:\n"
                    f"{json.dumps(tools, indent=2)}\n"
                )
                if log:
                    log(
                        f"Progressive disclosure: injected {len(tools)} tool "
                        f"schemas for categories: {sorted(categories_to_load)}"
                    )
                self.finalize_loaded_categories()

        return system_prompt + prompt_addon

    # -- parse_ai (the tag extractor) --------------------------------------

    @staticmethod
    def parse_ai(content: str, freeform: bool = False) -> dict:
        """Structured/freeform parser for AI responses.

        Uses xml.etree.ElementTree for structured mode (grammar-constrained output
        should produce valid XML), falling back to regex for partial/legacy responses.
        Validates the result through Pydantic's AIResponse model (fail-soft with logging).

        Freeform mode: ``content`` is the response verbatim, with optional
        ``{...}`` JSON action detection.

        Structured mode: extracts ``<thinking>...</thinking>`` and
        ``<final_output>...</final_output>`` tags via XML parser first,
        regex fallback second.

        Returns a dict matching the ``AIResponse`` Pydantic schema.
        """
        if freeform:
            command = None
            json_match = re.search(r"(\{.*\})", content, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(1))
                    if parsed.get("action"):
                        command = parsed
                except json.JSONDecodeError:
                    # Deliberate typed fallback (NOT a silent broad catch):
                    # malformed <command> JSON keeps the previous command value;
                    # content is still returned unchanged.
                    pass
            result = {
                "thinking": "",
                "final": content.strip(),
                "command": command,
                "ts": datetime.now().strftime("%H:%M:%S"),
            }
        else:
            # Structured mode: try xml.etree first (grammar guarantees well-formed XML),
            # fall back to regex for partial/legacy responses.
            thinking = None
            final_output = None
            parse_method = "none"

            # Wrap in a root element for XML parsing (XML requires single root)
            xml_content = f"<root>{content}</root>"
            try:
                root = ET.fromstring(xml_content)
                thinking_el = root.find("thinking")
                final_el = root.find("final_output")
                if thinking_el is not None and thinking_el.text:
                    thinking = thinking_el.text.strip()
                if final_el is not None and final_el.text:
                    final_output = final_el.text.strip()
                parse_method = "xml"
            except ET.ParseError:
                _STRUCTURED_OUTPUT_LOGGER.warning(
                    f"Structured output XML parse failed (len={len(content)}). "
                    f"Falling back to regex. First 200 chars: {content[:200]!r}"
                )

            # Regex fallback for partial/legacy content
            if final_output is None:
                final_match = re.search(
                    r"<final_output>(.*?)</final_output>", content, re.DOTALL
                )
                if final_match:
                    final_output = final_match.group(1).strip()
                    parse_method = "regex_final"
                else:
                    final_output = content
                    parse_method = "raw_content"
                    _STRUCTURED_OUTPUT_LOGGER.warning(
                        f"No <final_output> tag found (len={len(content)}). "
                        f"Using raw content. First 200 chars: {content[:200]!r}"
                    )

            if thinking is None:
                think_match = re.search(
                    r"<thinking>(.*?)</thinking>", content, re.DOTALL
                )
                thinking = think_match.group(1).strip() if think_match else "Synthesizing..."
                if parse_method == "none" and think_match:
                    parse_method = "regex"

            command = None
            json_match = re.search(r"(\{.*\})", final_output, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(1))
                    if parsed.get("action"):
                        command = parsed
                except json.JSONDecodeError:
                    # Deliberate typed fallback (NOT a silent broad catch):
                    # malformed <command> JSON keeps the previous command value;
                    # content is still returned unchanged.
                    pass

            result = {
                "thinking": thinking,
                "final": final_output,
                "command": command,
                "ts": datetime.now().strftime("%H:%M:%S"),
                "_parse_method": parse_method,
            }

        # Validate through Pydantic AIResponse model (fail-soft with logging)
        try:
            from models import AIResponse
            from pydantic import ValidationError
            clean_result = {k: v for k, v in result.items() if not k.startswith("_")}
            validated = AIResponse(**clean_result)
            return validated.model_dump()
        except (ValidationError, ValueError, TypeError) as e:
            _STRUCTURED_OUTPUT_LOGGER.warning(
                f"AIResponse validation failed: {e}. "
                f"Falling back to unvalidated dict. Keys: {list(result.keys())}"
            )
            # Strip internal keys before returning (they leak when validation fails)
            return {k: v for k, v in result.items() if not k.startswith("_")}

    @staticmethod
    def parse_tool_calls(content: str) -> list:
        """Extract tool calls from LLM output that contains <<TOOL_CALL:{...}>> directives.

        Returns a list of parsed tool call dicts, each with at minimum an 'action' key.
        Logs warnings for malformed tool call directives for observability.
        """
        tool_calls = []
        for match in re.finditer(r'<<TOOL_CALL:\s*(.*?)\s*>>', content, re.DOTALL):
            try:
                intent = json.loads(match.group(1))
                if intent.get("action"):
                    tool_calls.append(intent)
                else:
                    _STRUCTURED_OUTPUT_LOGGER.warning(
                        f"Tool call directive missing 'action' key: {match.group(1)[:200]!r}"
                    )
            except json.JSONDecodeError as e:
                _STRUCTURED_OUTPUT_LOGGER.warning(
                    f"Malformed TOOL_CALL JSON: {e}. Content: {match.group(1)[:200]!r}"
                )
        return tool_calls
