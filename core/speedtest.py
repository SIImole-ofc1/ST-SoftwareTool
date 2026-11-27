"""Internet speed test (ping, jitter, download, upload) against Cloudflare's speed-test servers,
the same ones speed.cloudflare.com uses, plus the details of the current Wi-Fi connection.

run() is blocking (~20 s) — call it from a worker thread. progress(stage, value) reports
live numbers: ('ping', ms), ('download', Mbps), ('upload', Mbps).
"""
from __future__ import annotations

import concurrent.futures as cf
import http.client
import json
import re
import statistics
import subprocess
import threading
import time
import urllib.request
from typing import Callable, Optional

from core.osinfo import IS_MAC, IS_WINDOWS, NO_WINDOW

_BASE = 'https://speed.cloudflare.com'
_UA = 'ST-SoftwareTool speed test'
_DOWN_CHUNK = 25_000_000          # bytes per download request
_UP_CHUNK = 8_000_000             # bytes per upload request
_PHASE_SECONDS = 9.0
_WARMUP = 1.5                     # seconds of each phase not counted (connections ramping up)
_STREAMS = 4


def _get(url: str, timeout: float = 15) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': _UA, 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def server() -> str:
    """'FRA (DE)' — the Cloudflare location that answers."""
    try:
        text = _get(f'{_BASE}/cdn-cgi/trace', 8).decode()
        info = dict(line.split('=', 1) for line in text.splitlines() if '=' in line)
        return f"{info.get('colo', '?')} ({info.get('loc', '?')})"
    except Exception:
        return 'Cloudflare'


def _ping(samples: int = 14):
    """Round trips on one kept-open connection, so the time is the network's, not the
    connection setup's. Returns (median ms, jitter ms)."""
    conn = http.client.HTTPSConnection('speed.cloudflare.com', timeout=8)
    times = []
    try:
        for i in range(samples):
            t0 = time.perf_counter()
            conn.request('GET', '/__down?bytes=0', headers={'User-Agent': _UA})
            conn.getresponse().read()
            if i:                                   # the first one includes the handshake
                times.append((time.perf_counter() - t0) * 1000)
    finally:
        conn.close()
    ping = statistics.median(times)
    jitter = statistics.mean(abs(a - b) for a, b in zip(times, times[1:])) if len(times) > 1 else 0
    return ping, jitter


class _Meter:
    """Counts bytes from several streams; reports Mbps since the warm-up ended."""

    def __init__(self):
        self.bytes = 0
        self.lock = threading.Lock()
        self.start = time.perf_counter()
        self.base = 0

    def add(self, n: int) -> None:
        with self.lock:
            self.bytes += n

    def restart(self) -> None:
        """Ignore what came before (connections ramping up)."""
        with self.lock:
            self.base = self.bytes
            self.start = time.perf_counter()

    def mbps(self) -> float:
        secs = max(1e-3, time.perf_counter() - self.start)
        return (self.bytes - self.base) * 8 / secs / 1e6


def _phase(worker, progress, stage: str, cancel: threading.Event) -> float:
    meter = _Meter()
    stop = threading.Event()
    deadline = time.perf_counter() + _PHASE_SECONDS

    def loop():
        while not stop.is_set() and not cancel.is_set() and time.perf_counter() < deadline:
            try:
                worker(meter, stop, deadline)
            except Exception:
                time.sleep(0.2)

    warm_until = time.perf_counter() + _WARMUP
    warmed = False
    with cf.ThreadPoolExecutor(_STREAMS) as pool:
        futures = [pool.submit(loop) for _ in range(_STREAMS)]
        while time.perf_counter() < deadline and not cancel.is_set():
            time.sleep(0.25)
            if not warmed and time.perf_counter() >= warm_until:
                meter.restart()
                warmed = True
            progress(stage, meter.mbps())
        result = meter.mbps()           # read now: waiting for the streams to close isn't speed
        stop.set()
        cf.wait(futures, timeout=20)
    return result


def _download(meter: _Meter, stop: threading.Event, deadline: float) -> None:
    req = urllib.request.Request(f'{_BASE}/__down?bytes={_DOWN_CHUNK}',
                                 headers={'User-Agent': _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        while not stop.is_set() and time.perf_counter() < deadline:
            chunk = resp.read(256 * 1024)
            if not chunk:
                break
            meter.add(len(chunk))


class _UploadBody:
    """A file-like body that counts what has been sent."""

    def __init__(self, size: int, meter: _Meter, stop: threading.Event, deadline: float):
        self._left = size
        self._meter, self._stop, self._deadline = meter, stop, deadline
        self._block = b'0' * (64 * 1024)

    def read(self, n: int = -1) -> bytes:
        if self._left <= 0 or self._stop.is_set() or time.perf_counter() >= self._deadline:
            return b''
        n = min(self._left, len(self._block) if n is None or n < 0 else min(n, len(self._block)))
        self._left -= n
        self._meter.add(n)
        return self._block[:n]


def _upload(meter: _Meter, stop: threading.Event, deadline: float) -> None:
    body = _UploadBody(_UP_CHUNK, meter, stop, deadline)
    req = urllib.request.Request(f'{_BASE}/__up', data=body, method='POST',
                                 headers={'User-Agent': _UA, 'Content-Type': 'application/octet-stream',
                                          'Content-Length': str(_UP_CHUNK)})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except Exception:
        if not (stop.is_set() or time.perf_counter() >= deadline):
            raise          # a stopped upload ends early on purpose


def run(progress: Optional[Callable[[str, float], None]] = None,
        cancel: Optional[threading.Event] = None) -> dict:
    """The whole test. Returns {ping, jitter, download, upload, server, time}."""
    progress = progress or (lambda *_: None)
    cancel = cancel or threading.Event()
    result = {'server': server(), 'time': time.time()}
    progress('server', 0)
    ping, jitter = _ping()
    result.update(ping=round(ping, 1), jitter=round(jitter, 1))
    progress('ping', ping)
    if cancel.is_set():
        return result
    result['download'] = round(_phase(_download, progress, 'download', cancel), 1)
    if cancel.is_set():
        return result
    result['upload'] = round(_phase(_upload, progress, 'upload', cancel), 1)
    return result


# ── Wi-Fi details ────────────────────────────────────────────────────────────

def wifi_info() -> dict:
    """The current Wi-Fi connection: channel, standard, link speed, signal / noise.
    (macOS hides the network name from apps without Location permission.)"""
    try:
        if IS_MAC:
            out = subprocess.run(['/usr/sbin/system_profiler', 'SPAirPortDataType', '-json'],
                                 capture_output=True, text=True, timeout=20).stdout
            data = json.loads(out or '{}')
            for entry in data.get('SPAirPortDataType', []):
                for iface in entry.get('spairport_airport_interfaces', []):
                    cur = iface.get('spairport_current_network_information')
                    if not cur:
                        continue
                    name = cur.get('_name', '')
                    sig = str(cur.get('spairport_signal_noise', ''))
                    m = re.match(r'(-?\d+)\s*dBm\s*/\s*(-?\d+)', sig)
                    return {
                        'connected': True,
                        'name': '' if name.startswith('<') else name,
                        'standard': str(cur.get('spairport_network_phymode', '')),
                        'channel': str(cur.get('spairport_network_channel', '')),
                        'link': cur.get('spairport_network_rate'),
                        'signal': int(m.group(1)) if m else None,
                        'noise': int(m.group(2)) if m else None,
                        'security': str(cur.get('spairport_security_mode', '')).replace(
                            'spairport_security_mode_', '').replace('_', ' ').upper(),
                    }
            return {'connected': False}
        if IS_WINDOWS:
            out = subprocess.run(['netsh', 'wlan', 'show', 'interfaces'], capture_output=True,
                                 text=True, timeout=10, creationflags=NO_WINDOW).stdout
            kv = {}
            for line in out.splitlines():
                if ':' in line:
                    k, _, v = line.partition(':')
                    kv[k.strip().lower()] = v.strip()
            if kv.get('state', '').lower() != 'connected':
                return {'connected': False}
            sig = re.match(r'(\d+)%', kv.get('signal', ''))
            return {'connected': True, 'name': kv.get('ssid', ''),
                    'standard': kv.get('radio type', ''), 'channel': kv.get('channel', ''),
                    'link': kv.get('receive rate (mbps)'),
                    'signal_percent': int(sig.group(1)) if sig else None,
                    'security': kv.get('authentication', '')}
    except Exception:
        pass
    return {'connected': False}


def signal_quality(dbm: Optional[int]) -> str:
    if dbm is None:
        return ''
    return ('Excellent' if dbm >= -55 else 'Good' if dbm >= -67 else
            'Fair' if dbm >= -75 else 'Weak')
