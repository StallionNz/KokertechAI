"""Tests for onboarding.py - run-once memory vault initialization script."""

import sys
import unittest
from unittest.mock import patch, MagicMock


class TestOnboardingScript(unittest.TestCase):
    """Tests that onboarding.py calls vault functions correctly at import time."""

    def setUp(self):
        if "onboarding" in sys.modules:
            del sys.modules["onboarding"]

    def _reload(self):
        """Patch memory_vault, then trigger module-level code via fresh import."""
        self.mock_vault = MagicMock()
        self.mock_vault.store_memory.return_value = 42
        self.patcher = patch.dict("sys.modules", {"memory_vault": self.mock_vault})
        self.patcher.start()
        import onboarding
        # Module is already deleted from sys.modules in setUp,
        # so import triggers fresh execution. DO NOT add reload().

    def tearDown(self):
        if hasattr(self, "patcher"):
            self.patcher.stop()

    def test_initialize_vault_called(self):
        self._reload()
        self.mock_vault.initialize_vault.assert_called_once()

    def test_store_memory_four_times(self):
        self._reload()
        assert self.mock_vault.store_memory.call_count == 4

    def test_core_memory_kokerpro(self):
        self._reload()
        c = self.mock_vault.store_memory.call_args_list[0]
        assert "KokerPro Executive Core" in c[0][0]
        assert c[0][1] == "system"

    def test_core_memory_stallion(self):
        self._reload()
        c = self.mock_vault.store_memory.call_args_list[1]
        assert "StallionNZ" in c[0][0]
        assert c[0][1] == "tool"

    def test_core_memory_omnipro(self):
        self._reload()
        c = self.mock_vault.store_memory.call_args_list[2]
        assert "OmniPro OS" in c[0][0]
        assert c[0][1] == "os"

    def test_core_memory_solar(self):
        self._reload()
        c = self.mock_vault.store_memory.call_args_list[3]
        assert "Solar Logic Controller" in c[0][0]
        assert c[0][1] == "hardware"

    def test_link_memories_three_times(self):
        self._reload()
        assert self.mock_vault.link_memories.call_count == 3

    def test_link_types(self):
        self._reload()
        c = self.mock_vault.link_memories.call_args_list
        assert c[0][0][2] == "USES_TOOL"
        assert c[1][0][2] == "INTEGRATES_WITH"
        assert c[2][0][2] == "DEPENDS_ON"

if __name__ == "__main__":
    unittest.main()
