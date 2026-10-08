@echo off
setlocal enabledelayedexpansion

title KokertechAI - Setup & Installer
color 0A

echo ====================================================================
echo                   KOKERTECH AI - WINDOWS INSTALLER
echo ====================================================================
echo.

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

:: 1. Verify Python availability
python --version >nul 2>&1
if %ERRORLEVEL% neq 0 (
    color 0C
    echo [ERROR] Python is not installed or not in your system PATH.
    echo.
    echo Please install Python 3.10+ (recommended: Python 3.11 or 3.12):
    echo   winget install Python.Python.3.11
    echo   or download from: https://www.python.org/downloads/
    echo.
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%i in ('python --version 2^>^&1') do set "PY_VER=%%i"
echo [OK] Detected Python %PY_VER%

:: 2. Create Virtual Environment
if not exist "%SCRIPT_DIR%\.venv\Scripts\python.exe" (
    echo [..] Creating virtual environment in .venv ...
    python -m venv .venv
    if %ERRORLEVEL% neq 0 (
        color 0C
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Existing virtual environment found in .venv.
)

:: 3. Activate and Install Dependencies
echo [..] Installing dependencies from requirements.txt ...
call "%SCRIPT_DIR%\.venv\Scripts\activate.bat"
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
if %ERRORLEVEL% neq 0 (
    color 0E
    echo [WARNING] Some dependencies had warnings during install.
) else (
    echo [OK] Dependencies installed successfully.
)

:: 4. Create Desktop Shortcut
echo.
set /p CREATE_SHORTCUT="Create Desktop Shortcut for KokertechAI? [Y/n]: "
if /i "%CREATE_SHORTCUT%"=="" set "CREATE_SHORTCUT=Y"
if /i "%CREATE_SHORTCUT%"=="Y" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = [System.Environment]::GetFolderPath('Desktop'); $sc = $ws.CreateShortcut((Join-Path $desktop 'KokertechAI.lnk')); $sc.TargetPath = (Join-Path '%SCRIPT_DIR%' 'kokertechai.cmd'); $sc.Arguments = 'gui'; $sc.WorkingDirectory = '%SCRIPT_DIR%'; $sc.IconLocation = (Join-Path '%SCRIPT_DIR%' 'kokertech.ico'); $sc.Description = 'KokertechAI Desktop Dashboard'; $sc.Save()" >nul 2>&1
    echo [OK] Desktop shortcut created.
)

:: 5. Offer Starter Model Download
echo.
set /p DOWNLOAD_MODEL="Download starter AI model into models/ now? [Y/n]: "
if /i "%DOWNLOAD_MODEL%"=="" set "DOWNLOAD_MODEL=Y"
if /i "%DOWNLOAD_MODEL%"=="Y" (
    python "%SCRIPT_DIR%\scripts\download_starter_model.py"
)

:: 6. Setup Complete
color 0A
echo.
echo ====================================================================
echo                   INSTALLATION COMPLETE!
echo ====================================================================
echo.
echo To launch KokertechAI anytime:
echo   - Double-click the Desktop Shortcut, OR
echo   - Run: kokertechai gui
echo.
echo Press any key to exit setup...
pause >nul
endlocal
