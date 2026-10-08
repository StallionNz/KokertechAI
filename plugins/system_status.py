"""plugins/system_status.py — Reports CPU load, RAM usage, and disk space diagnostics."""

from __future__ import annotations

import time
from typing import Any, Dict

PLUGIN_METADATA = {
    "name": "System Status",
    "description": "Reports CPU load, RAM usage, and disk space diagnostics",
    "version": "2.0.0",
    "tags": ["system", "diagnostics", "hardware", "monitoring"],
    "author": "KokertechAI",
    "requires": ["psutil"],
    "permissions": ["system"],
}

COMMAND_NAME = "SYSTEM_STATUS"
SCHEMA = {
    "action": "SYSTEM_STATUS",
}


def execute(intent_json: Dict[str, Any]) -> str:
    """Execute hardware diagnostics via psutil."""
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("C:\\")

        used_ram = getattr(ram, "used", 0) // (1024 ** 3)
        total_ram = getattr(ram, "total", 0) // (1024 ** 3)
        free_disk = getattr(disk, "free", 0) // (1024 ** 3)

        lines = [
            "🖥️ HARDWARE DIAGNOSTICS:",
            f"- CPU Load: {cpu}%",
            f"- RAM Usage: {ram.percent}% ({used_ram}GB / {total_ram}GB)",
            f"- Disk C:\\ Free: {free_disk}GB",
        ]

        try:
            battery = psutil.sensors_battery() if hasattr(psutil, "sensors_battery") else None
            if battery is not None:
                lines.append(f"- Battery: {battery.percent}%")
            if hasattr(psutil, "boot_time"):
                uptime_h = round((time.time() - psutil.boot_time()) / 3600, 1)
                lines.append(f"- System Uptime: {uptime_h}h")
        except (OSError, RuntimeError, AttributeError):
            pass

        return "\n".join(lines)

    except ImportError:
        return "❌ 'psutil' is not installed. Run: pip install psutil"
    except (OSError, RuntimeError, ValueError) as e:
        return f"❌ Diagnostics failed: {str(e)}"