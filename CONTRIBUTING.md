# Contributing to KokertechAI

Thank you for your interest in contributing to KokertechAI! We welcome improvements, bug fixes, and plugins that align with our offline-first, local-only architecture.

---

## Development Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/StallionNz/KokertechAI.git
   cd KokertechAI
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python -m venv .venv
   source .venv/Scripts/activate  # Windows Git Bash
   # or: .venv\Scripts\Activate.ps1
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## Core Engineering Invariants

All contributions must adhere to the project's zero-trust engineering rules:

1. **PyQt6 GUI Thread Affinity**:
   - Background threads, hotkeys, and workers must **never** mutate Qt widgets or UI elements directly.
   - Always marshal UI updates from background threads via `QTimer.singleShot(0, ...)` or Qt signals.

2. **SQLite Concurrency & Lock Defense**:
   - Every SQLite connection must configure `timeout=15.0`.
   - Always close connections in deterministic `try ... finally: conn.close()` blocks.

3. **Typed Exception Handling**:
   - No bare `except:` or blind `except Exception:`. Catch specific exception tuples (e.g., `(OSError, ValueError, RuntimeError)`).

4. **Offline-First & Zero Telemetry**:
   - Do not introduce cloud dependencies, telemetry, or external tracking calls into the core application.

---

## Pre-Flight Verification

Before submitting a Pull Request, verify that all pre-flight quality gates pass locally:

```bash
# Run the fast quality gate suite
python scripts/run_all_gates.py --fast

# Run the full test suite
python -m pytest
```

---

## Submitting Pull Requests

1. Create a feature branch: `git checkout -b feature/my-feature`
2. Ensure all gates pass and code conforms to Ruff formatting (`ruff check .`)
3. Write targeted unit tests in `tests/` covering new logic or bug fixes.
4. Commit with clear, descriptive messages and open a PR against `master`.
