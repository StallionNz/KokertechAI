# KokertechAI

An **offline-first, local-only AI platform** for Windows. KokertechAI runs a local
LLM in-process, keeps all state on your own disk, and drives it through a PyQt6
desktop dashboard, a Flask web UI, and a headless terminal CLI.

No cloud API keys. No data leaving the machine.

Until recently this repository was a private working repo; the documentation that
lived under `docs/` (internal architecture notes, session logs, and planning
records) has been removed and is not part of this tree.

---

## What it is

KokertechAI is not a chat wrapper. It is a coordinated stack of components that
share one in-process AI backend and one local memory store:

- **Local LLM** — `llama-cpp-python` running GGUF models fully in-process. No
  separate inference server and no external API. A mock mode is available for
  offline testing and CI.
- **Desktop dashboard** — PyQt6 application (`main.py` → `app_core.py`) with 15+
  tab modules covering chat, memory browsing, plugins, health, neural graph,
  cognitive agency, computer use, and more.
- **Web UI** — Flask app on port `:5050` (`kokerpro_web.py`) for remote access,
  plus a FastAPI variant (`kokerpro_fastapi.py`).
- **Terminal CLI** — `kokertech_terminal.py` plus the `cli/` package: Rich
  output, slash commands, and persisted sessions.
- **Memory system** — SQLite vault with embeddings and tiered
  (semantic / episodic / long-term) memory.
- **Plugin registry** — 28 native plugins with metadata and enable/disable UI.
- **Workflow engine** — YAML-based pipeline orchestration with step-type routing.
- **Sub-agent system** — specialised personas (researcher, coder, auditor,
  planner) coordinated through `sub_agents.py` and `services/`.
- **Docker sandbox** — executes generated Python in an isolated container, with
  automatic fallback to a local subprocess when Docker is unavailable.

## Requirements

- Windows (the desktop layer assumes it; the CLI and services are portable)
- Python 3.10 (see `tox.ini`)
- Optional: Docker, for the code sandbox
- Optional: a local GGUF model file

Dependencies are pinned in `requirements.txt`.

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate      # bash on Windows (Git Bash): source .venv/Scripts/activate
pip install -r requirements.txt
```

Then launch whichever surface you need:

```bash
python main.py                # PyQt6 desktop dashboard
python app_core.py            # same dashboard, direct entry point
python kokerpro_web.py        # Flask web UI on :5050
python kokertech_terminal.py  # headless terminal CLI
```

## Testing

The suite is ~164 test files and is designed for parallel execution:

```bash
python scripts/run_parallel_chunked.py   # full suite, chunked (recommended)
python -m pytest tests/test_ai_base.py   # a single file
```

A monolithic `pytest` run over the whole suite is not supported — the chunked
runner exists because the full suite crashes xdist workers in one process.

## Pre-flight gates

`scripts/run_all_gates.py` is the repo's quality gate runner. It enforces doc
hygiene, anchor integrity, lint baselines, silent-catch and singleton-reset
patterns, corruption recurrence, config-mode pins, and more.

```bash
python scripts/run_all_gates.py           # all gates
python scripts/run_all_gates.py --fast    # fast subset only
python scripts/run_all_gates.py --json    # machine-readable output
python scripts/run_all_gates.py --verbose # full output per gate
```

## Repository layout

| Path | Contents |
| --- | --- |
| `*.py` (root) | Core modules — AI backend, app core, vault, controllers, services |
| `app_core.py`, `main.py` | PyQt6 dashboard entry points |
| `tabs/` | Dashboard tab modules |
| `plugins/` | Native plugin implementations |
| `services/` | Backend service layer (code intelligence, tool use, prompts, …) |
| `cli/` | Terminal CLI package |
| `agent/` | Agent behaviour contracts |
| `.agents/` | Bundled skills |
| `scripts/` | Pre-flight gates, test runners, maintenance tooling |
| `tests/` | Test suite |
| `vscode-freebuff-agents/` | VS Code agent extension sources |
| `tools/` | Standalone utilities |

## Status

Actively developed. Interfaces and module layout may change without notice
between commits.

## License

KokertechAI is licensed under the **GNU Affero General Public License v3.0**
(AGPL-3.0-or-later). The full text is in [`LICENSE`](LICENSE).

Copyright (C) 2026 Kokertech Pty Ltd.

Under the AGPL, if you run a modified version of this software as a network
service, you must make the corresponding source available to that service's
users.
