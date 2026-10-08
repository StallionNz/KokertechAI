# KokertechAI Tabs Regression: CI Cron Deployment

## Files

- `scripts/ci_tabs_regression.py` — the detector (~10 min full sweep over the 12-file tabs-touching test set).
- `scripts/ci_tabs_alert.py` — alerting wrapper (this deployment). Reads `_diag/tabs_regression_latest.json` and dispatches to configured channels.
- `_diag/tabs_regression_latest.json` — last run's structured report (one row of summary + flips).
- `_diag/tabs_regression_history.jsonl` — append-only run history (one row per run).
- `_diag/tabs_alerts_history.jsonl` — append-only alert dispatch history (one row per alert attempt).
- `_diag/tabs_cron.log` — unified stdout + stderr trail (recommended log redirect target).

## Run sequence

```
[scheduler] -> python scripts/ci_tabs_regression.py       (exits 0 or 1)
             -> python scripts/ci_tabs_alert.py           (always exits 0; reads exit_code from JSON)
```

The wrapper unconditionally runs after the detector. It reads `exit_code` from `_diag/tabs_regression_latest.json` and decides internally whether to alert. This eliminates shell-level `if errorlevel 1` logic and works identically across `cron`, `schtasks`, and `systemd`.

## Alerting channel env-var matrix

| Env var                                                         | Effect                                                               |
|-----------------------------------------------------------------|----------------------------------------------------------------------|
| `KOKERTECH_ALERT_SLACK_WEBHOOK`                                 | POST `{"text": ...}` to Slack incoming webhook                       |
| `KOKERTECH_ALERT_SMTP_HOST`                                     | SMTP host (other SMTP_* + KOKERTECH_ALERT_EMAIL_TO required)         |
| `KOKERTECH_ALERT_SMTP_PORT`                                     | 25 (plain / STARTTLS) / 465 (SSL) / 587 (STARTTLS)                    |
| `KOKERTECH_ALERT_SMTP_USER` / `KOKERTECH_ALERT_SMTP_PASS`       | SMTP auth                                                            |
| `KOKERTECH_ALERT_FROM`                                          | From address                                                         |
| `KOKERTECH_ALERT_EMAIL_TO`                                      | comma-separated recipients                                           |
| (none)                                                          | log-only mode: writes alert payload to `_diag/tabs_alerts_history.jsonl` and stderr |

If neither channel is configured, the wrapper enters **log-only mode**. The wrapper always exits 0 — alert config errors should not be confused with regression-detection errors by the cron daemon.

## Cross-platform cron entries

### Linux crontab (place in `/etc/cron.d/kokertech-tabs` or `crontab -e`)

```
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
KOKERTECH_ALERT_SLACK_WEBHOOK=<Slack incoming-webhook URL>
KOKERTECH_ALERT_EMAIL_TO=ops@example.com
KOKERTECH_ALERT_SMTP_HOST=smtp.example.com
KOKERTECH_ALERT_SMTP_PORT=587
KOKERTECH_ALERT_SMTP_USER=kokertech-ci@example.com
KOKERTECH_ALERT_SMTP_PASS=__set_in_vault__
KOKERTECH_ALERT_FROM=kokertech-ci@example.com
30 6 * * * kokertech cd /opt/KokertechAI && python scripts/ci_tabs_regression.py >> _diag/tabs_cron.log 2>&1 ; python scripts/ci_tabs_alert.py >> _diag/tabs_cron.log 2>&1
```

> Store email password in your secret manager (`pass`, `vault`, HashiCorp Vault, K8s Secret) — direct cron env-var lines are world-readable via `/etc/cron.d` access.

### Windows Task Scheduler (admin PowerShell)

```powershell
$env:KOKERTECH_ALERT_SLACK_WEBHOOK = 'https://hooks.slack.com/services/...'
schtasks /create /tn "Kokertech Tabs CI" `
  /tr "cmd.exe /c cd /d C:\KokertechAI && python scripts\ci_tabs_regression.py > _diag\tabs_cron.log 2>&1 & python scripts\ci_tabs_alert.py >> _diag\tabs_cron.log 2>&1" `
  /sc daily /st 06:30 /ru SYSTEM
```

For log-only debugging on Windows you can run both manually first:

```powershell
cd C:\KokertechAI
python scripts\ci_tabs_regression.py
python scripts\ci_tabs_alert.py         # reads exit code from the JSON
```

### systemd timer + service (place in `/etc/systemd/system/kokertech-tabs-ci.{service,timer}`)

```ini
# kokertech-tabs-ci.service
[Unit]
Description=KokertechAI tabs regression detector + alerter
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
WorkingDirectory=/opt/KokertechAI
Environment=KOKERTECH_ALERT_SLACK_WEBHOOK=https://hooks.slack.com/services/...
Environment=KOKERTECH_ALERT_EMAIL_TO=ops@example.com
Environment=KOKERTECH_ALERT_SMTP_HOST=smtp.example.com
Environment=KOKERTECH_ALERT_SMTP_PORT=587
EnvironmentFile=/etc/kokertech/secrets.env   # KOKE


## Nightly DR Smoke (Sprint 14 expansion)

Companion to the tabs-regression cron above. Runs the full sha256-roundtrip
chain end-to-end (verify_snapshot_roundtrip --in-place + restore_session_chats_backup
--apply --yes into a fresh scratch dir) so nightly snapshot drift is detected
before it accumulates. Distinct from the tabs cron: this one targets
Backups/session_chats_*/manifest.txt, not the tab UI sweep.

### Schedule

| Field | Value |
|-------|-------|
| Task name | `KokertechAI Nightly DR Smoke` |
| Frequency | Daily, 03:00 local time |
| Account | `SYSTEM` (privilege HIGHEST) |
| Wrapper | `C:\KokertechAI\scripts\cron_nightly_dr_smoke.bat` |
| Log | `C:\KokertechAI\_diag\cron_nightly_dr.log` |
| Gate line | `NIGHTLY DR SMOKE: PASS|FAIL` (grepable for downstream CI) |

### Files

| Path | Purpose |
|------|---------|
| `scripts/cron_nightly_dr_smoke.py` | The actual chain (already exists; see the docstring for CLI). |
| `scripts/cron_nightly_dr_smoke.bat` | Windows wrapper. CRLF line endings, forces UTF-8, redirects output to `_diag\cron_nightly_dr.log`, propagates exit code, optionally POSTs a Slack alert on failure. |
| `scripts/install_nightly_dr_smoke_task.ps1` | Admin PowerShell installer. `#Requires -RunAsAdministrator`. Accepts `-Uninstall` to remove the task. |
| `_diag/cron_nightly_dr.log` | Append-only log; rotated manually (or via the same `session_log_max_files` config key as `execution_log.txt`). |

### Install (admin PowerShell)

```powershell
# One-time install:
powershell -ExecutionPolicy Bypass -File C:\KokertechAI\scripts\install_nightly_dr_smoke_task.ps1

# Verify:
schtasks /Query /TN "KokertechAI Nightly DR Smoke" /V /FO LIST

# Uninstall:
powershell -ExecutionPolicy Bypass -File C:\KokertechAI\scripts\install_nightly_dr_smoke_task.ps1 -Uninstall
```

The installer is idempotent (`/F` forces overwrite). Re-running it after
editing the .bat is safe.

### Exit codes (mirrored from `cron_nightly_dr_smoke.py`)

| Exit | Meaning | Cron action |
|------|---------|-------------|
| 0 | All stages clean (`NIGHTLY DR SMOKE: PASS`) | Continue |
| 1 | Any stage detected drift (`NIGHTLY DR SMOKE: FAIL`) | Task Scheduler records failure; Slack alert fires if env var set |
| 2 | Pre-flight error (manifest missing, scratch-dir conflict, etc.) | Same as 1 |
| 3 | .bat could not cd to `PROJECT_ROOT` (operator error) | Same as 1; rare |

### Slack alert wiring

`schtasks /Create` does **not** bake environment variables into the SYSTEM
account. To enable Slack alerts, ops must either:

1. **System Properties GUI**: `Win+R sysdm.cpl` -> Advanced -> Environment
   Variables -> System variables -> New -> `KOKERTECH_ALERT_SLACK_WEBHOOK`
   = `<Slack incoming-webhook URL>`
2. **PowerShell** (admin):
   ```powershell
   [Environment]::SetEnvironmentVariable(
     'KOKERTECH_ALERT_SLACK_WEBHOOK',
     '<Slack incoming-webhook URL>',
     'Machine')
   ```
3. **wrap the .bat call in `runas /user:SYSTEM`** (not used here; the wrapper
   already calls `python` directly and depends on the env var being on the
   machine, not the user).

The .bat reads the env var at runtime. If unset, the alert step is silently
skipped (cron still records the exit code). This matches the
`ci_tabs_alert.py` pattern used by the tabs cron above.
