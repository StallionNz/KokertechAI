"""
resource_history.py - Persistent ring buffer for resource usage history.
"""

import json
import os
import time
import threading
from datetime import datetime, timedelta



from logging_config import get_logger

logger = get_logger(name="MemoryVault")

class ResourceHistoryRingBuffer:
    """Persistent ring buffer for resource usage history (VRAM, RAM, disk)."""

    def __init__(self, max_entries: int, save_path: str):
        self.max_entries = max_entries
        self.save_path = save_path
        self._lock = threading.Lock()
        self._data: list = []

    @classmethod
    def load(cls, save_path: str, max_entries: int = 300) -> "ResourceHistoryRingBuffer":
        """Load persisted history from a JSON file, or create an empty buffer."""
        instance = cls(max_entries=max_entries, save_path=save_path)
        if os.path.isfile(save_path):
            try:
                with open(save_path, "r", encoding="utf-8") as f:
                    instance._data = json.load(f)
            except (json.JSONDecodeError, OSError):
                instance._data = []
        return instance

    def push(self, cpu: float, ram: float, vram: float, disk: float) -> None:
        """Append a metric snapshot with auto-trim and debounced save."""
        entry = {
            "ts": time.time(),
            "cpu": cpu,
            "ram": ram,
            "vram": vram,
            "disk": disk,
        }
        with self._lock:
            self._data.append(entry)
            if len(self._data) > self.max_entries:
                self._data = self._data[-self.max_entries:]
            dirty = True

    def get_series(self, metric: str) -> list[float]:
        """Return the time series for a named metric (e.g. 'cpu', 'ram')."""
        with self._lock:
            return [entry[metric] for entry in self._data if metric in entry]

    def save_immediate(self) -> None:
        """Force an immediate persist (no debounce)."""
        self._write()

    def save(self) -> None:
        """Persist data to the JSON save path (debounced)."""
        self._write()

    def _write(self) -> None:
        """Write data to disk (internal helper, no debounce)."""
        try:
            parent = os.path.dirname(self.save_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
        except OSError:
            pass
        with self._lock:
            try:
                with open(self.save_path, "w", encoding="utf-8") as f:
                    json.dump(self._data, f)
            except OSError:
                pass

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, index):
        return self._data[index]

    def latest(self, n: int = 10) -> list:
        """Return the most recent *n* entries."""
        with self._lock:
            return self._data[-n:] if self._data else []

    def clear(self) -> None:
        """Clear all data."""
        with self._lock:
            self._data.clear()
