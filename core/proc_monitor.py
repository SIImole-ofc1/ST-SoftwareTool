#!/usr/bin/env python3
"""
Subprocess process monitor — psutil runs here, completely isolated from the
main process and its GIL.  The main app communicates via stdin/stdout:

  stdin  ← 'refresh\n'   ask for a fresh process snapshot
  stdout → one JSON line  list of process dicts
  stdin  ← 'quit\n'      shut down cleanly
"""
import sys, json
from datetime import datetime

try:
    import psutil
except ImportError:
    print(json.dumps({'error': 'psutil_missing'}), flush=True)
    sys.exit(1)

_cpu_count = psutil.cpu_count(logical=True) or 1
_cache: dict = {}   # pid -> psutil.Process, maintained across refreshes for cpu_percent
_ATTRS = ['pid', 'name', 'status', 'create_time', 'memory_info', 'memory_percent']


def _refresh() -> list:
    current_pids: set = set()
    rows: list = []

    for proc in psutil.process_iter(_ATTRS):
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
                except (OSError, OverflowError):
                    pass

            if pid not in _cache:
                _cache[pid] = proc
                try:
                    proc.cpu_percent(interval=None)  # seed — always 0 on first call
                except Exception:
                    pass
                cpu_pct = 0.0
            else:
                try:
                    raw = _cache[pid].cpu_percent(interval=None) or 0.0
                    cpu_pct = round(min(raw / _cpu_count, 100.0), 1)
                except Exception:
                    cpu_pct = 0.0
                _cache[pid] = proc

            mem     = info.get('memory_info')
            ram_mb  = round(mem.rss / 1_048_576, 1) if mem else 0.0
            ram_pct = round(float(info.get('memory_percent') or 0.0), 1)

            rows.append({
                'pid':        pid,
                'name':       name,
                'cpu':        cpu_pct,
                'ram_mb':     ram_mb,
                'ram_pct':    ram_pct,
                'status':     info.get('status') or 'unknown',
                'started':    started_str,
                'started_ts': started_ts,
            })
        except Exception:
            continue

    for pid in set(_cache) - current_pids:
        del _cache[pid]

    return rows


if __name__ == '__main__':
    for raw_line in sys.stdin:
        cmd = raw_line.strip()
        if cmd == 'refresh':
            try:
                print(json.dumps(_refresh()), flush=True)
            except Exception as exc:
                print(json.dumps({'error': str(exc)}), flush=True)
        elif cmd == 'quit':
            break
