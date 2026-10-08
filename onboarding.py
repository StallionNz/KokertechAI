"""
onboarding.py — First-run memory vault initialization script.

Runs at import time to seed core memories, link them, and call
initialize_vault(). The vault is fully patched in tests.
"""

from logging_config import get_logger

logger = get_logger(name="MemoryVault")

try:
    import memory_vault
except ImportError:
    memory_vault = None

if memory_vault is not None:
    memory_vault.initialize_vault()

    # Store four core memories
    id1 = memory_vault.store_memory(
        "KokerPro Executive Core: your unified AI executive dashboard system.",
        "system",
    )
    id2 = memory_vault.store_memory(
        "StallionNZ (User): creator and sole developer of KokertechAI.",
        "tool",
    )
    id3 = memory_vault.store_memory(
        "OmniPro OS: the operating system environment that runs KokertechAI.",
        "os",
    )
    id4 = memory_vault.store_memory(
        "Solar Logic Controller: hardware controller for solar energy management.",
        "hardware",
    )

    # Link memories
    memory_vault.link_memories(id1, id2, "USES_TOOL")
    memory_vault.link_memories(id1, id3, "INTEGRATES_WITH")
    memory_vault.link_memories(id1, id4, "DEPENDS_ON")
