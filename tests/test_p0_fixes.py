"""Baseline P0 tests."""
import os, sys, threading, unittest

# Ensure the service registry is loaded with its 4 default services
# before any test creates a KokertechController (which calls get_services().get(...)).
import services.registry  # Triggers module-level registration of history/context/prompt/provider services

from kokertechController import KokertechController
import kokertech_logger

class TestApiKey(unittest.TestCase):
    def test_from_env(self):
        old = os.environ.get("KOKERTECH_API_KEY")
        os.environ["KOKERTECH_API_KEY"] = "t"
        try:
            c = KokertechController()
            self.assertEqual(c.api_key, "t")
        finally:
            if old: os.environ["KOKERTECH_API_KEY"] = old
            else: os.environ.pop("KOKERTECH_API_KEY", None)

    def test_fallback(self):
        old = os.environ.pop("KOKERTECH_API_KEY", None)
        try:
            c = KokertechController()
            self.assertEqual(c.api_key, "")
        finally:
            if old: os.environ["KOKERTECH_API_KEY"] = old

class TestLocks(unittest.TestCase):
    def setUp(self):
        self.c = KokertechController()
    def test_has_lock(self):
        self.assertTrue(hasattr(self.c, "_lock"))
    def test_wipe(self):
        self.c.history.append({})
        self.c.wipe_memory()
        self.assertEqual(len(self.c.history), 0)

class TestErr(unittest.TestCase):
    def setUp(self):
        self.c = KokertechController()
    def test_vram(self):
        self.assertIsInstance(self.c.get_vram_usage(), int)
    def test_cache(self):
        self.c.clear_api_cache()
    def test_summary(self):
        self.c.summary_file = r"C:\nope\x.txt"
        self.assertEqual(self.c._load_summary(), "")

class TestLog(unittest.TestCase):
    def test_has_log(self):
        c = KokertechController()
        self.assertTrue(hasattr(c.logger, 'info'))
        self.assertTrue(hasattr(c.logger, 'ok'))
        self.assertTrue(hasattr(c.logger, 'warning'))
        self.assertTrue(hasattr(c.logger, 'error'))

if __name__ == "__main__":
    unittest.main()
