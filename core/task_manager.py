"""Task Manager backend — process data is collected by proc_monitor subprocess.

psutil runs in a completely separate process so its GIL usage never stalls
the main UI thread.  The background QThread just blocks on readline() while
proc_monitor emits one JSON snapshot every ~1.8 seconds.
"""
import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple


def _find_monitor_cmd() -> List[str]:
    """Locate proc_monitor — compiled exe (Nuitka build) or source (dev)."""
    # Nuitka places proc_monitor.exe next to ST.exe (sys.executable)
    exe = os.path.join(os.path.dirname(sys.executable), 'proc_monitor.exe')
    if os.path.exists(exe):
        return [exe]
    # Dev mode: run the source file with the current interpreter
    py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proc_monitor.py')
    if os.path.exists(py):
        return [sys.executable, py]
    return []


# ── data classes ──────────────────────────────────────────────────────────────

class ProcessInfo:
    __slots__ = ('pid', 'name', 'cpu_percent', 'ram_mb',
                 'ram_percent', 'status', 'create_time', 'create_time_ts')

    def __init__(self, pid: int, name: str, cpu_percent: float,
                 ram_mb: float, ram_percent: float, status: str,
                 create_time: Optional[str], create_time_ts: float):
        self.pid            = pid
        self.name           = name
        self.cpu_percent    = cpu_percent
        self.ram_mb         = ram_mb
        self.ram_percent    = ram_percent
        self.status         = status
        self.create_time    = create_time
        self.create_time_ts = create_time_ts

    @property
    def started_str(self) -> str:
        return self.create_time or '—'


class HistoryEntry:
    __slots__ = ('pid', 'name', '_closed', '_duration')

    def __init__(self, pid: int, name: str, closed: str, duration: str):
        self.pid       = pid
        self.name      = name
        self._closed   = closed
        self._duration = duration

    @property
    def closed_str(self) -> str:
        return self._closed

    @property
    def duration_str(self) -> str:
        return self._duration


# ── backend ───────────────────────────────────────────────────────────────────

class TaskManagerBackend:
    _MAX_HISTORY = 300

    def __init__(self):
        self._cmd:     List[str]                    = _find_monitor_cmd()
        self._proc:    Optional[subprocess.Popen]   = None
        self._lock:    threading.Lock               = threading.Lock()
        self._seen:    Dict[int, Tuple[str, float]] = {}
        self._history: List[HistoryEntry]           = []
        self.available: bool                        = bool(self._cmd)

    # ── subprocess lifecycle ──────────────────────────────────────────────────

    def start_monitor(self) -> bool:
        """Spawn proc_monitor subprocess.  Returns True on success."""
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return True   # already running
            if not self._cmd:
                return False
            try:
                self._proc = subprocess.Popen(
                    self._cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                return True
            except Exception:
                self._proc = None
                return False

    def stop_monitor(self) -> None:
        """Terminate proc_monitor.  readline() in read_procs() returns immediately."""
        with self._lock:
            if self._proc:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                self._proc = None

    def is_alive(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # ── data reading (called from background thread) ──────────────────────────

    def read_procs(self) -> Optional[List[ProcessInfo]]:
        """
        Block until proc_monitor emits one JSON line (~1.8 s), then parse it.
        Returns None if the subprocess died or produced invalid data.
        """
        with self._lock:
            proc = self._proc
        if proc is None:
            return None

        try:
            line = proc.stdout.readline()
        except Exception:
            self.stop_monitor()
            return None

        if not line:
            self.stop_monitor()
            return None

        try:
            data = json.loads(line.decode())
        except Exception:
            return None

        now_ts      = datetime.now().timestamp()
        live_pids:  set             = set()
        results:    List[ProcessInfo] = []

        for item in data:
            pid = item.get('pid')
            if pid is None:
                continue
            live_pids.add(pid)

            name = item.get('name') or f'[{pid}]'
            ct   = item.get('ct') or 0.0

            started_str: Optional[str] = None
            if ct:
                try:
                    started_str = datetime.fromtimestamp(ct).strftime('%H:%M:%S')
                except (OSError, OverflowError, ValueError):
                    pass

            if pid not in self._seen:
                self._seen[pid] = (name, ct)

            results.append(ProcessInfo(
                pid=pid,
                name=name,
                cpu_percent=item.get('cpu', 0.0),
                ram_mb=item.get('ram_mb', 0.0),
                ram_percent=item.get('ram_pct', 0.0),
                status=item.get('status', 'unknown'),
                create_time=started_str,
                create_time_ts=ct,
            ))

        # Update closed-process history
        gone = set(self._seen) - live_pids
        for pid in gone:
            sname, sts = self._seen.pop(pid)
            self._history.append(HistoryEntry(
                pid=pid, name=sname,
                closed=datetime.fromtimestamp(now_ts).strftime('%H:%M:%S'),
                duration=self._fmt_duration(sts, now_ts),
            ))

        if len(self._history) > self._MAX_HISTORY:
            self._history = self._history[-self._MAX_HISTORY:]

        return results

    # ── public helpers ────────────────────────────────────────────────────────

    def history(self) -> List[HistoryEntry]:
        return list(reversed(self._history))

    def clear_history(self) -> None:
        self._history.clear()

    def kill_process(self, pid: int) -> Tuple[bool, str]:
        try:
            r = subprocess.run(
                ['taskkill', '/f', '/pid', str(pid)],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                return True, ''
            return False, (r.stderr.strip() or r.stdout.strip())
        except Exception as exc:
            return False, str(exc)

    def cleanup(self) -> None:
        self.stop_monitor()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _fmt_duration(started_ts: float, ended_ts: float) -> str:
        if not started_ts:
            return '—'
        secs = max(0, int(ended_ts - started_ts))
        if secs < 60:
            return f'{secs}s'
        if secs < 3600:
            return f'{secs // 60}m {secs % 60}s'
        return f'{secs // 3600}h {(secs % 3600) // 60}m'
