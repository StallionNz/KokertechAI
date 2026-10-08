"""
Discovery.py - System discovery and capability detection.

Probes an OpenAI-compatible ``/v1/models`` endpoint (llama.cpp server
default; any custom URL via the ``KOKERTECH_DISCOVERY_URL`` CONFIG key).
**Defensive at every layer** so module import never crashes:

* Missing ``requests`` package -> empty probe, diagnostic on stderr.
* Connection refused / DNS error / timeout -> empty probe, diagnostic
  on stderr; never raises.
* Non-JSON response body -> printed verbatim to stdout.
* Timeout resolves through three layers (CONFIG -> env var -> default):
  - ``CONFIG["discovery_timeout_seconds"]`` (in-process, configurable
    via Settings tab / app_settings.json).
  - ``KOKERTECH_DISCOVERY_TIMEOUT`` env var (legacy override, e.g. for
    shell scripts or .env files).
  - ``DEFAULT_DISCOVERY_TIMEOUT`` (5 seconds).
  Invalid / non-positive values fall through to the next layer so a
  typo never breaks the probe.

Library convention:
* ``import Discovery`` only defines symbols -- nothing prints, no
  network calls are made.
* The probe runs only when the module is executed as a script
  (``python Discovery.py``) or when callers invoke ``Discovery.probe()``
  directly.
* The probe target URL comes from the ``url=`` parameter when
  supplied (caller usually passes ``CONFIG["KOKERTECH_DISCOVERY_URL"]``
  from app_lifecycle.setup_discovery_probe) and falls back to the
  module-level ``DISCOVERY_URL`` constant for the standalone entrypoint
  so ``python Discovery.py`` keeps working out of the box.

"""

import json
import os
import sys

from logging_config import get_logger

# Module-level default URL for the discovery probe.
# Used as fallback when CONFIG["KOKERTECH_DISCOVERY_URL"] is absent.
DISCOVERY_URL = "http://127.0.0.1:1337/v1/models"
DEFAULT_DISCOVERY_TIMEOUT = 5

# Graceful requests import so the module never crashes at import time
# when requests is not installed.  probe() checks this at call time.
try:
    import requests as _requests
    requests = _requests
except ImportError:
    requests = None

logger = get_logger(name="MemoryVault")


def _is_valid_positive_int(value) -> bool:
    """Return True if *value* is a positive int and NOT a bool.

    In Python ``bool`` is a subclass of ``int`` (``isinstance(True, int)``
    is True), so a naive ``isinstance(value, int) and value > 0`` check
    would silently coerce ``True`` to ``1``.  This guard prevents a
    future Settings-UI checkbox from accidentally translating into
    "1 second timeout".
    """
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _resolve_timeout_from_config() -> int | None:
    """Read ``CONFIG["discovery_timeout_seconds"]`` and return a valid
    positive int, or ``None`` if the key is absent / invalid / a bool.
    """
    try:
        from config import CONFIG
        val = CONFIG.get("discovery_timeout_seconds")
        if val is not None and _is_valid_positive_int(val):
            return int(val)
    except (ImportError, AttributeError, KeyError):
        pass
    return None


def _resolve_timeout_from_env() -> int | None:
    """Read ``KOKERTECH_DISCOVERY_TIMEOUT`` env var and return a valid
    positive int, or ``None`` if the value is absent / invalid / zero.

    Whitespace is stripped so ``"  12  "`` is parsed as 12.
    """
    try:
        raw = os.environ.get("KOKERTECH_DISCOVERY_TIMEOUT", "").strip()
        if raw:
            val = int(raw)
            if val > 0:
                return val
    except (ValueError, TypeError):
        pass
    return None


def _timeout_from_env():
    """Return the probe timeout in seconds using three-tier precedence:

        1. ``CONFIG["discovery_timeout_seconds"]`` (in-process, configurable
           via Settings tab / ``app_settings.json``).
        2. ``KOKERTECH_DISCOVERY_TIMEOUT`` env var (legacy shell-script /
           .env override).
        3. ``Discovery.DEFAULT_DISCOVERY_TIMEOUT`` (= 5 seconds).

    Invalid (non-int, zero, negative, whitespace-only, missing) values
    fall through to the next tier so a typo at any level never breaks
    the probe.
    """
    # Tier 1: CONFIG
    cfg = _resolve_timeout_from_config()
    if cfg is not None:
        return cfg

    # Tier 2: env var
    env = _resolve_timeout_from_env()
    if env is not None:
        return env

    # Tier 3: default
    return DEFAULT_DISCOVERY_TIMEOUT


def probe(url=None):
    """Probe the configured model endpoint.

    Args:
        url: The OpenAI-compatible ``/v1/models`` endpoint to probe.
             When ``None`` (the default), uses the module-level
             ``DISCOVERY_URL`` constant (``http://127.0.0.1:1337/v1/models``)
             so the standalone-script entrypoint keeps working without
             configuration.  Production callers (e.g. app_lifecycle
             .setup_discovery_probe) typically pass
             ``CONFIG["KOKERTECH_DISCOVERY_URL"]`` so HTTPS endpoints
             and custom-model-service URLs are honoured via the
             existing CONFIG-driven pattern.

    Prints the JSON list to stdout on success, a diagnostic to stderr
    on any failure.  Never raises.

    Returns:
        A dict with:
          - ``"ok"`` (bool): True on success, False on failure.
          - ``"error"`` (str, optional): Error message on failure.
          - ``"models"`` (list, optional): Parsed model list on success.
    """
    if requests is None:
        print("requests library not installed; cannot probe", file=sys.stderr)
        return {"ok": False, "error": "requests not installed"}

    target = url if url is not None else DISCOVERY_URL
    timeout = _timeout_from_env()

    try:
        response = requests.get(target, timeout=timeout)
        try:
            data = response.json()
            try:
                print(json.dumps(data, indent=2))
            except ValueError:
                pass  # stdout closed (e.g. pytest capture teardown)
            models = data.get("data", []) if isinstance(data, dict) else data
            return {"ok": True, "models": models}
        except ValueError:
            # Non-JSON response (HTML error page, plain text, etc.)
            try:
                print(response.text)
            except ValueError:
                pass  # stdout closed
            return {"ok": False, "error": "non-json response"}
    except requests.exceptions.ConnectionError as e:
        print(f"Discovery endpoint unreachable: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}
    except requests.exceptions.Timeout as e:
        print(f"Discovery endpoint timed out: {e}", file=sys.stderr)
        return {"ok": False, "error": str(e)}
