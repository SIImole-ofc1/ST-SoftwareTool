"""Detection engines used by the ST-AntiVirus scanner.

Cross-platform:
* HashCloud     — Team Cymru Malware Hash Registry (free, no key): TXT lookup of a
                  file's SHA-1, answers with the % of AV engines that detect it.
                  Uses the system resolver on Windows, DNS-over-HTTPS elsewhere / as fallback.
* LOCAL_SIGS    — built-in hashes that work offline (EICAR test file, etc.).
* exec_kind()   — recognises Windows (PE), Linux (ELF) and macOS (Mach-O) executables.
* ScanCache     — SQLite cache so unchanged clean files are not re-read every scan.

Windows only (no-ops elsewhere):
* Amsi          — Antimalware Scan Interface: asks the installed AV engine to scan a buffer.
* is_signed()   — Authenticode check (WinVerifyTrust) used to suppress heuristic
                  false positives on validly signed software.
* security_center_products() — which AV products are installed and active.
"""
from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.request
from typing import Optional, Tuple

IS_WINDOWS = sys.platform == 'win32'
if IS_WINDOWS:
    from ctypes import wintypes


def _data_dir() -> str:
    if IS_WINDOWS:
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
    elif sys.platform == 'darwin':
        base = os.path.expanduser('~/Library/Application Support')
    else:
        base = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    return os.path.join(base, 'ST-SoftwareTool')


DATA_DIR = _data_dir()


# ── executable formats ────────────────────────────────────────────────────────
_MACHO_MAGICS = (b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe', b'\xcf\xfa\xed\xfe')


def exec_kind(head: bytes) -> Optional[str]:
    """'pe' (Windows), 'elf' (Linux), 'macho' (macOS) or None for non-executables."""
    if head[:2] == b'MZ':
        return 'pe'
    if head[:4] == b'\x7fELF':
        return 'elf'
    if head[:4] in _MACHO_MAGICS:
        return 'macho'
    return None

# ── local signatures (hashes only — never the malware bytes themselves) ───────
# md5 / sha1 of known-bad files. EICAR = the industry-standard harmless AV test file.
LOCAL_SIGS = {
    '44d88612fea8a8f36de82e1278abb02f':          'EICAR-Test-File',
    '3395856ce81f2b7382dee72602f798b642f14140':  'EICAR-Test-File',
}

# Detection threshold for the hash registry (% of AV engines that flag the file)
CLOUD_CONFIRMED_PCT = 25


# ══ Team Cymru Malware Hash Registry ══════════════════════════════════════════
# Answer for a listed hash: TXT "<last-seen-epoch> <detection-percent>"; unlisted = NXDOMAIN.

_NOT_LISTED = 0


def _parse_mhr(txt: str) -> Optional[int]:
    parts = txt.strip().strip('"').split()
    if len(parts) == 2 and parts[1].isdigit():
        return max(1, int(parts[1]))
    return None


if IS_WINDOWS:
    class _DnsTxtData(ctypes.Structure):
        _fields_ = [('dwStringCount', wintypes.DWORD),
                    ('pStringArray', ctypes.c_wchar_p * 1)]

    class _DnsRecord(ctypes.Structure):
        _fields_ = [('pNext', ctypes.c_void_p),
                    ('pName', ctypes.c_wchar_p),
                    ('wType', wintypes.WORD),
                    ('wDataLength', wintypes.WORD),
                    ('Flags', wintypes.DWORD),
                    ('dwTtl', wintypes.DWORD),
                    ('dwReserved', wintypes.DWORD),
                    ('Data', _DnsTxtData)]


class _SystemResolver:
    """TXT lookups through the OS resolver (Windows DnsQuery_W)."""

    _TYPE_TXT, _NAME_ERROR, _NO_RECORDS = 16, 9003, 9501

    def __init__(self):
        self._dns = ctypes.WinDLL('dnsapi.dll')
        self._dns.DnsQuery_W.argtypes = [
            wintypes.LPCWSTR, wintypes.WORD, wintypes.DWORD, ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(_DnsRecord)), ctypes.c_void_p]
        self._dns.DnsQuery_W.restype = wintypes.LONG
        self._dns.DnsRecordListFree.argtypes = [ctypes.c_void_p, ctypes.c_int]

    def txt(self, name: str) -> Optional[int]:
        """Return MHR verdict, _NOT_LISTED, or raise OSError when the lookup failed."""
        rec = ctypes.POINTER(_DnsRecord)()
        status = self._dns.DnsQuery_W(name, self._TYPE_TXT, 0, None, ctypes.byref(rec), None)
        if status in (self._NAME_ERROR, self._NO_RECORDS):
            return _NOT_LISTED
        if status != 0:
            raise OSError(f'DNS error {status}')
        try:
            node = rec
            while node:
                r = node.contents
                if r.wType == self._TYPE_TXT and r.Data.dwStringCount:
                    pct = _parse_mhr(r.Data.pStringArray[0] or '')
                    if pct is not None:
                        return pct
                node = ctypes.cast(r.pNext, ctypes.POINTER(_DnsRecord)) if r.pNext else None
            return _NOT_LISTED
        finally:
            self._dns.DnsRecordListFree(rec, 1)


class _DohResolver:
    """TXT lookups over DNS-over-HTTPS (works on every OS, needs only HTTPS)."""

    URL = 'https://cloudflare-dns.com/dns-query?type=TXT&name='

    def txt(self, name: str) -> Optional[int]:
        req = urllib.request.Request(self.URL + name,
                                     headers={'Accept': 'application/dns-json',
                                              'User-Agent': 'ST-AntiVirus'})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
        if data.get('Status') == 3:          # NXDOMAIN
            return _NOT_LISTED
        if data.get('Status') != 0:
            raise OSError(f'DoH status {data.get("Status")}')
        for ans in data.get('Answer', []):
            pct = _parse_mhr(ans.get('data', ''))
            if pct is not None:
                return pct
        return _NOT_LISTED


class HashCloud:
    """Look up SHA-1 hashes in the Team Cymru Malware Hash Registry."""

    ZONE = 'hash.cymru.com'

    def __init__(self):
        self.available = True        # flips to False after repeated network failures
        self._failures = 0
        self._lock = threading.Lock()
        self._resolvers = []
        if IS_WINDOWS:
            try:
                self._resolvers.append(_SystemResolver())
            except (OSError, AttributeError):
                pass
        self._resolvers.append(_DohResolver())

    def lookup(self, sha1: str) -> Optional[int]:
        """Return detection % (1-100) if known malware, 0 if not listed, None if unavailable."""
        if not self.available:
            return None
        for resolver in self._resolvers:
            try:
                pct = resolver.txt(f'{sha1}.{self.ZONE}')
            except Exception:
                continue
            with self._lock:
                self._failures = 0
            return pct
        with self._lock:
            self._failures += 1
            if self._failures >= 8:          # offline / DNS blocked: stop trying this scan
                self.available = False
        return None


# ══ AMSI ══════════════════════════════════════════════════════════════════════

_AMSI_RESULT_DETECTED = 32768


class Amsi:
    """Scan buffers with whatever AV engine is registered as an AMSI provider."""

    def __init__(self):
        self.available = False
        self._ctx = ctypes.c_void_p()
        self._lock = threading.Lock()
        if not IS_WINDOWS:
            return
        try:
            self._dll = ctypes.WinDLL('amsi.dll')
            self._dll.AmsiInitialize.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
            self._dll.AmsiScanBuffer.argtypes = [
                ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG, wintypes.LPCWSTR,
                ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
            self._dll.AmsiUninitialize.argtypes = [ctypes.c_void_p]
            self.available = self._dll.AmsiInitialize('ST-AntiVirus', ctypes.byref(self._ctx)) == 0
        except (OSError, AttributeError):
            self.available = False

    def detected(self, data: bytes, name: str) -> bool:
        if not self.available or not data:
            return False
        result = ctypes.c_int(0)
        buf = ctypes.create_string_buffer(data, len(data))
        hr = self._dll.AmsiScanBuffer(self._ctx, buf, len(data), name, None, ctypes.byref(result))
        return hr == 0 and result.value >= _AMSI_RESULT_DETECTED

    def close(self):
        if self.available:
            try:
                self._dll.AmsiUninitialize(self._ctx)
            except OSError:
                pass
            self.available = False


# ══ Authenticode signature check ══════════════════════════════════════════════

_wintrust = None
if IS_WINDOWS:
    class _GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                    ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]


    class _WinTrustFileInfo(ctypes.Structure):
        _fields_ = [('cbStruct', wintypes.DWORD), ('pcwszFilePath', wintypes.LPCWSTR),
                    ('hFile', wintypes.HANDLE), ('pgKnownSubject', ctypes.c_void_p)]


    class _WinTrustData(ctypes.Structure):
        _fields_ = [('cbStruct', wintypes.DWORD), ('pPolicyCallbackData', ctypes.c_void_p),
                    ('pSIPClientData', ctypes.c_void_p), ('dwUIChoice', wintypes.DWORD),
                    ('fdwRevocationChecks', wintypes.DWORD), ('dwUnionChoice', wintypes.DWORD),
                    ('pFile', ctypes.POINTER(_WinTrustFileInfo)), ('dwStateAction', wintypes.DWORD),
                    ('hWVTStateData', wintypes.HANDLE), ('pwszURLReference', wintypes.LPCWSTR),
                    ('dwProvFlags', wintypes.DWORD), ('dwUIContext', wintypes.DWORD),
                    ('pSignatureSettings', ctypes.c_void_p)]


    _WINTRUST_ACTION_GENERIC_VERIFY_V2 = _GUID(
        0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))

    try:
        _wintrust = ctypes.WinDLL('wintrust.dll')
        _wintrust.WinVerifyTrust.argtypes = [wintypes.HANDLE, ctypes.POINTER(_GUID), ctypes.c_void_p]
        _wintrust.WinVerifyTrust.restype = wintypes.LONG
    except OSError:
        _wintrust = None


def is_signed(path: str) -> bool:
    """True if the file carries a valid embedded Authenticode signature (offline check)."""
    if _wintrust is None:
        return False
    fi = _WinTrustFileInfo(ctypes.sizeof(_WinTrustFileInfo), path, None, None)
    wd = _WinTrustData()
    wd.cbStruct = ctypes.sizeof(_WinTrustData)
    wd.dwUIChoice = 2                 # WTD_UI_NONE
    wd.fdwRevocationChecks = 0        # WTD_REVOKE_NONE
    wd.dwUnionChoice = 1              # WTD_CHOICE_FILE
    wd.pFile = ctypes.pointer(fi)
    wd.dwStateAction = 1              # WTD_STATEACTION_VERIFY
    wd.dwProvFlags = 0x1000           # WTD_CACHE_ONLY_URL_RETRIEVAL (no network)
    guid = _WINTRUST_ACTION_GENERIC_VERIFY_V2
    try:
        res = _wintrust.WinVerifyTrust(wintypes.HANDLE(-1), ctypes.byref(guid), ctypes.byref(wd))
    finally:
        wd.dwStateAction = 2          # WTD_STATEACTION_CLOSE
        _wintrust.WinVerifyTrust(wintypes.HANDLE(-1), ctypes.byref(guid), ctypes.byref(wd))
    return res == 0


# ══ scan cache ════════════════════════════════════════════════════════════════

class ScanCache:
    """Remembers files already found clean (by path + size + mtime) and hash verdicts.

    Clean verdicts expire after CLEAN_TTL so newly-discovered malware is still caught.
    Must only be used from the thread that created it.
    """

    CLEAN_TTL = 7 * 24 * 3600

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(DATA_DIR, 'av_cache.sqlite')
        self._db = None
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._db = sqlite3.connect(self.path)
            self._db.execute('CREATE TABLE IF NOT EXISTS files ('
                             'path TEXT PRIMARY KEY, size INT, mtime INT, checked INT)')
            self._db.execute('CREATE TABLE IF NOT EXISTS hashes ('
                             'sha1 TEXT PRIMARY KEY, pct INT, checked INT)')
        except sqlite3.Error:
            self._db = None

    def is_clean(self, path: str, size: int, mtime: int) -> bool:
        if self._db is None:
            return False
        row = self._db.execute('SELECT size, mtime, checked FROM files WHERE path=?',
                               (path,)).fetchone()
        return bool(row and row[0] == size and row[1] == mtime
                    and time.time() - row[2] < self.CLEAN_TTL)

    def mark_clean(self, path: str, size: int, mtime: int) -> None:
        if self._db is not None:
            self._db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?)',
                             (path, size, mtime, int(time.time())))

    def forget(self, path: str) -> None:
        if self._db is not None:
            self._db.execute('DELETE FROM files WHERE path=?', (path,))

    def hash_verdict(self, sha1: str) -> Optional[int]:
        if self._db is None:
            return None
        row = self._db.execute('SELECT pct, checked FROM hashes WHERE sha1=?', (sha1,)).fetchone()
        if not row:
            return None
        pct, checked = row
        if pct == 0 and time.time() - checked > self.CLEAN_TTL:
            return None              # stale "clean" answer: ask again
        return pct

    def load_hashes(self) -> dict:
        """All still-valid hash verdicts: {sha1: pct}. Stale clean answers are left out."""
        if self._db is None:
            return {}
        cutoff = int(time.time()) - self.CLEAN_TTL
        return dict(self._db.execute(
            'SELECT sha1, pct FROM hashes WHERE pct > 0 OR checked >= ?', (cutoff,)))

    def store_hash(self, sha1: str, pct: int) -> None:
        if self._db is not None:
            self._db.execute('INSERT OR REPLACE INTO hashes VALUES (?,?,?)',
                             (sha1, pct, int(time.time())))

    def commit(self) -> None:
        if self._db is not None:
            try:
                self._db.commit()
            except sqlite3.Error:
                pass

    def close(self) -> None:
        if self._db is not None:
            self.commit()
            self._db.close()
            self._db = None


# ══ antivirus product status (Windows Security Center) ════════════════════════

def security_center_products() -> list[Tuple[str, bool, bool]]:
    """[(name, realtime_on, definitions_up_to_date)] for registered AV products (Windows).
    Cached for a minute so the scanner and its checks share one query."""
    if not IS_WINDOWS:
        return []
    cached = _sc_cache.get('value')
    if cached is not None and time.time() - _sc_cache['at'] < 60:
        return cached
    _sc_cache['value'] = _query_security_center()
    _sc_cache['at'] = time.time()
    return _sc_cache['value']


_sc_cache: dict = {}


def _query_security_center() -> list:
    import subprocess
    try:
        out = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             'Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | '
             'ForEach-Object { "$($_.displayName)|$($_.productState)" }'],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    products = []
    for line in out.splitlines():
        name, _, state = line.strip().rpartition('|')
        if name and state.isdigit():
            s = int(state)
            products.append((name, (s >> 12) & 0xF == 1, (s >> 4) & 0xF == 0))
    return products
