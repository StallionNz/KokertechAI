# scripts/install_nightly_dr_smoke_task.ps1
#
# Windows Task Scheduler installer/uninstaller for the KokertechAI nightly
# DR smoke cron. Creates (or removes) a daily task that runs at 03:00 local
# time, as SYSTEM, with the highest privileges, invoking the .bat wrapper.
#
# Usage (admin PowerShell required):
#   .\scripts\install_nightly_dr_smoke_task.ps1            # install + show status
#   .\scripts\install_nightly_dr_smoke_task.ps1 -Uninstall # remove task + clear env
#
# Exit codes:
#   0   success (task created/removed, or already in desired state)
#   1   pre-flight error (not admin, schtasks failure, SetX blocked, etc.)

#Requires -Version 5.1
#Requires -RunAsAdministrator

[CmdletBinding()]
param(
  [switch]$Uninstall
)

$ErrorActionPreference = 'Stop'
$TaskName      = 'KokertechAI Nightly DR Smoke'
$ProjectRoot   = 'C:\KokertechAI'
$BatPath       = Join-Path $ProjectRoot 'scripts\cron_nightly_dr_smoke.bat'
$TimeLocal     = '03:00'
$SlackEnvVar   = 'KOKERTECH_ALERT_SLACK_WEBHOOK'

function Write-Status($msg) { Write-Host "[install_nightly_dr_smoke_task] $msg" }
function Fail($msg, $code = 1) { Write-Error $msg; exit $code }

# Reviewer CRITICAL fix: Persist-MachineEnv now FAILS FAST if SetX /M is
# blocked (disk full, group policy lockdown, AV interference, etc.). Previously
# the installer would silently succeed even when Slack alerts were broken.
function Persist-MachineEnv {
  param([string]$Name, [string]$Value)
  $current = [Environment]::GetEnvironmentVariable($Name, 'Machine')
  if ($current -eq $Value) {
    Write-Status "$Name already set at Machine scope; skipping SetX."
    return
  }
  Write-Status "Persisting $Name at Machine scope (SetX /M) so SYSTEM tasks see it."
  # SetX truncates output to 1024 chars; webhook URLs are well under that.
  & setx.exe $Name $Value /M | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Fail ("SetX /M $Name failed (exit $LASTEXITCODE). " +
          "Slack alerts will NOT work. Either run this installer with full " +
          "admin, or pre-set the env var manually. " +
          "Task was created but env var persistence failed.")
  }
  Write-Status "$Name persisted. SYSTEM-scope tasks will now see it."
}

# Reviewer CRITICAL fix: clear the env var on -Uninstall so a stale URL does
# not survive a teardown-and-reinstall cycle.
function Clear-MachineEnv {
  param([string]$Name)
  $current = [Environment]::GetEnvironmentVariable($Name, 'Machine')
  if (-not $current) { return }
  Write-Status "Clearing $Name at Machine scope (was: $current)"
  & setx.exe $Name "" /M | Out-Null
  if ($LASTEXITCODE -ne 0) {
    Fail "SetX /M clear $Name failed (exit $LASTEXITCODE)."
  }
  Write-Status "$Name cleared."
}

# --- Pre-flight ---
if (-not (Test-Path $BatPath)) {
  Fail "Bat wrapper not found: $BatPath. Run from C:\KokertechAI first."
}
if (-not (Get-Command schtasks.exe -ErrorAction SilentlyContinue)) {
  Fail "schtasks.exe not on PATH. This script requires Windows 10+ / Server 2016+."
}

# --- Uninstall path ---
if ($Uninstall) {
  $existing = schtasks /Query /TN $TaskName 2>&1
  if ($LASTEXITCODE -eq 0) {
    schtasks /Delete /TN $TaskName /F
    if ($LASTEXITCODE -ne 0) { Fail "schtasks /Delete failed (exit $LASTEXITCODE)." }
    Write-Status "Task '$TaskName' removed."
  } else {
    Write-Status "Task '$TaskName' not present; nothing to delete."
  }
  Clear-MachineEnv -Name $SlackEnvVar
  exit 0
}

# --- Install path ---
# Build the cmd.exe wrapper that schtasks will execute. cmd.exe /c exits with
# the same code as the .bat so Task Scheduler records the failure.
$cmdAction = "cmd.exe /c `"$BatPath`""

# /RL HIGHEST + /RU SYSTEM so the task runs with full privileges regardless
# of who is logged in. /F forces overwrite (idempotent re-install).
$schtasksArgs = @(
  '/Create', '/TN', $TaskName, '/TR', $cmdAction,
  '/SC', 'DAILY', '/ST', $TimeLocal,
  '/RL', 'HIGHEST', '/RU', 'SYSTEM', '/F'
)

Write-Status "Installing task '$TaskName' (daily at $TimeLocal local, as SYSTEM)..."
Write-Status ("  schtasks " + ($schtasksArgs -join ' '))
& schtasks @schtasksArgs
if ($LASTEXITCODE -ne 0) { Fail "schtasks /Create failed (exit $LASTEXITCODE)." }

# If KOKERTECH_ALERT_SLACK_WEBHOOK is set in the installer's session, persist
# it at Machine scope so the SYSTEM account (which Task Scheduler uses) can
# see it. Persist-MachineEnv fails fast if SetX /M is blocked.
if ($env:KOKERTECH_ALERT_SLACK_WEBHOOK) {
  Persist-MachineEnv -Name $SlackEnvVar -Value $env:KOKERTECH_ALERT_SLACK_WEBHOOK
} else {
  Write-Status "$SlackEnvVar not set in this session; Slack alert will be inactive. To enable:"
  Write-Status "  `$env:$SlackEnvVar = 'https://hooks.slack.com/services/...'; .\install_nightly_dr_smoke_task.ps1"
}

Write-Status "Task installed. Verifying..."
& schtasks /Query /TN $TaskName /V /FO LIST
Write-Status "Done. Next run: 03:00 local time tomorrow. Log: $ProjectRoot\_diag\cron_nightly_dr.log"
exit 0
