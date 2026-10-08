"""Alerting wrapper for scripts/ci_tabs_regression.py.

Reads ``_diag/tabs_regression_latest.json`` after the detector runs and
dispatches to all configured channels when ``exit_code != 0``.

Channels (auto-detected via env vars; both run if both are set):

  - Slack incoming webhook when ``KOKERTECH_ALERT_SLACK_WEBHOOK`` is set.
  - SMTP email when ``KOKERTECH_ALERT_SMTP_HOST`` and
    ``KOKERTECH_ALERT_EMAIL_TO`` are set.

SMTP auth & transport (port-driven):

  - Port 25 + no creds       -> plaintext relay.
  - Port 25 + creds          -> STARTTLS.
  - Port 465                 -> implicit TLS (``SMTP_SSL``).
  - Port 587 (any)           -> STARTTLS.

If neither channel is configured and ``exit_code == 1``, the wrapper
enters **log-only mode**: it writes a structured dispatch record to
``_diag/tabs_alerts_history.jsonl`` and the human-readable payload to
stderr (which lands in ``_diag/tabs_cron.log`` after redirection). The
wrapper always exits 0 -- alert-config errors should not be confused
with regression-detection errors by the cron daemon.

Cron invocation pattern (cross-platform): chain with ``;`` (bash),
``&`` (cmd.exe), or systemd ``ExecStartPost`` -- the wrapper runs
unconditionally every time and decides internally whether to dispatch.
The detector's exit code is read from the JSON report, not from process
state, which avoids shell-specific ``errorlevel`` / ``$?`` logic
across ``cron``, ``schtasks``, and ``systemd``.

Usage::

    python scripts/ci_tabs_alert.py
    python scripts/ci_tabs_alert.py --self-test

--self-test bypasses the real report and dispatches a synthetic
``exit_code=1`` to all configured channels with a ``[SELF-TEST]`` prefix
on the body + subject. Useful for validating credential reachability
before first cron invocation, or after a credentials rotation.
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "_diag" / "tabs_regression_latest.json"
ALERT_HISTORY = REPO_ROOT / "_diag" / "tabs_alerts_history.jsonl"


# ---------------------------------------------------------------------------
# Secret-scrubbing
# ---------------------------------------------------------------------------

def _mask_smtp_pass(text: str) -> str:
    pw = os.environ.get("KOKERTECH_ALERT_SMTP_PASS", "")
    return text.replace(pw, "***REDACTED***") if pw else text


def _mask_slack_url(text: str) -> str:
    url = os.environ.get("KOKERTECH_ALERT_SLACK_WEBHOOK", "")
    if not url:
        return text
    parts = url.split("/", 3)
    if len(parts) >= 4:
        head = "/".join(parts[:3]) + "/"
        return text.replace(url, head + "***TOKEN_MASKED***")
    return text


def _scrub(text: str) -> str:
    return _mask_smtp_pass(_mask_slack_url(text))


def _log_err(line: str) -> None:
    print(_scrub(line), file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Message formatting
# ---------------------------------------------------------------------------

def _format_report_message(report: dict, prefix: str = "") -> str:
    flips = report.get("flips", {}) or {}
    regressions = flips.get("REGRESSION", []) or []
    summary = report.get("summary", {}) or {}
    new_files = report.get("new_files", []) or []
    lines = [
        f"{prefix}\U0001F6A8 [Kokertech Tabs Regression] REGRESSION DETECTED",
        f"Exit code:    {report.get('exit_code', '?')}",
        f"Captured at:  {report.get('captured_at', '?')}",
        (f"Summary:      PASS={summary.get('PASS', 0)} "
         f"FAIL={summary.get('FAIL', 0)} "
         f"TIMEOUT={summary.get('TIMEOUT', 0)} "
         f"ERROR={summary.get('ERROR', 0)}"),
    ]
    if regressions:
        lines.append("")
        lines.append(f"REGRESSIONS ({len(regressions)}):")
        for entry in regressions:
            lines.append(
                f"  - {entry.get('file', '?')}: "
                f"{entry.get('from', '?')} -> {entry.get('to', '?')}"
            )
    if new_files:
        lines.append("")
        lines.append(f"NEW FILES (added to baseline): {new_files}")
    lines.append("")
    lines.append(f"Full report: {REPORT_PATH}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

@dataclass
class DispatchResult:
    channel: str
    ok: bool
    detail: str = ""
    error: str = ""


def _send_slack(webhook_url: str, text: str) -> int:
    payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def _send_email(to_addrs, subject, body,
                smtp_host, smtp_port, from_addr, smtp_user, smtp_pass):
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    def _login(s):
        if smtp_user and smtp_pass:
            s.login(smtp_user, smtp_pass)

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=10) as s:
            _login(s)
            s.send_message(msg)
    elif smtp_port == 587 or (smtp_user and smtp_port == 25):
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            s.starttls()
            _login(s)
            s.send_message(msg)
    else:
        # Plaintext (port 25, no creds)
        with smtplib.SMTP(smtp_host, smtp_port, timeout=10) as s:
            _login(s)
            s.send_message(msg)


def _dispatch(text: str, *, self_test: bool) -> list:
    """Dispatch `text` to all configured channels; return results list."""
    results = []
    slack_url = os.environ.get("KOKERTECH_ALERT_SLACK_WEBHOOK")
    smtp_host = os.environ.get("KOKERTECH_ALERT_SMTP_HOST")
    smtp_to = os.environ.get("KOKERTECH_ALERT_EMAIL_TO")

    if slack_url:
        try:
            status = _send_slack(slack_url, text)
            results.append(DispatchResult("slack", ok=True, detail=f"HTTP {status}"))
        except Exception as exc:
            results.append(DispatchResult("slack", ok=False,
                                          error=f"{type(exc).__name__}: {exc}"))

    if smtp_host and smtp_to:
        recipients = [a.strip() for a in smtp_to.split(",") if a.strip()]
        subject_prefix = "[SELF-TEST] " if self_test else ""
        try:
            _send_email(
                recipients,
                f"{subject_prefix}[Kokertech] Tabs Regression Detected",
                text,
                smtp_host,
                int(os.environ.get("KOKERTECH_ALERT_SMTP_PORT", "25")),
                os.environ.get("KOKERTECH_ALERT_FROM",
                               "kokertech-ci@localhost"),
                os.environ.get("KOKERTECH_ALERT_SMTP_USER", ""),
                os.environ.get("KOKERTECH_ALERT_SMTP_PASS", ""),
            )
            results.append(DispatchResult("email", ok=True,
                                          detail=f"{len(recipients)} recipient(s)"))
        except Exception as exc:
            results.append(DispatchResult("email", ok=False,
                                          error=f"{type(exc).__name__}: {exc}"))

    return results


def _record_history(*, alert_triggered, results, exit_code, self_test):
    ALERT_HISTORY.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "alert_triggered": alert_triggered,
        "exit_code": exit_code,
        "self_test": self_test,
        "channels": [
            {"name": r.channel, "ok": r.ok,
             "detail": r.detail, "error": r.error}
            for r in results
        ],
    }
    with open(ALERT_HISTORY, "a", encoding="utf-8") as fp:
        fp.write(_scrub(json.dumps(record, ensure_ascii=False)) + "\n")


def _self_test_report() -> dict:
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "exit_code": 1,
        "summary": {"PASS": 9, "FAIL": 3, "TIMEOUT": 0, "ERROR": 0},
        "flips": {
            "REGRESSION": [
                {"file": "tests/test_settings_tab.py",
                 "from": "PASS", "to": "FAIL"},
                {"file": "tests/test_health_tab.py",
                 "from": "PASS", "to": "FAIL"},
            ],
            "IMPROVEMENT": [],
            "UNCHANGED": [],
        },
        "new_files": [],
        "missing_files": [],
    }


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Alerting wrapper for ci_tabs_regression.py.",
    )
    parser.add_argument(
        "--self-test", action="store_true",
        help="Bypass real report; dispatch synthetic exit=1 to all "
             "configured channels with a [SELF-TEST] prefix.",
    )
    args = parser.parse_args(argv)
    self_test = args.self_test

    if self_test:
        report = _self_test_report()
        _log_err("[self-test] using synthetic exit=1 payload; routing to configured channels")
    else:
        if not REPORT_PATH.exists():
            _log_err(f"[alert] no report at {REPORT_PATH}; nothing to do")
            return 0
        try:
            report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        except Exception as exc:
            _log_err(f"[alert] could not parse report: {type(exc).__name__}: {exc}")
            return 0

    exit_code = int(report.get("exit_code", 0) or 0)
    if exit_code == 0:
        _log_err("[alert] exit_code=0; no alert dispatched")
        _record_history(alert_triggered=False, results=[],
                        exit_code=exit_code, self_test=self_test)
        return 0

    text = _format_report_message(report,
                                  prefix="[SELF-TEST] " if self_test else "")
    results = _dispatch(text, self_test=self_test)

    if not results:
        _log_err(f"[alert] exit_code={exit_code} but no channels configured (log-only mode)")
    else:
        for r in results:
            if r.ok:
                _log_err(f"[alert] {r.channel} OK: {r.detail}")
            else:
                _log_err(f"[alert] {r.channel} FAIL: {r.error}")

    _record_history(alert_triggered=True, results=results,
                    exit_code=exit_code, self_test=self_test)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
