"""ST-AntiVirus scanner.

Every scan:
  1. runs the operating-system checks (protection status, processes, startup entries,
     hosts file, scheduled tasks, services, ...) — see core.av_system;
  2. analyses the files behind every running process and autostart entry;
  3. walks the scan locations recursively (Quick = high-risk folders, Full = every file
     on every fixed drive) and inspects file *contents*:
       - detection engines: local signatures, Team Cymru malware hash registry,
         AMSI (installed AV engine, Windows) and files the installed AV refuses to open;
       - heuristics: disguised executables, fake system names, injection / keylogger /
         credential-theft APIs, packers, suspicious script commands.
     Heuristic warnings are dropped for files with a valid digital signature.
Unchanged files that were clean are cached and skipped for 7 days.
"""
from __future__ import annotations

import concurrent.futures as cf
import ctypes
import hashlib
import math
import os
import queue
import stat
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

from core.av_engine import (CLOUD_CONFIRMED_PCT, IS_WINDOWS, LOCAL_SIGS, Amsi, HashCloud,
                            ScanCache, exec_kind, is_signed, security_center_products)
from core.av_system import (FAKE_SYSNAMES, QUARANTINE_DIR, block_threat, system_checks,
                            temp_dirs)
from core.av_types import ScanStats, ThreatEntry, file_threat, norm

__all__ = ['AntivirusManager', 'ThreatEntry', 'ScanStats', 'QUARANTINE_DIR']

_HASH_LIMIT      = 64 * 1024 * 1024    # files above this are not hashed
_HEURISTIC_BYTES = 8 * 1024 * 1024     # bytes fed to heuristics / AMSI
_WORKERS         = 24                  # parallel file analysers (I/O + network bound)

# Windows "cloud placeholder" attributes: opening such a file would download it
_CLOUD_ONLY = 0x00001000 | 0x00040000 | 0x00400000

_ERROR_VIRUS_INFECTED = 225
_ERROR_VIRUS_DELETED  = 226


# ── heuristic data ────────────────────────────────────────────────────────────

_INJECT_APIS = [
    b'CreateRemoteThread', b'WriteProcessMemory',
    b'VirtualAllocEx',     b'NtCreateThreadEx',
]
_KEYLOG_APIS = [
    b'GetAsyncKeyState',  b'SetWindowsHookExA', b'SetWindowsHookExW',
]
_CRED_APIS = [
    b'MiniDumpWriteDump', b'LsaEnumerateLogonSessions', b'SamConnect',
]
_SCRIPT_KW = [
    b'powershell -enc', b'powershell -e ',   b'-encodedcommand',
    b'invoke-expression', b'iex(',            b'iex (',
    b'downloadstring',  b'net user /add',    b'net localgroup administrators',
    b'mimikatz',        b'meterpreter',      b'empire',
    b'reg add hklm',    b'reg add hkcu',     b'schtasks /create',
]

_WIN_EXEC_EXTS = {'.exe', '.scr', '.dll', '.sys', '.com', '.cpl', '.ocx', '.pif', '.drv', '.msi'}
_SCRIPT_EXTS   = {'.bat', '.cmd', '.ps1', '.psm1', '.vbs', '.vbe', '.js', '.jse', '.wsf',
                  '.wsh', '.hta', '.sh', '.command', '.applescript'}
_OTHER_RISKY   = {'.jar', '.lnk', '.docm', '.xlsm', '.pptm', '.doc', '.xls', '.ppt',
                  '.zip', '.rar', '.7z', '.iso', '.img', '.dmg', '.pkg', '.deb', '.rpm',
                  '.apk', '.appimage'}
_RISKY_EXTS    = _WIN_EXEC_EXTS | _SCRIPT_EXTS | _OTHER_RISKY
# Types people launch by double-clicking (libraries like System.Xml.dll are normal)
_DISGUISE_EXTS = {'.exe', '.scr', '.com', '.pif', '.msi'} | _SCRIPT_EXTS

# Only these "fake" extensions indicate a real disguise (e.g. invoice.pdf.exe).
# Version numbers like "3.12.2" or "64-bit" are NOT document extensions.
_DOC_EXTS = {
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx',
    'jpg', 'jpeg', 'png', 'gif', 'bmp', 'tiff', 'ico', 'svg',
    'txt', 'rtf', 'csv', 'xml', 'html', 'htm', 'json',
    'zip', 'rar', '7z', 'iso', 'tar', 'gz',
    'mp4', 'mp3', 'avi', 'mkv', 'wav', 'mov', 'flac',
}

_SAFE_APPDATA = {
    'microsoft', 'discord',   'slack',   'spotify',  'zoom',     'signal',
    'telegram',  'whatsapp',  'teams',   'chrome',   'firefox',  'edge',
    'opera',     'brave',     'vivaldi', 'onedrive', 'dropbox',  'google',
    'nvidia',    'amd',       'intel',   'steam',    'epic',     'ubisoft',
    'battle.net','blizzard',  'riot',    'origin',   'obs',
    'gimp',      'vlc',       'mpv',     '7-zip',    'winrar',   'notepad',
    'vscode',    'code',      'python',  'node',     'npm',      'git',
    'java',      'jetbrains', 'rider',   'pycharm',  'webstorm',
}


def _entropy(data: bytes) -> float:
    if not data:
        return 0.0
    freq = [0] * 256
    for b in data:
        freq[b] += 1
    n = len(data)
    return -sum((f / n) * math.log2(f / n) for f in freq if f > 0)


def _code_entropy(data: bytes) -> Optional[float]:
    """Entropy of a PE file's executable sections (ignores resources and appended
    installer payloads, which are compressed in perfectly normal programs)."""
    try:
        pe = int.from_bytes(data[0x3C:0x40], 'little')
        if data[pe:pe + 4] != b'PE\0\0':
            return None
        n_sections = int.from_bytes(data[pe + 6:pe + 8], 'little')
        opt_size = int.from_bytes(data[pe + 20:pe + 22], 'little')
        table = pe + 24 + opt_size
        code = bytearray()
        for i in range(min(n_sections, 96)):
            sec = data[table + 40 * i: table + 40 * (i + 1)]
            if len(sec) < 40:
                break
            raw_size = int.from_bytes(sec[16:20], 'little')
            raw_ptr = int.from_bytes(sec[20:24], 'little')
            flags = int.from_bytes(sec[36:40], 'little')
            if flags & 0x20000000 and raw_size:          # IMAGE_SCN_MEM_EXECUTE
                code += data[raw_ptr:raw_ptr + min(raw_size, 2 * 1024 * 1024)]
        return _entropy(bytes(code)) if len(code) >= 1024 else None
    except (ValueError, IndexError):
        return None


# ── scan locations per OS ─────────────────────────────────────────────────────

def _home(*parts: str) -> str:
    return os.path.join(os.path.expanduser('~'), *parts)


def _quick_locations() -> List[str]:
    if IS_WINDOWS:
        env = os.path.expandvars
        locs = [*temp_dirs(), _home('Downloads'), _home('Desktop'), _home('Documents'),
                env(r'%PUBLIC%\Desktop'), env(r'%PUBLIC%\Downloads'),
                env(r'%PROGRAMDATA%\Microsoft\Windows\Start Menu\Programs\Startup'),
                env('%APPDATA%'), env('%LOCALAPPDATA%'), env('%PROGRAMDATA%')]
    elif sys.platform == 'darwin':
        locs = [*temp_dirs(), _home('Downloads'), _home('Desktop'), _home('Documents'),
                _home('Library', 'LaunchAgents'), _home('Library', 'Application Support'),
                '/Library/LaunchAgents', '/Library/LaunchDaemons', '/Users/Shared']
    else:
        locs = [*temp_dirs(), _home('Downloads'), _home('Desktop'), _home('Documents'),
                _home('.config', 'autostart'), _home('.local', 'bin'), _home('.local', 'share')]
    out, seen = [], set()
    for p in locs:
        n = norm(p)
        if n and '%' not in n and n not in seen and os.path.isdir(n):
            seen.add(n)
            out.append(p)
    return out


def _full_locations() -> List[str]:
    if not IS_WINDOWS:
        return ['/']
    drives: List[str] = []
    mask = ctypes.windll.kernel32.GetLogicalDrives()
    for i in range(26):
        if mask & (1 << i):
            dr = f'{chr(65 + i)}:\\'
            if ctypes.windll.kernel32.GetDriveTypeW(dr) == 3:   # DRIVE_FIXED
                drives.append(dr)
    return drives


# Never descended into: component stores, snapshots, virtual / device filesystems
_SKIP_DIRS_WIN = ('\\windows\\winsxs', '\\windows\\servicing',
                  '\\windows\\softwaredistribution', '\\system volume information')
_SKIP_ROOTS_POSIX = tuple(norm(p) for p in (
    '/proc', '/sys', '/dev', '/run', '/snap', '/System/Volumes', '/Volumes',
    '/private/var/vm', '/private/var/folders/zz'))


# ══ scanner ═══════════════════════════════════════════════════════════════════

class AntivirusManager:

    def __init__(self):
        self._stop = False
        self.stats = ScanStats()
        self._temps = temp_dirs()
        if IS_WINDOWS:
            self._appdata = tuple(norm(os.environ.get(v, '')) for v in ('APPDATA', 'LOCALAPPDATA')
                                  if os.environ.get(v))
            # Engines scan these, but heuristics are skipped: thousands of legitimate
            # system / installed binaries use "suspicious" APIs.
            self._no_heur = tuple(norm(os.environ[v]) for v in
                                  ('WINDIR', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMW6432')
                                  if os.environ.get(v))
        else:
            self._appdata = ()
            self._no_heur = tuple(norm(p) for p in ('/System', '/usr', '/bin', '/sbin', '/lib',
                                                    '/opt', '/Applications'))
        # Where people download / open files: names are not trusted, contents are sniffed
        self._user_visible = self._temps + tuple(norm(p) for p in (
            _home('Downloads'), _home('Desktop'), _home('Documents')))
        self._own_dirs = (norm(QUARANTINE_DIR),)

    def stop(self) -> None:
        self._stop = True

    # ── public scan API ───────────────────────────────────────────────────────

    def quick_scan(self, progress_cb=None, threat_cb=None) -> List[ThreatEntry]:
        """System checks + running/autostart programs + recursive scan of high-risk folders."""
        return self._run('quick', _quick_locations(), progress_cb, threat_cb)

    def full_scan(self, progress_cb=None, threat_cb=None) -> List[ThreatEntry]:
        """Quick-scan checks + the content of every file on every fixed drive."""
        return self._run('full', _full_locations(), progress_cb, threat_cb)

    def block_threat(self, threat: ThreatEntry) -> Tuple[bool, str]:
        return block_threat(threat)

    # ── orchestration ─────────────────────────────────────────────────────────

    def _run(self, mode: str, locations: List[str], progress_cb, threat_cb) -> List[ThreatEntry]:
        self._stop = False
        st = self.stats = ScanStats(mode=mode, started=time.time())
        threats: List[ThreatEntry] = []
        reported: set = set()

        def emit(t: ThreatEntry) -> None:
            key = (t.category, norm(t.file_path) or t.path)
            if key in reported:
                return
            reported.add(key)
            threats.append(t)
            st.threats = len(threats)
            if threat_cb:
                threat_cb(t)

        self._cloud = HashCloud()
        self._amsi = Amsi()
        st.amsi_ok = self._amsi.available
        cache = ScanCache()
        self._memo = cache.load_hashes()        # sha1 -> pct, shared by worker threads
        known_hashes = set(self._memo)
        self._memo_lock = threading.Lock()
        self._walked: set = set()
        self._last_progress = 0.0

        try:
            st.av_products = security_center_products()
            # 1. operating-system checks; they also collect programs to analyse
            programs: set = set()
            for msg, check in system_checks():
                if self._stop:
                    break
                self._progress(progress_cb, msg, force=True)
                try:
                    for t in check(programs):
                        emit(t)
                except Exception:
                    pass        # one broken check must not abort the whole scan

            with cf.ThreadPoolExecutor(max_workers=_WORKERS) as pool:
                pipe = _Pipeline(self, pool, cache, emit)

                # 2. running programs and autostart entries — always analysed deeply
                self._progress(progress_cb, f'Scanning {len(programs)} running and '
                                            f'startup programs…', force=True)
                for path in sorted(programs):
                    if self._stop:
                        break
                    pipe.submit_path(path)

                # 3. recursive walk of the scan locations
                for loc in locations:
                    if self._stop:
                        break
                    self._walk(loc, mode, pipe, progress_cb)

                pipe.finish(stop=self._stop)
        finally:
            st.cloud_ok = self._cloud.available
            st.stopped = self._stop
            self._amsi.close()
            with self._memo_lock:
                for sha1, pct in self._memo.items():
                    if sha1 not in known_hashes:
                        cache.store_hash(sha1, pct)
            cache.close()
            st.current = ''
            st.finished = time.time()
        return threats

    def _progress(self, cb, msg: str, force: bool = False) -> None:
        now = time.time()
        if cb and (force or now - self._last_progress > 0.25):
            self._last_progress = now
            cb(msg)

    def _walk(self, root: str, mode: str, pipe: '_Pipeline', progress_cb) -> None:
        st = self.stats
        stack = [root]
        while stack and not self._stop:
            d = stack.pop()
            dn = norm(d)
            if dn in self._walked or dn.startswith(self._own_dirs):
                continue
            if IS_WINDOWS:
                if any(s in dn for s in _SKIP_DIRS_WIN):
                    continue
            elif dn.startswith(_SKIP_ROOTS_POSIX):
                continue
            self._walked.add(dn)        # nested locations (e.g. LocalAppData\Temp) once only
            st.current = d
            self._progress(progress_cb, f'Scanning {d}')
            try:
                with os.scandir(d) as it:
                    entries = list(it)
            except OSError:
                continue
            user_visible = dn.startswith(self._user_visible)
            for e in entries:
                if self._stop:
                    return
                try:
                    if e.is_symlink() or (hasattr(e, 'is_junction') and e.is_junction()):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        stack.append(e.path)
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    s = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                st.files_seen += 1
                if getattr(s, 'st_file_attributes', 0) & _CLOUD_ONLY:
                    continue        # OneDrive-style placeholder: reading it would download it
                ext = os.path.splitext(e.name)[1].lower()
                if ext in _RISKY_EXTS or (not IS_WINDOWS and s.st_mode & 0o111):
                    deep = True
                elif mode == 'full' or (user_visible and (not ext or ext[1:] in _DOC_EXTS)):
                    # sniff the header: is this "document" secretly a program?
                    # (Full scan sniffs every file; quick scan only names people open)
                    deep = False
                else:
                    continue
                pipe.submit(e.path, e.name, ext, s.st_size, s.st_mtime_ns, deep)

    # ── per-file analysis (worker threads) ────────────────────────────────────

    def analyse_file(self, path: str, name: str, ext: str, size: int,
                     deep: bool) -> Tuple[List[ThreatEntry], bool]:
        """Return (threats, fully_checked). fully_checked=False: don't cache as clean."""
        out: List[ThreatEntry] = []
        name_l = name.lower()

        parts = name_l.split('.')
        # Scripts like jquery.svg.js are normal inside installed apps; the
        # "invoice.pdf.js" trick only matters where people open downloaded files.
        disguise_target = ext in _DISGUISE_EXTS and (
            ext not in _SCRIPT_EXTS or norm(path).startswith(self._user_visible))
        if len(parts) > 2 and disguise_target and parts[-2] in _DOC_EXTS:
            out.append(file_threat(
                'red', 'Double-Extension Disguise', path,
                f'Program disguised as .{parts[-2].upper()} file',
                'Double extensions (invoice.pdf.exe) trick people into running malware.'))
        if IS_WINDOWS and name_l in FAKE_SYSNAMES:
            out.append(file_threat(
                'red', 'Fake System Process Name', path,
                'File name mimics a core Windows system process',
                'Real Windows system processes live in System32. A look-alike elsewhere is malware.'))

        try:
            f = open(path, 'rb')
        except OSError as exc:
            if getattr(exc, 'winerror', 0) in (_ERROR_VIRUS_INFECTED, _ERROR_VIRUS_DELETED):
                out.append(file_threat(
                    'red', 'Blocked by Antivirus', path,
                    'Your installed antivirus refused to open this file because it is infected',
                    f'Windows reported: {exc.strerror}', confirmed=True))
                return out, True
            return out, False
        with f:
            try:
                head = f.read(4096)
            except OSError:
                return out, False
            kind = exec_kind(head)
            is_script = ext in _SCRIPT_EXTS

            if kind and ext.lstrip('.') in _DOC_EXTS:
                out.append(file_threat(
                    'red', 'Disguised Executable', path,
                    f'File named .{ext.lstrip(".").upper()} is actually a program',
                    'The file content is an executable. Malware hides programs behind '
                    'harmless-looking extensions so people open them.'))

            if not deep and not kind:
                return out, True            # ordinary data file

            # read once: hashes + leading bytes for heuristics / AMSI
            sha1 = md5 = None
            buf = head
            if size <= _HASH_LIMIT:
                h1, h5 = hashlib.sha1(head), hashlib.md5(head)
                chunks, kept = [head], len(head)
                try:
                    while chunk := f.read(1024 * 1024):
                        h1.update(chunk)
                        h5.update(chunk)
                        if kept < _HEURISTIC_BYTES:
                            chunks.append(chunk)
                            kept += len(chunk)
                except OSError:
                    return out, False
                sha1, md5 = h1.hexdigest(), h5.hexdigest()
                buf = b''.join(chunks)[:_HEURISTIC_BYTES]
            else:
                try:
                    buf = head + f.read(_HEURISTIC_BYTES - len(head))
                except OSError:
                    pass

        complete = True

        # ── detection engines ─────────────────────────────────────────────────
        if sha1:
            sig = LOCAL_SIGS.get(sha1) or LOCAL_SIGS.get(md5)
            if sig:
                out.append(file_threat(
                    'red', 'Known Malware (signature)', path, f'Matches signature: {sig}',
                    f'SHA-1 {sha1}\nMD5   {md5}', confirmed=True))
            else:
                pct = self._cloud_verdict(sha1)
                if pct is None:
                    complete = False        # offline: check again next scan
                elif pct >= CLOUD_CONFIRMED_PCT:
                    out.append(file_threat(
                        'red', 'Known Malware (hash registry)', path,
                        f'{pct}% of antivirus engines detect this file as malware',
                        f'Identified by the Team Cymru Malware Hash Registry.\nSHA-1 {sha1}',
                        confirmed=True))
                elif pct > 0:
                    out.append(file_threat(
                        'orange', 'Flagged by Some Engines', path,
                        f'{pct}% of antivirus engines detect this file',
                        'Only a few engines flag this file — it may be a false positive or a '
                        f'new threat. Review it before running it.\nSHA-1 {sha1}'))

        if (kind or is_script) and self._amsi.detected(buf, path):
            out.append(file_threat(
                'red', 'Malware (AMSI engine)', path,
                'The antivirus engine installed on this PC identified this file as malicious',
                'Detected through the Windows Antimalware Scan Interface (AMSI).',
                confirmed=True))

        # ── heuristics (not in system / program folders) ──────────────────────
        if not norm(path).startswith(self._no_heur):
            heur = self._heuristics(path, ext, kind, is_script, buf)
            if heur and kind == 'pe' and is_signed(path):
                heur = []                   # validly signed software: drop the guesses
            out.extend(heur)

        return out, complete

    # Suspicion score needed before an unsigned program is reported. Single traits are
    # common in legitimate software (games read keys, launchers use injection APIs,
    # installers live in Temp); malware typically combines several of them.
    _REPORT_SCORE = 4

    def _heuristics(self, path, ext, kind, is_script, buf) -> List[ThreatEntry]:
        out: List[ThreatEntry] = []
        if kind == 'pe' and ext in ('.exe', '.scr', '.com', '.pif'):
            out.extend(self._analyze_pe(path, buf))
        if is_script:
            content = buf[:65536].lower()
            hits = [kw.decode('utf-8', errors='replace') for kw in _SCRIPT_KW if kw in content]
            if len(hits) >= 2:
                out.append(file_threat(
                    'orange', 'Suspicious Script Content', path,
                    f'Script combines {len(hits)} risky commands: {", ".join(hits[:4])}',
                    'Malicious scripts chain commands like these to download and run code or '
                    'change system settings. Admin tools can too — review it before running.'))
        return out

    def _analyze_pe(self, path: str, data: bytes) -> List[ThreatEntry]:
        """Score an unsigned Windows program; report it only when several traits combine."""
        if len(data) < 64:
            return []
        pl = norm(path)
        inject = [a.decode() for a in _INJECT_APIS if a in data]
        keylog = [a.decode() for a in _KEYLOG_APIS if a in data]
        cred   = [a.decode() for a in _CRED_APIS if a in data]
        code_entropy = _code_entropy(data)

        # Credential dumping API pair is specific enough on its own
        if len(cred) >= 2:
            return [file_threat(
                'red', 'Credential Theft Tool', path, f'Credential APIs: {", ".join(cred)}',
                'This unsigned program can read passwords out of Windows memory, the way '
                'password-stealing tools do. Quarantine it unless you know exactly what it is.')]

        score, why = 0, []
        if len(inject) >= 2:
            score += 2; why.append(f'can inject code into other programs ({", ".join(inject)})')
        elif inject:
            score += 1; why.append(f'uses an injection API ({inject[0]})')
        if keylog:
            score += 1; why.append(f'can monitor the keyboard ({", ".join(keylog)})')
        if cred:
            score += 1; why.append(f'uses a credential API ({cred[0]})')
        if code_entropy is not None and code_entropy > 7.2:
            score += 2; why.append(f'its program code is packed/encrypted (entropy {code_entropy:.2f}/8)')
        if pl.startswith(self._temps):
            score += 1; why.append('it is located in a Temp folder')
        elif os.path.dirname(pl) in self._appdata:
            score += 1; why.append('it sits directly in the AppData root folder')

        if score < self._REPORT_SCORE:
            return []
        return [file_threat(
            'red' if score >= 6 else 'orange', 'Suspicious Program (heuristic)', path,
            f'Unsigned program with {len(why)} malware-like traits (score {score})',
            'Why:\n- ' + '\n- '.join(why) +
            '\n\nNo detection engine has identified this file as malware. It may be a '
            'legitimate unsigned tool — quarantine it if you do not recognise it.')]

    def _cloud_verdict(self, sha1: str) -> Optional[int]:
        with self._memo_lock:
            if sha1 in self._memo:
                return self._memo[sha1]
        pct = self._cloud.lookup(sha1)
        self.stats.cloud_lookups += 1
        if pct is not None:
            with self._memo_lock:
                self._memo[sha1] = pct
        return pct


class _Pipeline:
    """Feeds files to worker threads; results are handled on the scan thread only
    (the SQLite cache and the threat callback are not thread-safe)."""

    _MAX_IN_FLIGHT = 256

    def __init__(self, mgr: AntivirusManager, pool: cf.ThreadPoolExecutor,
                 cache: ScanCache, emit: Callable[[ThreatEntry], None]):
        self.mgr, self.pool, self.cache, self.emit = mgr, pool, cache, emit
        self.results: 'queue.Queue' = queue.Queue()
        self.in_flight = 0
        self.done: set = set()
        self._since_commit = 0

    def submit_path(self, path: str) -> None:
        try:
            s = os.stat(path)
        except OSError:
            return
        if not stat.S_ISREG(s.st_mode):
            return
        name = os.path.basename(path)
        self.mgr.stats.files_seen += 1
        self.submit(path, name, os.path.splitext(name)[1].lower(), s.st_size, s.st_mtime_ns, True)

    def submit(self, path, name, ext, size, mtime, deep) -> None:
        key = norm(path)
        if key in self.done:
            return
        self.done.add(key)
        if self.cache.is_clean(key, size, mtime):
            self.mgr.stats.files_cached += 1
            return
        while self.in_flight >= self._MAX_IN_FLIGHT and not self.mgr._stop:
            self._drain(block=True)
        if self.mgr._stop:
            return
        self.in_flight += 1
        fut = self.pool.submit(self.mgr.analyse_file, path, name, ext, size, deep)
        fut.add_done_callback(lambda f, k=key, s=size, m=mtime: self.results.put((k, s, m, f)))
        self._drain(block=False)

    def _drain(self, block: bool) -> None:
        while self.in_flight:
            try:
                key, size, mtime, fut = (self.results.get(timeout=0.2) if block
                                         else self.results.get_nowait())
            except queue.Empty:
                return
            block = False
            self.in_flight -= 1
            if fut.cancelled():
                continue
            self.mgr.stats.files_checked += 1
            try:
                threats, complete = fut.result()
            except Exception:
                continue
            for t in threats:
                self.emit(t)
            if not threats and complete:
                self.cache.mark_clean(key, size, mtime)
                self._since_commit += 1
                if self._since_commit >= 2000:
                    self.cache.commit()
                    self._since_commit = 0

    def finish(self, stop: bool) -> None:
        if stop:
            # cancelled futures still run their done-callback, so in_flight drains to 0
            self.pool.shutdown(wait=False, cancel_futures=True)
        while self.in_flight:
            self._drain(block=True)
        self.cache.commit()
