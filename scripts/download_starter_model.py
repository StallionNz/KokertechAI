#!/usr/bin/env python3
"""
download_starter_model.py — Starter Model Downloader for KokertechAI.

Downloads tested, public GGUF models directly into models/ with no
Hugging Face token required.

Usage:
    python scripts/download_starter_model.py
    python scripts/download_starter_model.py --model compact
    python scripts/download_starter_model.py --model balanced
    python scripts/download_starter_model.py --model standard
"""

import os
import sys
import time
import argparse
import urllib.request
import urllib.error

# Resolve models directory relative to repository root
WORKSPACE_DIR = os.environ.get(
    "KOKERTECH_WORKSPACE_DIR",
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
MODIFIED_MODELS_DIR = os.path.join(WORKSPACE_DIR, "models")

MODELS = {
    "compact": {
        "name": "qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "label": "Qwen 2.5 0.5B Instruct (Compact - 468 MB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/main/qwen2.5-0.5b-instruct-q4_k_m.gguf",
        "size_mb": 468,
        "desc": "Ultra-fast, runs on any CPU, ideal for quick testing & background workers."
    },
    "balanced": {
        "name": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "label": "Qwen 2.5 1.5B Instruct (Balanced - 1.0 GB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/main/qwen2.5-1.5b-instruct-q4_k_m.gguf",
        "size_mb": 1065,
        "desc": "Great balance of reasoning speed and accuracy."
    },
    "standard": {
        "name": "qwen2.5-3b-instruct-q4_k_m.gguf",
        "label": "Qwen 2.5 3B Instruct (Recommended - 2.0 GB)",
        "url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct-GGUF/resolve/main/qwen2.5-3b-instruct-q4_k_m.gguf",
        "size_mb": 2007,
        "desc": "High intelligence, recommended for general chat and tool-calling."
    }
}


def format_bytes(num_bytes: int) -> str:
    """Format bytes into human-readable string."""
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:3.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} TB"


def download_with_progress(url: str, dest_path: str) -> bool:
    """Download file from url to dest_path with progress display."""
    tmp_path = dest_path + ".downloading"
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    if not url.startswith(("http://", "https://")):
        print(f"\nERROR: Unsupported URL scheme: {url}")
        return False

    headers = {"User-Agent": "KokertechAI/1.0"}
    req = urllib.request.Request(url, headers=headers)  # noqa: S310

    print(f"\nConnecting to: {url}")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
            total_size = int(response.headers.get("content-length", 0))
            downloaded = 0
            start_time = time.time()
            chunk_size = 1024 * 1024  # 1MB chunks

            print(f"File size: {format_bytes(total_size)}")
            print(f"Target:    {dest_path}")
            print("\nDownloading... (press Ctrl+C to cancel)\n")

            with open(tmp_path, "wb") as out_file:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out_file.write(chunk)
                    downloaded += len(chunk)

                    elapsed = time.time() - start_time
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    percent = (downloaded / total_size * 100) if total_size > 0 else 0
                    eta_sec = (total_size - downloaded) / speed if speed > 0 else 0

                    bar_len = 30
                    filled = int(bar_len * downloaded / total_size) if total_size > 0 else 0
                    bar = "█" * filled + "░" * (bar_len - filled)

                    status = (
                        f"\r[{bar}] {percent:5.1f}% | "
                        f"{format_bytes(downloaded)}/{format_bytes(total_size)} | "
                        f"{format_bytes(speed)}/s | ETA: {int(eta_sec)}s"
                    )
                    sys.stdout.write(status)
                    sys.stdout.flush()

        print("\n\nFinalizing...")
        if os.path.exists(dest_path):
            os.remove(dest_path)
        os.replace(tmp_path, dest_path)
        print(f"SUCCESS: Model saved to {dest_path}")
        return True

    except (urllib.error.URLError, TimeoutError, OSError) as e:
        print(f"\nERROR: Download failed: {e}")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False
    except KeyboardInterrupt:
        print("\n\nDownload aborted by user.")
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        return False


def main():
    parser = argparse.ArgumentParser(description="Download starter GGUF models for KokertechAI")
    parser.add_argument(
        "--model",
        choices=["compact", "balanced", "standard"],
        help="Model tier to download (compact, balanced, or standard)"
    )
    parser.add_argument(
        "--dest",
        default=MODIFIED_MODELS_DIR,
        help="Destination directory for models (default: models/)"
    )
    args = parser.parse_args()

    selected_key = args.model

    if not selected_key:
        print("=" * 60)
        print("  KokertechAI — Starter Model Downloader")
        print("=" * 60)
        print("Select a starter model to download into models/:\n")
        print("  [1] Compact  : Qwen 2.5 0.5B (~468 MB)  - Instant setup, all CPUs")
        print("  [2] Balanced : Qwen 2.5 1.5B (~1.0 GB)  - Great speed & quality")
        print("  [3] Standard : Qwen 2.5 3B   (~2.0 GB)  - Recommended default")
        print("  [q] Quit")
        print("-" * 60)

        choice = input("Enter choice [1/2/3, default=1]: ").strip().lower()
        if choice in ["q", "quit", "exit"]:
            print("Cancelled.")
            return 0
        if choice == "2":
            selected_key = "balanced"
        elif choice == "3":
            selected_key = "standard"
        else:
            selected_key = "compact"

    model_info = MODELS[selected_key]
    target_file = os.path.join(args.dest, model_info["name"])

    if os.path.exists(target_file):
        print(f"\nModel already exists at: {target_file}")
        overwrite = input("Re-download and overwrite? [y/N]: ").strip().lower()
        if overwrite not in ["y", "yes"]:
            print("Skipped download.")
            return 0

    success = download_with_progress(model_info["url"], target_file)
    if success:
        print("\nNext step:")
        print("  Launch KokertechAI (kokertechai gui) and select this model in Settings -> Apply!")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
