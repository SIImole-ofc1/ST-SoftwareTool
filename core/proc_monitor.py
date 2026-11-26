"""
Standalone subprocess — enumerates processes and pushes one JSON line to stdout every
~INTERVAL seconds.  The parent app reads these lines in a background thread so the
collection work never stalls the UI event loop.

Windows: psutil.
macOS:   /bin/ps.  macOS lets a normal user read the memory and CPU time of their own
         processes only, so psutil reports 0 for about half of them (everything owned by
         root or system accounts).  /bin/ps is setuid root and sees every process, and it
         also gives the full executable path instead of a 16-character name.

Run as a script (dev), via proc_monitor.exe (Windows build) or as
"<app binary> --proc-monitor" (macOS build, see main.py).
"""
import json
import os
import subprocess
import sys
import time

try:
    import psutil
    _cpu_count    = psutil.cpu_count(logical=True) or 1
    _total_ram_mb = (psutil.virtual_memory().total / 1_048_576) or 1.0
except ImportError:
    sys.exit(1)

INTERVAL = 1.8   # slightly under 2 s — reader always sees fresh data on each 2-s tick
_ATTRS   = ['pid', 'name', 'status', 'create_time', 'memory_info']
IS_MAC   = sys.platform == 'darwin'


# ── psutil (Windows, Linux, and macOS fallback) ──────────────────────────────

class _PsutilSampler:
    def __init__(self):
        self._cpu_procs: dict = {}   # pid -> persistent psutil.Process for cpu_percent

    def snapshot(self) -> list:
        procs = []
        live_pids: set = set()
        try:
            for p in psutil.process_iter(_ATTRS):
                try:
                    info = p.info
                    pid  = info['pid']
                    if pid is None:
                        continue
                    live_pids.add(pid)

                    name = (info.get('name') or '').strip() or f'[{pid}]'

                    cpu_obj = self._cpu_procs.get(pid)
                    if cpu_obj is None:
                        try:
                            cpu_obj = psutil.Process(pid)
                            cpu_obj.cpu_percent(interval=None)   # seed first reading
                            self._cpu_procs[pid] = cpu_obj
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        cpu_pct = 0.0
                    else:
                        try:
                            raw     = cpu_obj.cpu_percent(interval=None) or 0.0
                            cpu_pct = round(min(raw / _cpu_count, 100.0), 1)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            self._cpu_procs.pop(pid, None)
                            cpu_pct = 0.0

                    mem     = info.get('memory_info')
                    ram_mb  = round(mem.rss / 1_048_576, 1) if mem else 0.0
                    ram_pct = round(ram_mb / _total_ram_mb * 100.0, 1)

                    procs.append({
                        'pid':     pid,
                        'name':    name,
                        'cpu':     cpu_pct,
                        'ram_mb':  ram_mb,
                        'ram_pct': ram_pct,
                        'status':  info.get('status') or 'unknown',
                        'ct':      info.get('create_time') or 0.0,
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception:
            pass

        for pid in list(self._cpu_procs):
            if pid not in live_pids:
                del self._cpu_procs[pid]
        return procs


# ── /bin/ps (macOS) ───────────────────────────────────────────────────────────

# First letter of the BSD process state → the same words psutil uses
_PS_STATE = {'R': 'running', 'S': 'sleeping', 'I': 'idle', 'T': 'stopped',
             'U': 'disk-sleep', 'Z': 'zombie'}
_PS_CMD   = ['/bin/ps', '-axww', '-o', 'pid=,rss=,time=,state=,lstart=,comm=']
_PS_ENV   = dict(os.environ, LC_ALL='C', LANG='C')   # English dates, '.' decimals


def _cpu_seconds(text: str) -> float:
    """ps "time" ([dd-][hh:]mm:ss.ss) → seconds."""
    days = 0
    if '-' in text:
        d, text = text.split('-', 1)
        days = int(d)
    secs = 0.0
    for part in text.split(':'):
        secs = secs * 60 + float(part)
    return days * 86400 + secs


class _MacPsSampler:
    def __init__(self):
        self._prev: dict = {}   # pid -> (start_ts, cpu_seconds, wall_ts)

    def snapshot(self) -> list:
        out = subprocess.run(_PS_CMD, capture_output=True, text=True, timeout=10,
                             env=_PS_ENV, errors='replace').stdout
        now = time.monotonic()
        procs, seen = [], {}
        for line in out.splitlines():
            # pid rss time state  Dow Mon DD HH:MM:SS YYYY  command (may contain spaces)
            parts = line.split(None, 9)
            if len(parts) < 10:
                continue
            try:
                pid  = int(parts[0])
                rss  = int(parts[1])
                cpu  = _cpu_seconds(parts[2])
                ct   = time.mktime(time.strptime(' '.join(parts[4:9]), '%a %b %d %H:%M:%S %Y'))
            except (ValueError, OverflowError):
                continue
            path = parts[9].strip()
            name = os.path.basename(path.rstrip('/')) or path or f'[{pid}]'

            cpu_pct = 0.0
            prev = self._prev.get(pid)
            if prev and prev[0] == ct and now > prev[2]:   # same process as last time
                cpu_pct = (cpu - prev[1]) / (now - prev[2]) / _cpu_count * 100.0
                cpu_pct = round(min(max(cpu_pct, 0.0), 100.0), 1)
            seen[pid] = (ct, cpu, now)

            ram_mb = round(rss / 1024.0, 1)
            procs.append({
                'pid':     pid,
                'name':    name,
                'cpu':     cpu_pct,
                'ram_mb':  ram_mb,
                'ram_pct': round(ram_mb / _total_ram_mb * 100.0, 1),
                'status':  _PS_STATE.get(parts[3][:1], 'unknown'),
                'ct':      ct,
            })
        self._prev = seen
        return procs


# ── main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    sampler = _PsutilSampler()
    mac = _MacPsSampler() if IS_MAC and os.path.exists(_PS_CMD[0]) else None

    while True:
        t0 = time.monotonic()
        procs = None
        if mac is not None:
            try:
                procs = mac.snapshot() or None
            except (OSError, subprocess.SubprocessError):
                procs = None
        if procs is None:
            procs = sampler.snapshot()

        try:
            sys.stdout.buffer.write(json.dumps(procs).encode() + b'\n')
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            break

        elapsed = time.monotonic() - t0
        time.sleep(max(0.0, INTERVAL - elapsed))


if __name__ == '__main__':
    main()
