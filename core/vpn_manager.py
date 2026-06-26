"""
ST-VPN — self-hosted anonymity via the Tor network.

No VPN provider, no subscriptions, no trust required.
Traffic is routed through 3 encrypted hops across the world.
Each hop only knows its immediate neighbours — nobody sees the full path.
"""
from __future__ import annotations

import ctypes
import io
import os
import re
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import ssl
import struct
import time
import urllib.request
import urllib.parse
import winreg
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_TOR_DIR    = os.path.join(_MODULE_DIR, 'tor_bundle')
_DATA_DIR   = os.path.join(_MODULE_DIR, 'tor_data')

SOCKS_PORT   = 9050
CONTROL_PORT = 9051
HTTP_TUNNEL  = 8118

# ── Stealth / pluggable-transport modes ──────────────────────────────────────
#
# NONE     — plain Tor, IP is masked but traffic pattern is recognisable
# OBFS4    — randomises traffic; looks like noise, not Tor, not HTTPS
# SNOWFLAKE— WebRTC signalling; traffic appears to be a video call
#
STEALTH_NONE      = 'none'
STEALTH_OBFS4     = 'obfs4'
STEALTH_SNOWFLAKE = 'snowflake'

# Built-in obfs4 bridges — same defaults embedded in Tor Browser.
# Traffic is transformed into random-looking bytes; DPI cannot identify it as Tor.
_OBFS4_BRIDGES = [
    'obfs4 192.95.36.142:443 CDF2E852BF539B82BD10E27E9115A31734E378C2 cert=qUVQ0srL1JI/vO6V6m/24anYXiJD3zP8bsEFyq1cIVMBT7SB70cnFlM/Zh5V0VZRw9T27Q iat-mode=1',
    'obfs4 38.229.1.78:80 C8CBDB2464FC9804A69531437BCF2BE31FDD2EE4 cert=Hmyfd2ev46gGY7NoVxkiSFpPVd2-mgrhHtwrPp1W1vS21IOc+D4uL74HO5Q9tSAeV0e3rA iat-mode=1',
    'obfs4 85.31.186.98:443 011F2599C0E9B27EE74B353155E244813763C3E5 cert=ayq0XzCwhpdysn5o0EyDUbmSOx3X/oTEbzDMvK8sB8xke/lUrC81XW1pobdRCfloI5DOAA iat-mode=0',
    'obfs4 85.31.186.26:443 91A6354697E6B02A386312F68D82CF86824D3606 cert=T75sZN8ITvSwtp5GUbI+3aeWUv7tUlANFzTHbNYGpXNGz7r40P7b33cCAk0xR7hq4O2G6A iat-mode=0',
    'obfs4 193.11.166.194:27015 2D82C2E354D531A68469ADF7F878D7060A10AE4D cert=4TLQPJrTSaDffMK7Nbao6LC7G9OW/NHkUwIdjLSS3KYf0Nv4/nQzsW7HBqa+Fd2P4TDNNg iat-mode=0',
    'obfs4 193.11.166.194:27016 2D82C2E354D531A68469ADF7F878D7060A10AE4D cert=4TLQPJrTSaDffMK7Nbao6LC7G9OW/NHkUwIdjLSS3KYf0Nv4/nQzsW7HBqa+Fd2P4TDNNg iat-mode=1',
    'obfs4 209.148.46.65:443 74FAD13168806246602538555B5521A0383A1875 cert=ssH+9rP8dG2NLDN2XuFw63hIO/9MNNinLmxQDpVa+2JEB6RoaZ1UCILjBMkUNXJcsCIq4A iat-mode=0',
    'obfs4 37.218.245.14:38224 D9A82D2F9C2F65A18407B1D2B764F130847F8B5D cert=bjRh9Wieg6HIvCjKbKfBxIQ5B7ikTwEFvj3STVFKQJMOC5XpGqyCHe8gMPNiLkuEP8PNUA iat-mode=0',
]

# Built-in Snowflake bridges maintained by the Tor Project.
# These are identical to the ones embedded in Tor Browser.
_SNOWFLAKE_BRIDGES = [
    (
        'snowflake 192.0.2.3:80 2B280B23E1107BB62ABFC40DDCC8824814F80A72 '
        'fingerprint=2B280B23E1107BB62ABFC40DDCC8824814F80A72 '
        'url=https://snowflake-broker.torproject.net.global.prod.fastly.net/ '
        'front=cdn.sstatic.net '
        'ice=stun:stun.l.google.com:19302,stun:stun.antisip.com:3478,'
        'stun:stun.bluesip.net:3478,stun:stun.dus.net:3478,'
        'stun:stun.epygi.com:3478,stun:stun.sonetel.com:3478,'
        'stun:stun.uls.co.za:3478,stun:stun.voipgate.com:3478,'
        'stun:stun.voys.nl:3478'
    ),
    (
        'snowflake 192.0.2.4:80 8838024498816A039FCBBE93CDBC0F4A26E48F7 '
        'fingerprint=8838024498816A039FCBBE93CDBC0F4A26E48F7 '
        'url=https://1098762253.rsc.cdn77.org/ '
        'front=www.phpmyadmin.net '
        'ice=stun:stun.l.google.com:19302,stun:stun.antisip.com:3478,'
        'stun:stun.bluesip.net:3478,stun:stun.dus.net:3478,'
        'stun:stun.epygi.com:3478,stun:stun.sonetel.com:3478,'
        'stun:stun.uls.co.za:3478,stun:stun.voipgate.com:3478,'
        'stun:stun.voys.nl:3478'
    ),
]

_PROXY_REG = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'

# Tor Expert Bundle — Windows x86_64
# Check https://www.torproject.org/download/tor/ for the latest version.
_BUNDLE_URL = (
    'https://archive.torproject.org/tor-package-archive/torbrowser/'
    '14.0.3/tor-expert-bundle-windows-x86_64-14.0.3.tar.gz'
)
_BUNDLE_FALLBACK = 'https://www.torproject.org/download/tor/'

# Common places Tor can be found
_TOR_CANDIDATES = [
    os.path.join(_TOR_DIR, 'tor', 'tor.exe'),   # our extracted bundle
    os.path.join(_TOR_DIR, 'tor.exe'),
    r'C:\Program Files\Tor Browser\Browser\TorBrowser\Tor\tor.exe',
    r'C:\Program Files (x86)\Tor Browser\Browser\TorBrowser\Tor\tor.exe',
]


# ── data classes ─────────────────────────────────────────────────────────────

@dataclass
class Hop:
    index:       int
    nickname:    str = ''
    fingerprint: str = ''
    ip:          str = ''
    country:     str = '??'
    role:        str = ''    # Guard / Middle / Exit


@dataclass
class VpnStats:
    bytes_sent:  int   = 0
    bytes_recv:  int   = 0
    duration_s:  float = 0.0
    circuit:     List[Hop] = field(default_factory=list)


# ── manager ───────────────────────────────────────────────────────────────────

class TorVpnManager:

    def __init__(self):
        self._proc:        Optional[subprocess.Popen] = None
        self._ctrl:        Optional[socket.socket]    = None
        self._connected    = False
        self._start_time   = 0.0
        self._bytes_base:  Tuple[int, int] = (0, 0)
        self._circuit:     List[Hop] = []
        self._lock         = threading.Lock()
        self._stealth_mode = STEALTH_NONE
        self._obfs4_bridges: List[str] = []   # user-supplied obfs4 bridge lines

    # ── public: tor location ──────────────────────────────────────────────────

    def find_tor(self) -> Optional[str]:
        home = os.path.expanduser('~')
        extra = [
            os.path.join(home, 'Desktop', 'Tor Browser',
                         'Browser', 'TorBrowser', 'Tor', 'tor.exe'),
            os.path.join(home, 'Downloads', 'Tor Browser',
                         'Browser', 'TorBrowser', 'Tor', 'tor.exe'),
        ]
        for c in _TOR_CANDIDATES + extra:
            if c and os.path.exists(c):
                return c
        found = shutil.which('tor')
        return found if found else None

    def is_tor_available(self) -> bool:
        return self.find_tor() is not None

    def is_connected(self) -> bool:
        return self._connected

    # ── public: download ──────────────────────────────────────────────────────

    def download_tor(self,
                     progress_cb: Optional[Callable[[int, str], None]] = None,
                     ) -> Tuple[bool, str]:
        """Download and extract the Tor Expert Bundle into _TOR_DIR."""
        os.makedirs(_TOR_DIR, exist_ok=True)
        tmp = os.path.join(tempfile.gettempdir(), 'tor_expert_bundle.tar.gz')

        if progress_cb:
            progress_cb(0, 'Connecting to torproject.org…')

        def _hook(count, block, total):
            if total > 0 and progress_cb:
                pct = min(85, int(count * block * 85 / total))
                mb  = count * block / 1_048_576
                progress_cb(pct, f'Downloading… {mb:.1f} MB')

        try:
            urllib.request.urlretrieve(_BUNDLE_URL, tmp, _hook)
        except Exception as exc:
            return False, (
                f'Download failed: {exc}\n\n'
                f'Please visit  {_BUNDLE_FALLBACK}\n'
                f'and download the Windows Expert Bundle manually,\n'
                f'then extract  tor.exe  into:\n{_TOR_DIR}'
            )

        if progress_cb:
            progress_cb(88, 'Extracting…')

        try:
            with tarfile.open(tmp, 'r:gz') as tf:
                tf.extractall(_TOR_DIR)
            os.remove(tmp)
        except Exception as exc:
            return False, f'Extraction failed:\n{exc}'

        if progress_cb:
            progress_cb(100, 'Done')

        if self.is_tor_available():
            return True, ''
        return False, (
            'Tor was downloaded but  tor.exe  was not found inside the archive.\n'
            f'Please extract it manually into:\n{_TOR_DIR}'
        )

    # ── public: connect ───────────────────────────────────────────────────────

    def connect(self,
                progress_cb: Optional[Callable[[int, str], None]] = None,
                ) -> Tuple[bool, str]:
        """
        Start Tor, wait for full bootstrap, then configure the Windows system
        proxy so all traffic from proxy-aware applications goes through Tor.
        """
        tor = self.find_tor()
        if not tor:
            return False, (
                'Tor is not installed.\n\n'
                'Click  "Download Tor"  and ST-VPN will handle the rest.'
            )

        os.makedirs(_DATA_DIR, exist_ok=True)
        torrc = self._write_torrc()

        if progress_cb:
            progress_cb(1, 'Launching Tor…')

        try:
            self._proc = subprocess.Popen(
                [tor, '-f', torrc],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding='utf-8',
                errors='replace',
            )
        except OSError as exc:
            return False, f'Could not launch Tor:\n{exc}'

        ok = self._wait_bootstrap(progress_cb, timeout=120)
        if not ok:
            # Collect any [err] lines from Tor's output for a useful message
            err_lines = [l for l in getattr(self, '_last_tor_output', [])
                         if '[err]' in l or '[warn]' in l]
            detail = ''
            if err_lines:
                detail = '\n\nTor reported:\n' + '\n'.join(err_lines[-5:])
            self._kill_tor()
            return False, (
                'Tor could not connect to the network.\n\n'
                'Common causes:\n'
                '  • Firewall or antivirus blocking Tor (allow ports 443 and 9001)\n'
                '  • No internet connection\n'
                '  • Tor Browser is already open and holding port 9050 — close it first'
                + detail
            )

        self._open_control()
        self._set_proxy(True)

        with self._lock:
            self._connected  = True
            self._start_time = time.monotonic()
            self._bytes_base = _net_bytes()

        # Fetch circuit info in background (doesn't block connect)
        threading.Thread(target=self._refresh_circuit, daemon=True).start()

        if progress_cb:
            progress_cb(100, 'Connected — traffic is now routed through Tor')

        return True, ''

    # ── public: disconnect ────────────────────────────────────────────────────

    def disconnect(self) -> Tuple[bool, str]:
        self._set_proxy(False)
        self._close_control()
        self._kill_tor()
        with self._lock:
            self._connected = False
            self._circuit   = []
        return True, ''

    # ── public: circuit ───────────────────────────────────────────────────────

    def set_stealth(self, mode: str, obfs4_bridges: Optional[List[str]] = None) -> None:
        """Configure stealth / pluggable-transport mode before calling connect()."""
        self._stealth_mode   = mode
        self._obfs4_bridges  = obfs4_bridges or []

    def auto_stealth(self) -> str:
        """Automatically pick the best available stealth mode.

        Priority: obfs4 > Snowflake > plain Tor.
        Returns a human-readable description of the selected mode.
        """
        obfs4_ok, snow_ok = self.pt_status()
        if obfs4_ok:
            self.set_stealth(STEALTH_OBFS4, [])
            return 'obfs4 active — traffic disguised as random noise'
        elif snow_ok:
            self.set_stealth(STEALTH_SNOWFLAKE, [])
            return 'Snowflake active — traffic tunnelled over WebRTC'
        else:
            self.set_stealth(STEALTH_NONE, [])
            return 'Standard Tor  (add obfs4proxy.exe for full stealth)'

    def pt_status(self) -> Tuple[bool, bool]:
        """Return (obfs4_available, snowflake_available)."""
        return (
            self._find_pt('obfs4proxy') is not None or
            self._find_pt('lyrebird')   is not None,
            self._find_pt('snowflake-client') is not None,
        )

    def new_identity(self) -> Tuple[bool, str]:
        """Ask Tor for a fresh circuit (new exit IP, new route).

        Tor enforces a 10-second rate limit between NEWNYM signals internally.
        We don't block on it — callers can fire this as often as they like;
        Tor will apply each request as soon as the rate-limit window passes.
        Returns (success, message).
        """
        resp = self._ctrl_cmd('SIGNAL NEWNYM')
        if resp.startswith('250'):
            threading.Thread(target=self._refresh_circuit, daemon=True).start()
            return True, ''
        if '515' in resp:
            # Tor is rate-limiting but WILL change the circuit soon
            threading.Thread(target=self._refresh_circuit, daemon=True).start()
            return True, 'Rate limit — new circuit will activate in a moment'
        return False, f'Control error: {resp.strip()}'

    def get_circuit(self) -> List[Hop]:
        with self._lock:
            return list(self._circuit)

    # ── public: stats / IP ───────────────────────────────────────────────────

    def get_stats(self) -> VpnStats:
        s = VpnStats()
        with self._lock:
            if not self._connected:
                return s
            s.duration_s = time.monotonic() - self._start_time
            s.circuit    = list(self._circuit)
            base = self._bytes_base
        cur = _net_bytes()
        s.bytes_sent = max(0, cur[0] - base[0])
        s.bytes_recv = max(0, cur[1] - base[1])
        return s

    def fetch_ips(self) -> Tuple[str, str]:
        """Return (ipv4, ipv6) of the current outgoing address.

        When connected, routes the check through Tor's SOCKS5 port so the
        result reflects what websites actually see, not the real local IP.
        """
        if self._connected:
            # Go through the Tor SOCKS5 proxy — this is what sites see
            v4 = self._socks5_get('http://api4.ipify.org') or \
                 self._socks5_get('http://checkip.amazonaws.com')
            v6 = self._socks5_get('http://api6.ipify.org')
        else:
            v4 = _http_get('https://api.ipify.org')
            v6 = _http_get('https://api6.ipify.org')
        return v4, v6

    def _socks5_get(self, url: str) -> str:
        """
        Fetch a plain-HTTP URL through Tor's SOCKS5 proxy using only stdlib.
        No external libraries (PySocks, requests) required.
        """
        try:
            p = urllib.parse.urlparse(url)
            host = p.hostname or ''
            port = p.port or (443 if p.scheme == 'https' else 80)
            path = (p.path or '/') + (f'?{p.query}' if p.query else '')

            # ── SOCKS5 handshake ──────────────────────────────────────────────
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(12)
            s.connect(('127.0.0.1', SOCKS_PORT))

            # Greeting: VERSION=5, NMETHODS=1, METHOD=0x00 (no auth)
            s.sendall(b'\x05\x01\x00')
            resp = s.recv(2)
            if len(resp) < 2 or resp[0] != 5 or resp[1] != 0:
                s.close()
                return ''

            # CONNECT request: VER CMD RSV ATYP(domain) len(host) host port
            host_b = host.encode('idna')
            s.sendall(
                bytes([5, 1, 0, 3, len(host_b)]) +
                host_b +
                struct.pack('!H', port)
            )
            # Response: VER REP RSV ATYP ... (at least 10 bytes for IPv4)
            conn_resp = s.recv(10)
            if len(conn_resp) < 2 or conn_resp[1] != 0:
                s.close()
                return ''

            # ── optional TLS ──────────────────────────────────────────────────
            if p.scheme == 'https':
                ctx = ssl.create_default_context()
                s = ctx.wrap_socket(s, server_hostname=host)

            # ── HTTP/1.0 GET ──────────────────────────────────────────────────
            s.sendall(
                f'GET {path} HTTP/1.0\r\n'
                f'Host: {host}\r\n'
                f'User-Agent: ST-VPN/1.0\r\n'
                f'Connection: close\r\n\r\n'
                .encode()
            )
            data = b''
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            s.close()

            # Strip HTTP response headers
            parts = data.split(b'\r\n\r\n', 1)
            if len(parts) < 2:
                return ''
            return parts[1].decode('utf-8', errors='replace').strip()
        except Exception:
            return ''

    # ── formatting helpers ────────────────────────────────────────────────────

    @staticmethod
    def fmt_bytes(n: int) -> str:
        for unit, div in (('GB', 1 << 30), ('MB', 1 << 20), ('KB', 1 << 10)):
            if n >= div:
                return f'{n / div:.2f} {unit}'
        return f'{n} B'

    @staticmethod
    def fmt_duration(secs: float) -> str:
        s = int(secs)
        h, r = divmod(s, 3600)
        m, s = divmod(r, 60)
        return f'{h:02d}:{m:02d}:{s:02d}'

    # ── internal: torrc ───────────────────────────────────────────────────────

    def _find_pt(self, name: str) -> Optional[str]:
        """Locate a pluggable-transport binary next to tor.exe."""
        tor = self.find_tor()
        if not tor:
            return None
        tor_dir = os.path.dirname(tor)
        candidates = [
            os.path.join(tor_dir, 'PluggableTransports', f'{name}.exe'),
            os.path.join(tor_dir, '..', 'PluggableTransports', f'{name}.exe'),
            os.path.join(tor_dir, f'{name}.exe'),
        ]
        for c in candidates:
            normed = os.path.normpath(c)
            if os.path.exists(normed):
                return normed
        found = shutil.which(name)
        return found

    def _write_torrc(self) -> str:
        # Locate geoip databases
        geoip4 = geoip6 = ''
        for sub in ('tor/data', 'data', ''):
            p4 = os.path.join(_TOR_DIR, sub, 'geoip')
            p6 = os.path.join(_TOR_DIR, sub, 'geoip6')
            if os.path.exists(p4):
                geoip4 = p4
            if os.path.exists(p6):
                geoip6 = p6

        lines = [
            f'SocksPort {SOCKS_PORT}',
            f'ControlPort {CONTROL_PORT}',
            f'HTTPTunnelPort {HTTP_TUNNEL}',
            'CookieAuthentication 1',
            f'DataDirectory {_DATA_DIR}',
            'Log notice stdout',
        ]
        if geoip4:
            lines.append(f'GeoIPFile {geoip4}')
        if geoip6:
            lines.append(f'GeoIPv6File {geoip6}')

        # ── Stealth / pluggable transports ────────────────────────────────────
        if self._stealth_mode == STEALTH_OBFS4:
            # obfs4proxy (also shipped as lyrebird in newer Tor bundles)
            pt = self._find_pt('obfs4proxy') or self._find_pt('lyrebird')
            if pt:
                bridges = self._obfs4_bridges or _OBFS4_BRIDGES
                lines += [
                    'UseBridges 1',
                    f'ClientTransportPlugin obfs4 exec "{pt}"',
                ]
                for b in bridges:
                    b = b.strip()
                    if b and not b.startswith('#'):
                        prefix = '' if b.lower().startswith('bridge ') else 'Bridge '
                        lines.append(f'{prefix}{b}')

        elif self._stealth_mode == STEALTH_SNOWFLAKE:
            pt = self._find_pt('snowflake-client')
            if pt:
                log = os.path.join(_DATA_DIR, 'snowflake.log')
                lines += [
                    'UseBridges 1',
                    f'ClientTransportPlugin snowflake exec "{pt}" -log "{log}"',
                ]
                for b in _SNOWFLAKE_BRIDGES:
                    lines.append(f'Bridge {b}')

        path = os.path.join(_DATA_DIR, 'torrc')
        with open(path, 'w') as f:
            f.write('\n'.join(lines) + '\n')
        return path

    # ── internal: bootstrap ───────────────────────────────────────────────────

    def _wait_bootstrap(self,
                        progress_cb: Optional[Callable[[int, str], None]],
                        timeout: int = 120) -> bool:
        deadline = time.monotonic() + timeout
        pct_prev = 0
        self._last_tor_output: list[str] = []

        while time.monotonic() < deadline:
            if self._proc is None:
                return False
            # Process died early — capture remaining output for error message
            if self._proc.poll() is not None:
                remaining = self._proc.stdout.read()
                if remaining:
                    self._last_tor_output.extend(remaining.splitlines())
                return False

            line = self._proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue

            line = line.strip()
            self._last_tor_output.append(line)
            if len(self._last_tor_output) > 60:
                self._last_tor_output.pop(0)

            # Detect fatal config errors — no point waiting further
            if '[err]' in line:
                if progress_cb:
                    progress_cb(0, f'Tor error: {line}')
                # Drain remaining output then bail
                self._proc.stdout.read()
                return False

            m = re.search(r'Bootstrapped (\d+)%.*?:\s*(.*)', line)
            if m:
                pct = int(m.group(1))
                msg = m.group(2).strip()
                if pct != pct_prev and progress_cb:
                    progress_cb(pct, f'{pct}%  —  {msg}')
                pct_prev = pct
                if pct >= 100:
                    return True

        return False

    # ── internal: control port ────────────────────────────────────────────────

    def _open_control(self):
        # Read the cookie Tor wrote during startup
        cookie_hex = ''
        cookie_path = os.path.join(_DATA_DIR, 'control_auth_cookie')
        for _ in range(30):   # wait up to 3 s for the file to appear
            if os.path.exists(cookie_path):
                break
            time.sleep(0.1)
        try:
            with open(cookie_path, 'rb') as f:
                cookie_hex = f.read().hex()
        except OSError:
            pass

        try:
            s = socket.socket()
            s.settimeout(5)
            s.connect(('127.0.0.1', CONTROL_PORT))
            s.sendall(f'AUTHENTICATE {cookie_hex}\r\n'.encode())
            _recv_ctrl(s)
            s.settimeout(4)
            self._ctrl = s
        except OSError:
            self._ctrl = None

    def _close_control(self):
        if self._ctrl:
            try:
                self._ctrl.sendall(b'QUIT\r\n')
                self._ctrl.close()
            except OSError:
                pass
            self._ctrl = None

    def _ctrl_cmd(self, cmd: str) -> str:
        if not self._ctrl:
            return ''
        try:
            self._ctrl.sendall(f'{cmd}\r\n'.encode())
            return _recv_ctrl(self._ctrl)
        except OSError:
            return ''

    # ── internal: circuit parsing ─────────────────────────────────────────────

    def _refresh_circuit(self):
        hops = self._build_circuit()
        with self._lock:
            self._circuit = hops

    def _build_circuit(self) -> List[Hop]:
        raw = self._ctrl_cmd('GETINFO circuit-status')
        hops: List[Hop] = []

        # Find the first BUILT circuit with 3 hops
        for line in raw.splitlines():
            if 'BUILT' not in line:
                continue
            parts = line.split()
            if len(parts) < 3:
                continue
            path_str = parts[2]
            hop_strs = path_str.split(',')
            if len(hop_strs) < 2:
                continue
            roles = ['Guard', 'Middle', 'Exit'] if len(hop_strs) >= 3 else ['Guard', 'Exit']
            for idx, hs in enumerate(hop_strs):
                m = re.match(r'\$?([0-9A-Fa-f]+)(?:~(\w+))?', hs)
                if not m:
                    continue
                hop = Hop(
                    index       = idx + 1,
                    fingerprint = m.group(1),
                    nickname    = m.group(2) or '',
                    role        = roles[min(idx, len(roles) - 1)],
                )
                # Resolve fingerprint → router status entry → IP
                ns = self._ctrl_cmd(f'GETINFO ns/id/{hop.fingerprint}')
                ip_m = re.search(r'^r \S+ \S+ \S+ \S+ \S+ (\S+) ', ns, re.M)
                if ip_m:
                    hop.ip = ip_m.group(1)
                    cc = self._ctrl_cmd(f'GETINFO ip-to-country/{hop.ip}')
                    m2 = re.search(r'ip-to-country/[\d.]+=(\S+)', cc)
                    if m2:
                        hop.country = m2.group(1).upper()
                hops.append(hop)
            break   # use first BUILT circuit only

        return hops

    # ── internal: proxy / process / network ───────────────────────────────────

    def _set_proxy(self, enable: bool):
        # Use both the HTTP tunnel (port 8118) for http/https traffic
        # AND the SOCKS5 port (9050) — together they cover virtually all apps.
        proxy_str = (
            f'http=127.0.0.1:{HTTP_TUNNEL};'
            f'https=127.0.0.1:{HTTP_TUNNEL};'
            f'socks=127.0.0.1:{SOCKS_PORT}'
        )
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _PROXY_REG, 0, winreg.KEY_SET_VALUE
            )
            if enable:
                winreg.SetValueEx(key, 'ProxyEnable', 0, winreg.REG_DWORD, 1)
                winreg.SetValueEx(key, 'ProxyServer', 0, winreg.REG_SZ, proxy_str)
                # Exclude localhost from the proxy so local apps still work
                winreg.SetValueEx(
                    key, 'ProxyOverride', 0, winreg.REG_SZ,
                    'localhost;127.*;10.*;172.16.*;192.168.*;<local>'
                )
            else:
                winreg.SetValueEx(key, 'ProxyEnable', 0, winreg.REG_DWORD, 0)
                for name in ('ProxyServer', 'ProxyOverride'):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
            winreg.CloseKey(key)
        except OSError:
            pass
        # Notify WinINet so the change takes effect immediately
        try:
            wi = ctypes.windll.wininet
            wi.InternetSetOptionW(0, 37, None, 0)   # SETTINGS_CHANGED
            wi.InternetSetOptionW(0, 39, None, 0)   # REFRESH
        except Exception:
            pass

    def _kill_tor(self):
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=8)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None


# ── module-level helpers ──────────────────────────────────────────────────────

def _recv_ctrl(sock: socket.socket, max_bytes: int = 65536) -> str:
    """Read from a Tor control socket until we see a final response line."""
    buf = b''
    try:
        while len(buf) < max_bytes:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            text = buf.decode('utf-8', errors='replace')
            # Tor control protocol: final line starts with "NNN " (space, not dash)
            if re.search(r'^\d{3} ', text, re.MULTILINE):
                return text
    except socket.timeout:
        pass
    return buf.decode('utf-8', errors='replace')


def _net_bytes() -> Tuple[int, int]:
    try:
        import psutil
        c = psutil.net_io_counters()
        return c.bytes_sent, c.bytes_recv
    except Exception:
        return 0, 0


def _http_get(url: str, timeout: int = 8) -> str:
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'ST-VPN/1.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode().strip()
    except Exception:
        return ''
