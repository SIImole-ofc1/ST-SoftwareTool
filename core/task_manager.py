"""Task Manager backend.

psutil runs in a dedicated subprocess (proc_monitor.py) so that process
scanning never holds the GIL in the main process — the UI thread stays
completely responsive at all times.

Flow:
  _TaskWorker (QThread) ──write──► proc_monitor subprocess
                        ◄─readline── JSON process list
  readline() releases the GIL while waiting, so the UI thread is free
  during the entire subprocess scan (~50-150 ms per refresh on Windows).
"""
import os, sys, json, subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# ── data classes ─────────────────────────────────────────────────────────────

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
        self.create_time    = create_time      # 'HH:MM:SS' string or None
        self.create_time_ts = create_time_ts   # epoch float for sorting

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
        self._proc:    Optional[subprocess.Popen]    = None
        self._seen:    Dict[int, Tuple[str, float]]  = {}   # pid → (name, started_ts)
        self._history: List[HistoryEntry]             = []
        self.available = False
        self._start_subprocess()

    def _start_subprocess(self) -> None:
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proc_monitor.py')
        if not os.path.exists(script):
            return
        try:
            self._proc = subprocess.Popen(
                [sys.executable, script],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,          # line-buffered — each JSON line is one logical message
            )
            self.available = True
        except Exception:
            self._proc = None

    # ── public API ────────────────────────────────────────────────────────────

    def refresh(self) -> List[ProcessInfo]:
        if not self.available or not self._proc:
            return []

        try:
            self._proc.stdin.write('refresh\n')
            self._proc.stdin.flush()
            raw = self._proc.stdout.readline()   # GIL released here while subprocess scans
            if not raw:
                self.available = False
                return []
            data = json.loads(raw)
            if isinstance(data, dict):           # error payload from subprocess
                return []
        except Exception:
            return []

        current_pids: set        = set()
        results: List[ProcessInfo] = []
        now_ts = datetime.now().timestamp()

        for row in data:
            pid = row.get('pid')
            if pid is None:
                continue
            current_pids.add(pid)
            name       = row.get('name', f'[{pid}]')
            started_ts = float(row.get('started_ts') or 0.0)
            if pid not in self._seen:
                self._seen[pid] = (name, started_ts)
            results.append(ProcessInfo(
                pid=pid, name=name,
                cpu_percent=row.get('cpu', 0.0),
                ram_mb=row.get('ram_mb', 0.0),
                ram_percent=row.get('ram_pct', 0.0),
                status=row.get('status', 'unknown'),
                create_time=row.get('started'),
                create_time_ts=started_ts,
            ))

        gone = set(self._seen) - current_pids
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

    def history(self) -> List[HistoryEntry]:
        return list(reversed(self._history))

    def clear_history(self) -> None:
        self._history.clear()

    def kill_process(self, pid: int) -> Tuple[bool, str]:
        try:
            r = subprocess.run(
                ['taskkill', '/f', '/pid', str(pid)],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return True, ''
            return False, (r.stderr.strip() or r.stdout.strip())
        except Exception as exc:
            return False, str(exc)

    def cleanup(self) -> None:
        if self._proc:
            try:
                self._proc.stdin.write('quit\n')
                self._proc.stdin.flush()
                self._proc.wait(timeout=3)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

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
