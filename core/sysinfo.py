"""System details for the menu-bar popup: cores, memory, disk, network, battery, top apps.

snapshot() is called every ~2 s while the popup is open, from a worker thread. Slow or
rarely-changing values (battery health, public IP, OS name) are cached.
"""
from __future__ import annotations

import ctypes
import ctypes.util
import os
import platform
import plistlib
import socket
import subprocess
import threading
import time
import urllib.request
from typing import Optional

from core.osinfo import IS_MAC, IS_WINDOWS

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

_libc = None
_procs: dict = {}                     # pid -> psutil.Process (keeps cpu_percent baselines)
_lock = threading.Lock()
_cache: dict = {}


def _sysctl_int(name: str) -> Optional[int]:
    """macOS sysctl value as an int (no subprocess)."""
    global _libc
    if not IS_MAC:
        return None
    try:
        if _libc is None:
            _libc = ctypes.CDLL(ctypes.util.find_library('c'))
        value = ctypes.c_int64(0)
        size = ctypes.c_size_t(ctypes.sizeof(value))
        if _libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
            return None
        return int(value.value) if size.value == 8 else int(ctypes.c_int32(value.value).value)
    except Exception:
        return None


def _cached(key: str, max_age: float, fn):
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < max_age:
        return hit[1]
    try:
        value = fn()
    except Exception:
        value = hit[1] if hit else None
    _cache[key] = (now, value)
    return value


# ── static-ish ───────────────────────────────────────────────────────────────

def os_name() -> str:
    if IS_MAC:
        return f'macOS {platform.mac_ver()[0]}'
    if IS_WINDOWS:
        return f'Windows {platform.release()}'
    return platform.platform(terse=True)


def core_layout() -> str:
    """'10 cores · 4P + 6E' on Apple Silicon, else '8 cores'."""
    logical = (psutil.cpu_count() if psutil else os.cpu_count()) or 0
    p = _sysctl_int('hw.perflevel0.logicalcpu')
    e = _sysctl_int('hw.perflevel1.logicalcpu')
    if p and e:
        return f'{logical} cores · {p}P + {e}E'
    return f'{logical} cores'


def _uptime() -> str:
    if psutil is None:
        return ''
    secs = int(time.time() - psutil.boot_time())
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    return f'{d}d {h}h' if d else f'{h}h {m}m'


def _local_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(('192.0.2.1', 9))         # no packet is sent; picks the outgoing interface
        return s.getsockname()[0]
    except OSError:
        return '—'
    finally:
        s.close()


def _public_ip() -> str:
    req = urllib.request.Request('https://api.ipify.org', headers={'User-Agent': 'ST-SoftwareTool'})
    with urllib.request.urlopen(req, timeout=6) as resp:
        ip = resp.read().decode().strip()
    return ip if len(ip) <= 45 else '—'


def _battery_health() -> Optional[dict]:
    """macOS: maximum capacity vs. design, and charge cycles (from ioreg)."""
    if not IS_MAC:
        return None
    out = subprocess.run(['/usr/sbin/ioreg', '-rn', 'AppleSmartBattery', '-a'],
                         capture_output=True, timeout=5).stdout
    if not out:
        return None
    items = plistlib.loads(out)
    if not items:
        return None
    b = items[0]
    data = b.get('BatteryData') or {}          # newer macOS keeps the mAh values here
    design = b.get('DesignCapacity') or data.get('DesignCapacity') or 0
    maxcap = (b.get('AppleRawMaxCapacity') or b.get('NominalChargeCapacity')
              or data.get('NominalChargeCapacity') or data.get('FullChargeCapacity') or 0)
    health = round(100 * maxcap / design) if design and maxcap else None
    return {'health': min(health, 100) if health else None, 'cycles': b.get('CycleCount')}


def _pressure() -> Optional[str]:
    level = _sysctl_int('kern.memorystatus_vm_pressure_level')
    return {1: 'Normal', 2: 'Warning', 4: 'Critical'}.get(level) if level else None


def _top_apps(n: int = 3) -> list:
    """Top processes by CPU since the previous call."""
    if psutil is None:
        return []
    seen, rows = set(), []
    for proc in psutil.process_iter(['name']):
        pid = proc.pid
        seen.add(pid)
        if pid not in _procs:
            _procs[pid] = proc
            try:
                proc.cpu_percent(None)       # first call only sets the baseline
            except Exception:
                pass
            continue
        try:
            pct = _procs[pid].cpu_percent(None)
        except Exception:
            continue
        if pct > 0.1 and pid != 0:
            rows.append((pct, proc.info.get('name') or str(pid)))
    for pid in list(_procs):
        if pid not in seen:
            del _procs[pid]
    rows.sort(reverse=True)
    cores = psutil.cpu_count() or 1
    return [(name, pct / cores) for pct, name in rows[:n]]   # % of the whole CPU


def _main_disk():
    for path in (('/System/Volumes/Data', '/') if IS_MAC else
                 (os.environ.get('SystemDrive', 'C:') + '\\',) if IS_WINDOWS else ('/',)):
        if os.path.isdir(path):
            try:
                u = psutil.disk_usage(path)
                name = 'Macintosh HD' if IS_MAC else path
                if IS_MAC:
                    try:
                        name = subprocess.run(
                            ['/usr/sbin/diskutil', 'info', '-plist', '/'], capture_output=True,
                            timeout=5).stdout
                        name = plistlib.loads(name).get('VolumeName') or 'Macintosh HD'
                    except Exception:
                        name = 'Macintosh HD'
                return {'name': name, 'used': u.used, 'free': u.free, 'total': u.total}
            except OSError:
                continue
    return None


# ── the snapshot ─────────────────────────────────────────────────────────────

def snapshot() -> dict:
    """Everything the popup's details show. Blocking (~20–80 ms); call off the UI thread."""
    if psutil is None:
        return {}
    with _lock:
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = None
        bat = psutil.sensors_battery() if hasattr(psutil, 'sensors_battery') else None
        battery = None
        if bat is not None:
            secs = bat.secsleft
            left = (None if secs in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN)
                    or secs is None or secs < 0 else secs)
            battery = {'percent': bat.percent, 'plugged': bat.power_plugged, 'secs_left': left}
            extra = _cached('battery_health', 60, _battery_health) or {}
            battery.update(extra)
        public = _cache.get('public_ip', (0, None))[1]
        if time.monotonic() - _cache.get('public_ip', (-1e9, None))[0] > 600:
            # refresh in the background so a slow network never stalls the popup
            _cache['public_ip'] = (time.monotonic(), public)
            threading.Thread(target=lambda: _cached_public(), daemon=True).start()
        return {
            'cores': psutil.cpu_percent(percpu=True),
            'layout': _cached('layout', 3600, core_layout),
            'load': load,
            'mem': {'used': vm.total - vm.available, 'available': vm.available, 'total': vm.total,
                    'swap': sw.used, 'pressure': _pressure()},
            'disk': _cached('disk', 30, _main_disk),
            'local_ip': _cached('local_ip', 15, _local_ip),
            'public_ip': public,
            'battery': battery,
            'top': _top_apps(),
            'os': _cached('os', 3600, os_name),
            'uptime': _uptime(),
        }


def _cached_public() -> None:
    try:
        ip = _public_ip()
    except Exception:
        ip = None
    _cache['public_ip'] = (time.monotonic(), ip)
