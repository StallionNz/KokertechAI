#!/usr/bin/env python3
"""
build_exe.py — PyInstaller packaging helper for KokertechAI.

Builds a standalone Windows executable for KokertechAI into dist/KokertechAI.

Usage:
    python scripts/build_exe.py
    python scripts/build_exe.py --onefile
"""

import os
import sys
import subprocess
import argparse

WORKSPACE_DIR = os.environ.get(
    "KOKERTECH_WORKSPACE_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def main():
    parser = argparse.ArgumentParser(description="Package KokertechAI into a Windows executable")
    parser.add_argument("--onefile", action="store_true", help="Package into a single standalone .exe")
    args = parser.parse_args()

    # Verify PyInstaller is installed
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[..] PyInstaller not found. Installing via pip...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])  # noqa: S603

    icon_path = os.path.join(WORKSPACE_DIR, "kokertech.ico")
    main_script = os.path.join(WORKSPACE_DIR, "main.py")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--name=KokertechAI",
        "--windowed",
        "--clean",
        "--noconfirm",
        f"--icon={icon_path}",
        f"--add-data={os.path.join(WORKSPACE_DIR, 'assets')};assets",
        f"--add-data={icon_path};.",
        f"--add-data={os.path.join(WORKSPACE_DIR, 'kokertech_icon.png')};.",
        f"--add-data={os.path.join(WORKSPACE_DIR, 'plugins')};plugins",
        "--hidden-import=PyQt6",
        "--hidden-import=PyQt6.QtCore",
        "--hidden-import=PyQt6.QtGui",
        "--hidden-import=PyQt6.QtWidgets",
        "--hidden-import=logging_config",
        "--hidden-import=config",
        "--hidden-import=ai_base",
        "--hidden-import=memory_vault",
        "--hidden-import=kokertechController",
    ]

    if args.__dict__.get("onefile"):
        cmd.append("--onefile")
    else:
        cmd.append("--onedir")

    cmd.append(main_script)

    print("=" * 60)
    print("  KokertechAI — PyInstaller Executable Builder")
    print("=" * 60)
    print(f"Target: {main_script}")
    print(f"Mode:   {'One-File' if args.onefile else 'One-Directory'}")
    print("-" * 60)

    ret = subprocess.call(cmd, cwd=WORKSPACE_DIR)  # noqa: S603
    if ret == 0:
        dist_dir = os.path.join(WORKSPACE_DIR, "dist", "KokertechAI")
        print("\n" + "=" * 60)
        print("BUILD SUCCESSFUL!")
        print(f"Output available in: {dist_dir}")
        print("=" * 60)
    else:
        print(f"\nBUILD FAILED with exit code {ret}")
    return ret


if __name__ == "__main__":
    sys.exit(main())
