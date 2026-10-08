@echo off
setlocal
set "KOKERTECH_DIR=%~dp0"
if "%KOKERTECH_DIR:~-1%"=="\" set "KOKERTECH_DIR=%KOKERTECH_DIR:~0,-1%"
cd /d "%KOKERTECH_DIR%"

set "PYTHON_EXE=python"
if exist "%KOKERTECH_DIR%\.venv\Scripts\python.exe" (
    set "PYTHON_EXE=%KOKERTECH_DIR%\.venv\Scripts\python.exe"
)

if "%~1"=="" goto run_cli
if /i "%~1"=="cli" goto run_cli
if /i "%~1"=="terminal" goto run_cli
if /i "%~1"=="gui" goto run_gui
if /i "%~1"=="app" goto run_gui
if /i "%~1"=="--gui" goto run_gui
if /i "%~1"=="web" goto run_web
if /i "%~1"=="model" goto run_model
if /i "%~1"=="download-model" goto run_model
if /i "%~1"=="gates" goto run_gates
if /i "%~1"=="test" goto run_test
if /i "%~1"=="tests" goto run_test
if /i "%~1"=="help" goto show_help
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help

"%PYTHON_EXE%" "%KOKERTECH_DIR%\kokertech_terminal.py" %*
goto end

:run_cli
"%PYTHON_EXE%" "%KOKERTECH_DIR%\kokertech_terminal.py"
goto end

:run_gui
"%PYTHON_EXE%" "%KOKERTECH_DIR%\main.py"
goto end

:run_web
"%PYTHON_EXE%" "%KOKERTECH_DIR%\kokerpro_web.py"
goto end

:run_model
"%PYTHON_EXE%" "%KOKERTECH_DIR%\scripts\download_starter_model.py" %2 %3
goto end

:run_gates
"%PYTHON_EXE%" "%KOKERTECH_DIR%\scripts\run_all_gates.py" --fast
goto end

:run_test
"%PYTHON_EXE%" -m pytest tests\test_cli.py
goto end

:show_help
echo KokertechAI Launcher
echo.
echo Usage:
echo   kokertechai                Launch the interactive CLI terminal (default)
echo   kokertechai gui            Launch the PyQt6 desktop GUI dashboard
echo   kokertechai web            Launch the web interface
echo   kokertechai download-model Download recommended starter GGUF model
echo   kokertechai gates          Run pre-flight quality gates
echo   kokertechai test           Run CLI automated test suite
echo.
goto end

:end
endlocal
