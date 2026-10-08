"""
test_discovery.py - Unit tests for Discovery.py.

Defensive contract:
  * ``import Discovery`` never prints and never crashes
  * ``Discovery.probe()`` is invoked explicitly by callers / tests
  * success path: ``requests.get(url, timeout=N)`` then ``print(json)``
  * timeout resolves through three-tier precedence:
    CONFIG ``discovery_timeout_seconds`` -> env var
    ``KOKERTECH_DISCOVERY_TIMEOUT`` (legacy) -> default 5s
  * any failure mode (missing requests, network error, bad env var,
    non-JSON body) degrades gracefully with a stderr diagnostic
"""
import os
import unittest
from unittest.mock import MagicMock, patch

import Discovery
import config



class TestImportIsLibrary(unittest.TestCase):
    """``import Discovery`` MUST NOT print or call any network code."""

    def test_module_level_code_never_calls_requests(self):
        """Importing the module must not invoke ``requests.get``.  Any
        earlier import (e.g., the test framework discovering this file)
        has already happened; we patch ``requests.get`` and assert it
        is untouched through another ``import`` round-trip.
        """
        with patch("requests.get") as mock_get:
            # Force a re-import using importlib so we observe the exact
            # module-level statements.
            import importlib
            importlib.reload(Discovery)
            mock_get.assert_not_called()

    def test_module_level_code_never_prints(self):
        """``import Discovery`` must not produce stdout/stderr output."""
        with patch("builtins.print") as mock_print:
            import importlib
            importlib.reload(Discovery)
            mock_print.assert_not_called()


class TestProbeInvocation(unittest.TestCase):
    """When called as a script (``__name__ == '__main__'``), probe()
    runs.  We test the contract by calling ``probe()`` directly, which
    is the equivalent of the script entrypoint."""

    @patch("builtins.print")
    @patch("requests.get")
    def test_default_5s_when_env_var_unset_or_empty(
        self, mock_get, mock_print
    ):
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        # Explicit empty string -> _timeout_from_env treats falsy -> default.
        with patch.dict(os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": ""}):
            Discovery.probe()
        mock_get.assert_called_once_with(
            "http://127.0.0.1:1337/v1/models", timeout=5
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_env_var_overrides_default_timeout(
        self, mock_get, mock_print
    ):
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        # Pop CONFIG's discovery_timeout_seconds so the env var layer
        # is reachable (since CONFIG wins when both are set).
        with patch.dict(os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "12"}), \
             patch.dict(config.CONFIG, {}, clear=False):
            config.CONFIG.pop("discovery_timeout_seconds", None)
            Discovery.probe()
        mock_get.assert_called_once_with(
            "http://127.0.0.1:1337/v1/models", timeout=12
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_invalid_env_var_falls_back_to_default(
        self, mock_get, mock_print
    ):
        """Non-integer / zero / negative / whitespace-only values at the
        env-var layer fall through to the next tier.  With
        CONFIG[``discovery_timeout_seconds``] absent AND env var
        invalid, the default 5s wins (test invalidates the env-var
        layer; the next layer reachable is the default).
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        # Pop CONFIG's discovery_timeout_seconds so the env-var layer
        # is the one being tested and falls through to the default.
        with patch.dict(config.CONFIG, {}, clear=False):
            config.CONFIG.pop("discovery_timeout_seconds", None)
            for bad in ("abc", "0", "-5", "  "):
                with patch.dict(os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": bad}):
                    Discovery.probe()
        # Each invocation should call requests.get with default 5s.
        self.assertGreaterEqual(mock_get.call_count, 4)
        for call in mock_get.call_args_list:
            args, kwargs = call
            self.assertEqual(kwargs.get("timeout"), 5)

    @patch("builtins.print")
    @patch("requests.get")
    def test_whitespace_padded_env_var_is_stripped(
        self, mock_get, mock_print
    ):
        """``'  12  '`` should be parsed as 12, not raise on int()."""
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        # Pop CONFIG's discovery_timeout_seconds so the env-var layer
        # is reachable.
        with patch.dict(os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "  12  "}), \
             patch.dict(config.CONFIG, {}, clear=False):
            config.CONFIG.pop("discovery_timeout_seconds", None)
            Discovery.probe()
        mock_get.assert_called_once_with(
            "http://127.0.0.1:1337/v1/models", timeout=12
        )


class TestDefensiveContract(unittest.TestCase):
    """``probe()`` MUST NEVER raise -- any failure mode degrades
    gracefully with a stderr diagnostic."""

    @patch("builtins.print")
    @patch("requests.get")
    def test_requests_connection_error_is_caught_not_raised(
        self, mock_get, mock_print
    ):
        # IMPORTANT: the requests package raises its OWN ConnectionError
        # (requests.exceptions.ConnectionError), NOT the stdlib built-in.
        # Both inherit from ``OSError`` but stdlib ConnectionError does NOT
        # inherit from ``requests.exceptions.RequestException``.
        from requests.exceptions import ConnectionError as ReqConnErr
        mock_get.side_effect = ReqConnErr("Connection refused")
        with patch.dict(os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": ""}):
            Discovery.probe()
        # probe() must NOT raise -- that is the entire contract.
        mock_print.assert_called_once()
        diagnostic = mock_print.call_args[0][0]
        self.assertIn("unreachable", diagnostic)
        self.assertIn("Connection refused", diagnostic)

    @patch("builtins.print")
    @patch("requests.get")
    def test_request_timeout_is_caught_not_raised(
        self, mock_get, mock_print
    ):
        from requests.exceptions import Timeout
        mock_get.side_effect = Timeout("timed out")
        with patch.dict(os.environ, {}):
            Discovery.probe()
        mock_print.assert_called_once()
        self.assertIn("timed out", mock_print.call_args[0][0])

    @patch("builtins.print")
    @patch("requests.get")
    def test_non_json_response_is_printed_verbatim(
        self, mock_get, mock_print
    ):
        """A non-JSON body (HTML error page, plain text) is printed as-is
        instead of raising ValueError out of probe()."""
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "<html>Not a real server</html>"
        mock_get.return_value = mock_response
        with patch.dict(os.environ, {}):
            Discovery.probe()
        mock_print.assert_called_once_with("<html>Not a real server</html>")


class TestMissingRequests(unittest.TestCase):
    """If the ``requests`` package is missing, the probe degrades to a
    no-op rather than crashing module import."""

    def test_probe_returns_gracefully_when_requests_unavailable(self):
        """``probe()`` prints a stderr diagnostic and returns without
        crashing when ``Discovery.requests`` is None.

        We call ``probe()`` directly without ``importlib.reload()``
        because reload would re-run the module body, where the
        ``try: import requests`` succeeds (requests IS installed in
        the test env) and overwrites our ``None`` patch.
        """
        with patch.object(Discovery, "requests", None), \
             patch("builtins.print") as mock_print:
            Discovery.probe()
        mock_print.assert_called_once()
        diagnostic = mock_print.call_args[0][0]
        self.assertIn("not installed", diagnostic)


class TestConfigDrivenUrl(unittest.TestCase):
    """``probe()`` accepts an explicit ``url=`` parameter so the
    application can wire it to ``CONFIG["KOKERTECH_DISCOVERY_URL"]``
    (llama.cpp / OpenAI-compatible endpoints, HTTPS
    endpoints, custom ports).

    Backward compat: ``probe()`` with no args still hits the module
    default ``DISCOVERY_URL`` so the standalone-script entrypoint and
    any caller still using the bare form keep working.
    """

    def test_config_entry_present_with_default(self):
        """CONFIG has the new key with the default URL."""
        self.assertIn("KOKERTECH_DISCOVERY_URL", config.CONFIG)
        self.assertEqual(
            config.CONFIG["KOKERTECH_DISCOVERY_URL"],
            "http://127.0.0.1:1337/v1/models",
        )
        # Module default must agree with the config default so the
        # backward-compat path returns the same URL either way.
        self.assertEqual(
            config.CONFIG["KOKERTECH_DISCOVERY_URL"],
            Discovery.DISCOVERY_URL,
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_https_endpoint_override(self, mock_get, mock_print):
        """``probe(url='https://...')`` honors the override (HTTPS).

        The assertion deliberately checks only the URL passed to
        ``requests.get`` (not the timeout) so this test stays robust
        against a globally-set ``KOKERTECH_DISCOVERY_TIMEOUT`` env var.
        The env-var timeout contract is covered separately by
        ``test_default_5s_when_env_var_unset_or_empty``.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        Discovery.probe(url="https://api.example.com/v1/models")
        self.assertEqual(
            mock_get.call_args[0][0],
            "https://api.example.com/v1/models",
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_custom_url_with_non_default_port(self, mock_get, mock_print):
        """Custom host:port URL is honored end-to-end.

        Decoupled from ``timeout`` for env-var robustness.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        Discovery.probe(url="http://192.168.1.50:8080/v1/models")
        self.assertEqual(
            mock_get.call_args[0][0],
            "http://192.168.1.50:8080/v1/models",
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_no_url_arg_falls_back_to_module_default(
        self, mock_get, mock_print
    ):
        """``probe()`` with no args preserves backward compat
        (post-refactor: the module-level ``DISCOVERY_URL`` is the
        fallback for callers that don't pass ``url=``)."""
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": ""}):
            Discovery.probe()
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, Discovery.DISCOVERY_URL)

    @patch("builtins.print")
    @patch("requests.get")
    def test_url_none_explicitly_falls_back_to_module_default(
        self, mock_get, mock_print
    ):
        """``probe(url=None)`` is identical to ``probe()`` no-arg."""
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        Discovery.probe(url=None)
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, Discovery.DISCOVERY_URL)

    @patch("builtins.print")
    @patch("requests.get")
    def test_url_kwarg_honored_alongside_env_timeout(
        self, mock_get, mock_print
    ):
        """``url=`` and the env-var timeout are orthogonal: passing
        ``url=`` does NOT block the env-var timeout.  (Pop CONFIG so
        the env-var layer is reachable; CONFIG would otherwise win.)"""
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "12"}), \
             patch.dict(config.CONFIG, {}, clear=False):
            config.CONFIG.pop("discovery_timeout_seconds", None)
            Discovery.probe(url="https://api.openrouter.ai/api/v1/models")
        mock_get.assert_called_once_with(
            "https://api.openrouter.ai/api/v1/models", timeout=12
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_url_with_trailing_slash_passed_verbatim(self, mock_get, mock_print):
        """Trailing-slash URLs are passed verbatim to ``requests.get``
        — the probe does NOT strip or normalize the path segment.
        OpenAI-compatible endpoints are commonly configured with or
        without a trailing ``/``; locking this contract down means a
        future refactor that adds URL normalization will be forced to
        update this test rather than silently change behavior.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        Discovery.probe(url="https://api.example.com/v1/models/")
        self.assertEqual(
            mock_get.call_args[0][0],
            "https://api.example.com/v1/models/",
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_url_with_query_string_passed_verbatim(
        self, mock_get, mock_print
    ):
        """URLs with query strings (e.g. ``?api-version=2024-01``) are
        passed verbatim to ``requests.get`` so OpenAI-style versioned
        endpoints work without extra normalization."""
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        Discovery.probe(url="https://api.openai.com/v1/models?api-version=2024-01")
        self.assertEqual(
            mock_get.call_args[0][0],
            "https://api.openai.com/v1/models?api-version=2024-01",
        )


class TestDiscoveryWiring(unittest.TestCase):
    """Wiring-level integration tests.

    Locks in that ``app_lifecycle._run_discovery_probe`` (the daemon-thread
    probe body invoked by ``setup_discovery_probe``) ACTUALLY reads
    ``CONFIG["KOKERTECH_DISCOVERY_URL"]`` and surfaces the resolved URL in
    the session-journal payload — proving the end-to-end wiring, not just
    the library contract.

    Patches ``auto_logger.journal_event`` (the side-effect we want to
    observe), ``requests.get`` (so no real HTTP call fires), and
    ``Discovery.probe`` indirectly via the requests.get mock.  Each test
    constructs a minimal stub ``owner`` object with only the attributes
    the probe body reads/writes; ``_refresh_available_models=None``
    short-circuits the QTimer.singleShot branch so tests don't need a
    real Qt event loop.
    """

    def _make_owner_stub(self):
        """Stub owner: the few attributes ``_run_discovery_probe``
        reads/writes. ``_refresh_available_models=None`` skips the
        QTimer branch entirely so the test doesn't need a Qt app.

        ``active_threads`` is a real list so ``setup_discovery_probe``
        can append to it and the test can ``.join()`` the daemon to
        observe its journal side-effect synchronously.
        """
        import threading
        owner = MagicMock()
        owner._lock = threading.Lock()
        owner.file_logger = MagicMock()
        owner._refresh_available_models = None
        owner.available_models = []
        owner._discovery_status = "pending"
        owner._discovery_diagnostic = ""
        owner.active_threads = []
        return owner

    @patch("auto_logger.journal_event")
    @patch("requests.get")
    def test_journal_payload_uses_config_override_url(
        self, mock_get, mock_journal
    ):
        """CONFIG override flows into the journal payload.

        Sets ``CONFIG["KOKERTECH_DISCOVERY_URL"] = "https://custom/"``,
        invokes ``setup_discovery_probe`` directly (the production entry
        point), joins its daemon thread, and asserts that the journal
        event includes ``provider_url`` equal to the CONFIG override —
        proving the wiring reads CONFIG rather than just the Discovery
        module default.
        """
        from app_lifecycle import AppLifecycleMixin

        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        owner = self._make_owner_stub()
        override_url = "https://custom.example.com/v1/models"

        with patch.dict(
            config.CONFIG,
            {"KOKERTECH_DISCOVERY_URL": override_url, "discovery_probe_on_startup": True},
        ):
            AppLifecycleMixin.setup_discovery_probe(owner)
            self.assertEqual(len(owner.active_threads), 1)
            owner.active_threads[0].join(timeout=5)

        provider_calls = [
            c for c in mock_journal.call_args_list
            if c.args and c.args[0] == "provider"
        ]
        self.assertGreater(
            len(provider_calls), 0,
            "no PROVIDER journal event was emitted",
        )
        payload = provider_calls[0].args[2]
        self.assertEqual(
            payload["provider_url"], override_url,
            f"journal payload did not contain the override URL: {payload}",
        )

    @patch("auto_logger.journal_event")
    @patch("requests.get")
    def test_journal_payload_reflects_connection_refused(
        self, mock_get, mock_journal
    ):
        """Connection-refused still records the override URL.

        When ``requests.get`` raises ``ConnectionError``, the probe
        resolves the URL from CONFIG BEFORE the network attempt and
        records it in the journal payload with status="error" — proving
        URL resolution is decoupled from probe success.
        """
        from app_lifecycle import AppLifecycleMixin
        from requests.exceptions import ConnectionError as ReqConnErr

        mock_get.side_effect = ReqConnErr("Connection refused")
        owner = self._make_owner_stub()
        override_url = "http://offline.example.com/v1/models"

        with patch.dict(
            config.CONFIG,
            {"KOKERTECH_DISCOVERY_URL": override_url, "discovery_probe_on_startup": True},
        ):
            AppLifecycleMixin.setup_discovery_probe(owner)
            self.assertEqual(len(owner.active_threads), 1)
            owner.active_threads[0].join(timeout=5)

        provider_calls = [
            c for c in mock_journal.call_args_list
            if c.args and c.args[0] == "provider"
        ]
        self.assertGreater(len(provider_calls), 0)
        payload = provider_calls[0].args[2]
        self.assertEqual(
            payload["provider_url"], override_url,
            "URL resolution must precede the network attempt",
        )
        self.assertEqual(payload["status"], "error")
        self.assertIn("diagnostic", payload)

    @patch("auto_logger.journal_event")
    @patch("requests.get")
    def test_journal_payload_falls_back_to_module_default(
        self, mock_get, mock_journal
    ):
        """With no CONFIG override, payload uses Discovery.DISCOVERY_URL."""
        import Discovery
        from app_lifecycle import AppLifecycleMixin

        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        owner = self._make_owner_stub()

        # Snapshot + clear any existing CONFIG override for this test.
        with patch.dict(
            config.CONFIG,
            {"discovery_probe_on_startup": True},
            clear=False,
        ):
            config.CONFIG.pop("KOKERTECH_DISCOVERY_URL", None)
            AppLifecycleMixin.setup_discovery_probe(owner)
            self.assertEqual(len(owner.active_threads), 1)
            owner.active_threads[0].join(timeout=5)

        provider_calls = [
            c for c in mock_journal.call_args_list
            if c.args and c.args[0] == "provider"
        ]
        self.assertGreater(len(provider_calls), 0)
        payload = provider_calls[0].args[2]
        self.assertEqual(
            payload["provider_url"], Discovery.DISCOVERY_URL,
            f"payload should fall back to module default, got: {payload}",
        )


class TestConfigDrivenTimeout(unittest.TestCase):
    """``probe()`` timeout resolves through three-tier precedence:

    1. ``CONFIG["discovery_timeout_seconds"]`` (in-process, configurable
       via Settings tab / ``app_settings.json``).
    2. ``KOKERTECH_DISCOVERY_TIMEOUT`` env var (legacy shell-script /
       .env override).
    3. ``Discovery.DEFAULT_DISCOVERY_TIMEOUT`` (= 5 seconds).

    Backward compat: when ``CONFIG["discovery_timeout_seconds"]`` is
    unset / invalid, the env var still works as a fallback -- so legacy
    shell scripts / .env files keep functioning after the migration.
    Invalid (non-int, zero, negative, whitespace-only, missing) values
    fall through to the next tier so a typo at any level never breaks
    the probe.
    """

    def test_config_entry_present_with_default_5(self):
        """CONFIG has the new key with the canonical 5-second default,
        aligned with ``Discovery.DEFAULT_DISCOVERY_TIMEOUT``.
        """
        self.assertIn("discovery_timeout_seconds", config.CONFIG)
        self.assertEqual(config.CONFIG["discovery_timeout_seconds"], 5)
        self.assertEqual(
            config.CONFIG["discovery_timeout_seconds"],
            Discovery.DEFAULT_DISCOVERY_TIMEOUT,
        )

    @patch("builtins.print")
    @patch("requests.get")
    def test_config_timeout_overrides_env_var(
        self, mock_get, mock_print
    ):
        """CONFIG wins over a contradicting env var.

        With both ``KOKERTECH_DISCOVERY_TIMEOUT=99`` and
        ``CONFIG["discovery_timeout_seconds"]=12`` set, the probe uses
        12 (CONFIG prevails).  This locks in the migration contract:
        CONFIG is the canonical runtime override, env var is the
        legacy fallback only.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(
            os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "99"}
        ), patch.dict(
            config.CONFIG, {"discovery_timeout_seconds": 12}
        ):
            Discovery.probe()
        self.assertEqual(mock_get.call_args.kwargs.get("timeout"), 12)

    @patch("builtins.print")
    @patch("requests.get")
    def test_env_var_fallback_when_config_unset(
        self, mock_get, mock_print
    ):
        """Env var works as fallback when CONFIG key is missing.

        Removes ``discovery_timeout_seconds`` from CONFIG and sets
        ``KOKERTECH_DISCOVERY_TIMEOUT=7``; probe uses 7.  This locks
        in backward compat for legacy shell scripts / .env files that
        pre-date the CONFIG migration.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(
            os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "7"}
        ), patch.dict(config.CONFIG, {}, clear=False):
            config.CONFIG.pop("discovery_timeout_seconds", None)
            Discovery.probe()
        self.assertEqual(mock_get.call_args.kwargs.get("timeout"), 7)

    @patch("builtins.print")
    @patch("requests.get")
    def test_default_fallback_when_both_unset(
        self, mock_get, mock_print
    ):
        """With both CONFIG key absent AND env var unset, falls back
        to ``Discovery.DEFAULT_DISCOVERY_TIMEOUT`` (= 5).
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(os.environ, {}), \
             patch.dict(config.CONFIG, {}, clear=False):
            config.CONFIG.pop("discovery_timeout_seconds", None)
            Discovery.probe()
        self.assertEqual(mock_get.call_args.kwargs.get("timeout"), 5)

    @patch("builtins.print")
    @patch("requests.get")
    def test_invalid_config_falls_through_to_env_var(
        self, mock_get, mock_print
    ):
        """CONFIG invalid (non-int, zero, negative) -> env var layer
        is consulted next; if env var is valid the env-var value wins.
        """
        for bad in ("abc", 0, -5, "  "):
            mock_get.reset_mock()
            mock_get.return_value = MagicMock(json=lambda: {"data": []})
            with patch.dict(
                os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "88"}
            ), patch.dict(
                config.CONFIG, {"discovery_timeout_seconds": bad}
            ):
                Discovery.probe()
            self.assertEqual(
                mock_get.call_args.kwargs.get("timeout"), 88,
                f"CONFIG={bad!r} should fall through to env var (88)",
            )

    @patch("builtins.print")
    @patch("requests.get")
    def test_invalid_env_var_falls_through_to_valid_config(
        self, mock_get, mock_print
    ):
        """Bad env var doesn't mask a valid CONFIG value.

        CONFIG says 12, env var says ``"abc"`` (unparseable).  Since
        the env-var layer fails to parse, the env-var layer falls
        through, but the CONFIG layer above it has already returned
        12 -- so the probe uses 12.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(
            os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "abc"}
        ), patch.dict(
            config.CONFIG, {"discovery_timeout_seconds": 12}
        ):
            Discovery.probe()
        self.assertEqual(mock_get.call_args.kwargs.get("timeout"), 12)

    @patch("builtins.print")
    @patch("requests.get")
    def test_both_invalid_falls_back_to_default(
        self, mock_get, mock_print
    ):
        """If both CONFIG-layer AND env-var-layer values are invalid,
        ``Discovery.DEFAULT_DISCOVERY_TIMEOUT`` (5) wins.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(
            os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "abc"}
        ), patch.dict(
            config.CONFIG, {"discovery_timeout_seconds": -1}
        ):
            Discovery.probe()
        self.assertEqual(mock_get.call_args.kwargs.get("timeout"), 5)

    @patch("builtins.print")
    @patch("requests.get")
    def test_config_rejects_bool_coercion(
        self, mock_get, mock_print
    ):
        """CONFIG value ``True`` is *not* coerced to ``1``.

        ``isinstance(True, int)`` is True in Python (booleans subclass
        int), but the probe must treat booleans as invalid -> next
        layer.  This prevents a future config-UI checkbox from
        silently changing the timeout semantics.
        """
        mock_get.return_value = MagicMock(json=lambda: {"data": []})
        with patch.dict(
            os.environ, {"KOKERTECH_DISCOVERY_TIMEOUT": "44"}
        ), patch.dict(
            config.CONFIG, {"discovery_timeout_seconds": True}
        ):
            Discovery.probe()
        self.assertEqual(mock_get.call_args.kwargs.get("timeout"), 44)


if __name__ == "__main__":
    unittest.main()
