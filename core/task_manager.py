"""Task Manager backend — uses psutil directly in the calling thread.

The caller is responsible for running refresh() from a non-main thread
(e.g. a QThread) so the UI stays responsive during the process scan.
psutil Windows API calls release the GIL, so this is safe.
"""
import os, sys, subprocess
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import psutil
    _cpu_count = psutil.cpu_count(logical=True) or 1
    _PSUTIL_OK = True
except ImportError:
    _PSUTIL_OK = False
    _cpu_count = 1


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
    _ATTRS = ['pid', 'name', 'status', 'create_time', 'memory_info', 'memory_percent']

    def __init__(self):
        self._seen:    Dict[int, Tuple[str, float]] = {}
        self._history: List[HistoryEntry]           = []
        self._cache:   Dict[int, object]            = {}  # pid → psutil.Process
        self.available = _PSUTIL_OK

    # ── public API ────────────────────────────────────────────────────────────

    def refresh(self) -> List[ProcessInfo]:
        if not self.available:
            return []

        current_pids: set          = set()
        results: List[ProcessInfo] = []
        now_ts = datetime.now().timestamp()

        try:
            proc_iter = psutil.process_iter(self._ATTRS)
        except Exception:
            return []

        for proc in proc_iter:
            try:
                info = proc.info
                pid  = info.get('pid')
                if pid is None:
                    continue
                current_pids.add(pid)

                name = (info.get('name') or '').strip() or f'[{pid}]'

                started_str = None
                started_ts  = 0.0
                raw_ct = info.get('create_time')
                if raw_ct:
                    try:
                        started_str = datetime.fromtimestamp(raw_ct).strftime('%H:%M:%S')
                        started_ts  = float(raw_ct)
                    except (OSError, OverflowError, ValueError):
                        pass

                # CPU percent: seed on first encounter, measure on subsequent
                if pid not in self._cache:
                    self._cache[pid] = proc
                    try:
                        proc.cpu_percent(interval=None)
                    except Exception:
                        pass
                    cpu_pct = 0.0
                else:
                    try:
                        raw = self._cache[pid].cpu_percent(interval=None) or 0.0
                        cpu_pct = round(min(raw / _cpu_count, 100.0), 1)
                    except Exception:
                        cpu_pct = 0.0
                    self._cache[pid] = proc

                mem     = info.get('memory_info')
                ram_mb  = round(mem.rss / 1_048_576, 1) if mem else 0.0
                ram_pct = round(float(info.get('memory_percent') or 0.0), 1)

                if pid not in self._seen:
                    self._seen[pid] = (name, started_ts)

                results.append(ProcessInfo(
                    pid=pid, name=name,
                    cpu_percent=cpu_pct,
                    ram_mb=ram_mb, ram_percent=ram_pct,
                    status=info.get('status') or 'unknown',
                    create_time=started_str,
                    create_time_ts=started_ts,
                ))
            except Exception:
                continue

        # Detect processes that exited since last refresh
        gone = set(self._seen) - current_pids
        for pid in gone:
            sname, sts = self._seen.pop(pid)
            self._history.append(HistoryEntry(
                pid=pid, name=sname,
                closed=datetime.fromtimestamp(now_ts).strftime('%H:%M:%S'),
                duration=self._fmt_duration(sts, now_ts),
            ))

        # Prune stale cache entries
        for pid in list(self._cache):
            if pid not in current_pids:
                del self._cache[pid]

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
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                return True, ''
            return False, (r.stderr.strip() or r.stdout.strip())
        except Exception as exc:
            return False, str(exc)

    def cleanup(self) -> None:
        self._cache.clear()

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
