"""
Standalone subprocess — enumerates processes with psutil and pushes one JSON
line to stdout every ~INTERVAL seconds.  The parent (ST.exe) reads these lines
in a background thread so psutil's GIL usage never stalls the UI event loop.
"""
import json
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


def main() -> None:
    cpu_procs: dict = {}   # pid -> persistent psutil.Process for cpu_percent tracking

    while True:
        t0        = time.monotonic()
        procs     = []
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

                    cpu_obj = cpu_procs.get(pid)
                    if cpu_obj is None:
                        try:
                            cpu_obj = psutil.Process(pid)
                            cpu_obj.cpu_percent(interval=None)   # seed first reading
                            cpu_procs[pid] = cpu_obj
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                        cpu_pct = 0.0
                    else:
                        try:
                            raw     = cpu_obj.cpu_percent(interval=None) or 0.0
                            cpu_pct = round(min(raw / _cpu_count, 100.0), 1)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            cpu_procs.pop(pid, None)
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

        for pid in list(cpu_procs):
            if pid not in live_pids:
                del cpu_procs[pid]

        try:
            sys.stdout.buffer.write(json.dumps(procs).encode() + b'\n')
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError):
            break

        elapsed = time.monotonic() - t0
        time.sleep(max(0.0, INTERVAL - elapsed))


if __name__ == '__main__':
    main()
