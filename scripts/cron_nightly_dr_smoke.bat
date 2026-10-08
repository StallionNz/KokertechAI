@echo off
REM scripts/cron_nightly_dr_smoke.bat
REM
REM Windows wrapper for the nightly DR smoke cron. Invoked by Task Scheduler
REM ("KokertechAI Nightly DR Smoke", 03:00 daily, see install_nightly_dr_smoke_task.ps1).
REM
REM Behavior:
REM   1. Detect missing PROJECT_ROOT. `cd /d` alone returns 0 on missing dir;
REM      the trailing-backslash `if not exist "%PROJECT_ROOT%\"` test distinguishes
REM      a missing DIR from a file with that name. (Reviewer-caught MEDIUM fix.)
REM   2. Locate python.exe via `where python` with `delims=` (so paths-with-spaces
REM      like "C:\Program Files\Python314\python.exe" survive intact). Fall back
REM      to C:\Python314\python.exe. (Reviewer-caught HIGH #1 fix.)
REM   3. cd to project root, force UTF-8, run the Python script --apply --yes.
REM   4. Capture stdout+stderr to _diag\cron_nightly_dr.log.
REM   5. On non-zero exit, POST a Slack alert (if KOKERTECH_ALERT_SLACK_WEBHOOK
REM      is set in the SYSTEM account env - installer persists it via SetX /M).
REM      Use `Write-Output` (NOT Write-Host) so the success/fail line is captured
REM      by the outer `>>` redirect reliably across PowerShell host configurations.
REM      (Reviewer-caught HIGH #2 fix.)
REM   6. Exit with the same code as the Python script so Task Scheduler records
REM      the failure (0/1/2/3/4).
REM
REM Exit codes:
REM   0   all stages clean (NIGHTLY DR SMOKE: PASS)
REM   1   any stage detected drift (NIGHTLY DR SMOKE: FAIL) -- includes vault
REM       census FAIL (test pollution, active bleed, corrupt embeddings)
REM   2   pre-flight error (manifest missing, scratch-dir conflict, etc.)
REM   3   cannot cd to PROJECT_ROOT (operator error / missing install)
REM   4   python.exe not found on PATH or in fallback location
REM
REM Sprint 19.8.2 (2026-09-22): added STAGE 3 -- vault hygiene census
REM (scripts/vault_maintenance.py --census). Read-only (mode=ro connection).
REM Exit 1 FAILs the whole cron so Slack alerts fire on vault drift too.

setlocal
set "PROJECT_ROOT=C:\KokertechAI"
set "PYTHONIOENCODING=utf-8"
set "LOG_DIR=%PROJECT_ROOT%\_diag"
set "LOG_FILE=%LOG_DIR%\cron_nightly_dr.log"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
echo === KokertechAI Nightly DR Smoke === >> "%LOG_FILE%"
echo === %DATE% %TIME% ===                       >> "%LOG_FILE%"

REM Reviewer MEDIUM fix: trailing backslash distinguishes a missing DIR from a
REM file with the same name; `cd /d` returns 0 on missing dir.
if not exist "%PROJECT_ROOT%\" (
  echo CRITICAL: PROJECT_ROOT not found: %PROJECT_ROOT% >> "%LOG_FILE%"
  endlocal & exit /b 3
)

cd /d "%PROJECT_ROOT%" || (
  echo CRITICAL: cd to %PROJECT_ROOT% failed >> "%LOG_FILE%"
  endlocal & exit /b 3
)

REM Reviewer HIGH #1 fix: `delims=` token keeps paths-with-spaces intact.
set "PY_EXE="
for /f "delims=" %%P in ('where python 2^>nul') do (
  if not defined PY_EXE set "PY_EXE=%%P"
)
if not defined PY_EXE (
  if exist "C:\Python314\python.exe" set "PY_EXE=C:\Python314\python.exe"
)
if not defined PY_EXE (
  if exist "C:\Python\Python314\python.exe" set "PY_EXE=C:\Python\Python314\python.exe"
)
if not defined PY_EXE (
  echo CRITICAL: python.exe not found on PATH or in C:\Python314 >> "%LOG_FILE%"
  echo   Set the PYTHON_EXE env var or install Python. >> "%LOG_FILE%"
  endlocal & exit /b 4
)
echo Using Python: %PY_EXE% >> "%LOG_FILE%"

"%PY_EXE%" scripts\cron_nightly_dr_smoke.py --apply --yes >> "%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"
echo === DR smoke exit=%RC% (%DATE% %TIME%) === >> "%LOG_FILE%"

REM ----------------------------------------------------------------
REM STAGE 3: vault hygiene census (Sprint 19.8.2). Read-only; exit 1
REM means pollution / active bleed / corrupt embeddings -- the same
REM alerting path as DR drift so nightly failures are never silent.
REM The census result does not mask the DR smoke exit code: the cron
REM exits non-zero if EITHER stage fails.
REM ----------------------------------------------------------------
set "CENSUS_RC=0"
"%PY_EXE%" scripts\vault_maintenance.py --census >> "%LOG_FILE%" 2>&1
set "CENSUS_RC=%ERRORLEVEL%"
echo === census exit=%CENSUS_RC% (%DATE% %TIME%) === >> "%LOG_FILE%"

REM Propagate the worse of the two stage results.
if %CENSUS_RC% GTR %RC% set "RC=%CENSUS_RC%"

set "STAGE_RC=%RC%"

REM Reviewer HIGH #2 fix: `Write-Output` (success stream) is captured by the
REM outer `>> "%LOG_FILE%" 2>&1`; `Write-Host` (host stream) is NOT. The
REM Invoke-RestMethod | Out-Null suppresses the Slack response body so the log
REM only shows the clean status line.
if not "%STAGE_RC%"=="0" if not "%KOKERTECH_ALERT_SLACK_WEBHOOK%"=="" (
  echo Posting alert to Slack webhook... >> "%LOG_FILE%"
  powershell -NoProfile -Command ^
    "$body = @{ text = '[KokertechAI Nightly DR Smoke] FAIL (exit %STAGE_RC%). See %LOG_FILE%' } | ConvertTo-Json -Compress;" ^
    "try { Invoke-RestMethod -Uri '%KOKERTECH_ALERT_SLACK_WEBHOOK%' -Method Post -ContentType 'application/json' -Body $body -TimeoutSec 10 ^| Out-Null;" ^
    "  Write-Output '  Slack alert posted (HTTP 200)'" ^
    "} catch {" ^
    "  Write-Output ('  Slack alert FAILED: ' + $_.Exception.Message)" ^
    "}" >> "%LOG_FILE%" 2>&1
)

endlocal & exit /b %STAGE_RC%
