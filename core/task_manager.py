"""Task Manager backend — process data is collected by proc_monitor subprocess.

psutil runs in a completely separate process so its GIL usage never stalls
the main UI thread.  The background QThread just blocks on readline() while
proc_monitor emits one JSON snapshot every ~1.8 seconds.

Fallback: when proc_monitor is not found (old install / first run), psutil
is used inline.  The persistent cpu_procs dict prevents the GIL-stall that
the old per-refresh dict caused.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from core.osinfo import IS_MAC, IS_WINDOWS, NO_WINDOW, mac_app_bundle, own_executable

try:
    import psutil as _psutil
    _CPU_COUNT = max(_psutil.cpu_count(logical=True) or 1, 1)
except Exception:
    _psutil = None  # type: ignore
    _CPU_COUNT = 1


def _find_monitor_cmd() -> List[str]:
    """Locate proc_monitor — compiled exe (Windows build), the app itself (macOS build)
    or the source file (dev)."""
    # Nuitka places proc_monitor.exe next to ST.exe (sys.executable)
    exe = os.path.join(os.path.dirname(sys.executable), 'proc_monitor.exe')
    if IS_WINDOWS and os.path.exists(exe):
        return [exe]
    # Dev mode: run the source file with the current interpreter
    py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'proc_monitor.py')
    if os.path.exists(py):
        return [sys.executable, py]
    # macOS app: the main binary runs the collector when started with --proc-monitor
    if IS_MAC and mac_app_bundle():
        return [own_executable(), '--proc-monitor']
    return []


# macOS processes that must never be ended: doing so crashes or logs out the Mac
_MAC_PROTECTED = {'kernel_task', 'launchd', 'WindowServer', 'loginwindow'}


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
        self._cmd:        List[str]                    = _find_monitor_cmd()
        self._proc:       Optional[subprocess.Popen]   = None
        self._lock:       threading.Lock               = threading.Lock()
        self._seen:       Dict[int, Tuple[str, float]] = {}
        self._history:    List[HistoryEntry]           = []
        # Fallback psutil state (used when proc_monitor subprocess is unavailable)
        self._cpu_procs:  dict                         = {}
        self.available:   bool                         = bool(self._cmd) or (_psutil is not None)

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
                    creationflags=NO_WINDOW,
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

    @property
    def can_read_inline(self) -> bool:
        """read_procs() can collect without the subprocess (psutil in this process)."""
        return _psutil is not None

    def is_alive(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    # ── data reading (called from background thread) ──────────────────────────

    def read_procs(self) -> Optional[List[ProcessInfo]]:
        """
        Block until proc_monitor emits one JSON line (~1.8 s), then parse it.
        Falls back to inline psutil when proc_monitor subprocess is unavailable.
        Returns None on fatal error.
        """
        with self._lock:
            proc = self._proc

        if proc is not None:
            # Subprocess path — blocks on readline (~1.8 s, zero GIL impact)
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
        elif _psutil is not None:
            # Fallback path — psutil inline with persistent cpu objects
            # Sleep first so callers don't spin; cpu_percent() needs an interval
            time.sleep(1.8)
            _ATTRS = ['pid', 'name', 'cpu_percent', 'memory_info',
                      'memory_percent', 'status', 'create_time']
            data = []
            live_pids: set = set()
            try:
                for p in _psutil.process_iter(_ATTRS):
                    try:
                        info = p.info
                        pid  = info['pid']
                        if pid is None:
                            continue
                        live_pids.add(pid)
                        cpu_obj = self._cpu_procs.get(pid)
                        if cpu_obj is None:
                            cpu_obj = _psutil.Process(pid)
                            cpu_obj.cpu_percent(interval=None)
                            self._cpu_procs[pid] = cpu_obj
                            cpu_pct = 0.0
                        else:
                            raw = cpu_obj.cpu_percent(interval=None) or 0.0
                            cpu_pct = round(min(raw / _CPU_COUNT, 100.0), 1)
                        mem = info.get('memory_info')
                        data.append({
                            'pid':     pid,
                            'name':    info.get('name') or f'[{pid}]',
                            'cpu':     cpu_pct,
                            'ram_mb':  round((mem.rss if mem else 0) / 1_048_576, 1),
                            'ram_pct': round(info.get('memory_percent') or 0.0, 1),
                            'status':  info.get('status') or 'unknown',
                            'ct':      info.get('create_time') or 0.0,
                        })
                    except (_psutil.NoSuchProcess, _psutil.AccessDenied):
                        pass
            except Exception:
                return None
            # Clean up dead processes from cpu_procs
            for dead in set(self._cpu_procs) - live_pids:
                self._cpu_procs.pop(dead, None)
        else:
            return None

        now_ts   = datetime.now().timestamp()
        results: List[ProcessInfo] = []
        live:    set = set()

        for item in data:
            pid = item.get('pid')
            if pid is None:
                continue
            live.add(pid)

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
        gone = set(self._seen) - live
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
        """End a process. Error codes: 'already_gone', 'access_denied', 'protected';
        anything else is a message to show."""
        if not IS_WINDOWS:
            return self._end_posix(pid)
        try:
            r = subprocess.run(
                ['taskkill', '/f', '/pid', str(pid)],
                capture_output=True, text=True, timeout=5,
                creationflags=NO_WINDOW,
            )
            if r.returncode == 0:
                return True, ''
            return False, (r.stderr.strip() or r.stdout.strip())
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _end_posix(pid: int, grace: float = 3.0) -> Tuple[bool, str]:
        """Ask the process to quit (SIGTERM, lets it save and clean up); if it is still
        running after `grace` seconds, force it (SIGKILL) — like Quit / Force Quit."""
        if _psutil is None:
            return False, 'psutil is not available'
        if pid <= 1 or pid == os.getpid():
            return False, 'protected'
        try:
            proc = _psutil.Process(pid)
            if IS_MAC and proc.name() in _MAC_PROTECTED:
                return False, 'protected'
            proc.terminate()
        except _psutil.NoSuchProcess:
            return False, 'already_gone'
        except _psutil.AccessDenied:
            return False, 'access_denied'
        try:
            proc.wait(timeout=grace)
            return True, ''
        except _psutil.TimeoutExpired:
            pass
        except _psutil.NoSuchProcess:
            return True, ''
        try:
            proc.kill()
            proc.wait(timeout=2)
            return True, ''
        except _psutil.NoSuchProcess:
            return True, ''
        except _psutil.AccessDenied:
            return False, 'access_denied'
        except _psutil.TimeoutExpired:
            return False, 'The process did not exit.'

    @staticmethod
    def kill_process_as_admin(pid: int) -> Tuple[bool, str]:
        """macOS: end a process owned by root or another user, after the user enters an
        administrator password. Returns (False, 'cancelled') if they cancel."""
        from core.mac_admin import run_as_admin
        pid = int(pid)   # only a number ever reaches the shell
        if pid <= 1:
            return False, 'protected'
        try:
            if _psutil is not None and _psutil.Process(pid).name() in _MAC_PROTECTED:
                return False, 'protected'
        except _psutil.NoSuchProcess:
            return False, 'already_gone'
        except _psutil.AccessDenied:
            pass
        # Quit, give it 3 s, then force; succeed if the process is gone either way
        cmd = (f'/bin/kill -TERM {pid} 2>/dev/null; i=0; '
               f'while /bin/kill -0 {pid} 2>/dev/null && [ $i -lt 30 ]; do sleep 0.1; i=$((i+1)); done; '
               f'/bin/kill -0 {pid} 2>/dev/null && /bin/kill -KILL {pid}; '
               f'sleep 0.3; ! /bin/kill -0 {pid} 2>/dev/null')
        return run_as_admin(cmd, f'ST-SoftwareTool wants to end process {pid}.')

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
