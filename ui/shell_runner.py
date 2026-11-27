"""Real shell commands for the ST terminal (ls, cd, grep, git, brew, …).

Every command runs in the user's login shell — zsh/bash on macOS and Linux, PowerShell on
Windows — so PATH, Homebrew and the user's .zprofile work as in Terminal. The working folder
carries over between commands (cd works). Output streams in as it arrives; Ctrl+C stops the
command; while one runs, what the user types goes to its input (answers to y/n questions).

Full-screen programs (vim, top, less, ssh, …) need a real terminal, so they open in Terminal
(macOS), a new console (Windows) or the system terminal (Linux) instead.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import stat
import sys
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from core.osinfo import IS_MAC, IS_WINDOWS, NO_WINDOW

_MARK = '\x1eST_DONE:'
_ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[@-Z\\-_]')

# Programs that draw the whole screen or need a real keyboard / TTY
FULLSCREEN = {
    'vim', 'vi', 'nvim', 'nano', 'pico', 'emacs', 'micro', 'top', 'htop', 'btop', 'less',
    'more', 'man', 'ssh', 'telnet', 'tmux', 'screen', 'watch', 'mc', 'ncdu', 'irb', 'ftp',
}
# Language shells: full-screen only when started without a script
REPLS = {'python', 'python3', 'node', 'ruby', 'bash', 'zsh', 'sh', 'fish', 'lua', 'php',
         'sqlite3', 'mysql', 'psql', 'pwsh', 'powershell'}


def needs_real_terminal(command: str) -> bool:
    try:
        words = shlex.split(command, posix=not IS_WINDOWS)
    except ValueError:
        return False
    while words and ('=' in words[0] and not words[0].startswith('=')):   # FOO=1 cmd
        words = words[1:]
    if words and words[0] in ('sudo', 'exec', 'time', 'nohup'):
        words = words[1:]
    if not words:
        return False
    prog = os.path.basename(words[0]).lower()
    if IS_WINDOWS and prog.endswith('.exe'):
        prog = prog[:-4]
    if prog in FULLSCREEN:
        return True
    return prog in REPLS and len(words) == 1


def _askpass_script() -> str:
    """macOS: a tiny helper that sudo runs to ask for the password in a dialog."""
    path = os.path.join(tempfile.gettempdir(), f'st-askpass-{os.getuid()}.sh')
    body = ('#!/bin/sh\n'
            '/usr/bin/osascript -e \'text returned of (display dialog '
            '"ST-SoftwareTool terminal: sudo needs your password to continue." '
            'default answer "" with hidden answer with title "Administrator password" '
            'with icon caution)\' 2>/dev/null\n')
    try:
        with open(path, 'w') as f:
            f.write(body)
        os.chmod(path, stat.S_IRWXU)
    except OSError:
        pass
    return path


class ShellRunner(QObject):
    """Runs one shell command at a time."""
    output   = Signal(str, bool)   # text (one or more lines), is_error
    finished = Signal(int)         # exit code (-1 = stopped / failed to start)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cwd = os.path.expanduser('~')
        self._proc: QProcess | None = None
        self._buf = {'out': '', 'err': ''}
        self._code = None

    # ── state ─────────────────────────────────────────────────────────────────

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def shell_name(self) -> str:
        if IS_WINDOWS:
            return 'PowerShell'
        return os.path.basename(self._unix_shell())

    def display_cwd(self) -> str:
        home = os.path.expanduser('~')
        if self.cwd == home:
            return '~'
        if self.cwd.startswith(home + os.sep):
            return '~' + self.cwd[len(home):]
        return self.cwd

    @staticmethod
    def _unix_shell() -> str:
        sh = os.environ.get('SHELL', '')
        if sh and os.access(sh, os.X_OK) and os.path.basename(sh) in ('zsh', 'bash', 'sh', 'ksh'):
            return sh
        return '/bin/zsh' if IS_MAC else '/bin/bash'

    # ── environment ───────────────────────────────────────────────────────────

    def _environment(self) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()
        # The packaged app's own runtime settings must not leak into user commands
        for var in ('PYTHONHOME', 'PYTHONPATH', 'QT_PLUGIN_PATH', 'QML2_IMPORT_PATH',
                    'DYLD_LIBRARY_PATH', 'DYLD_FRAMEWORK_PATH', 'NUITKA_ONEFILE_PARENT'):
            env.remove(var)
        # A text view, not a terminal: plain output, no pagers waiting for a key
        for k, v in (('TERM', 'dumb'), ('NO_COLOR', '1'), ('CLICOLOR', '0'),
                     ('PAGER', 'cat'), ('GIT_PAGER', 'cat'), ('MANPAGER', 'cat'),
                     ('HOMEBREW_NO_EMOJI', '1'), ('HOMEBREW_NO_ENV_HINTS', '1'),
                     ('HOMEBREW_COLOR', ''), ('PYTHONUNBUFFERED', '1')):
            env.insert(k, v)
        if not IS_WINDOWS:
            from core import homebrew
            path = env.value('PATH', '/usr/bin:/bin:/usr/sbin:/sbin').split(':')
            for d in reversed(homebrew.path_dirs() + ['/usr/local/bin']):
                if d not in path:
                    path.insert(0, d)
            env.insert('PATH', ':'.join(path))
        if IS_MAC:
            env.insert('SUDO_ASKPASS', _askpass_script())
        return env

    # ── running ───────────────────────────────────────────────────────────────

    def run(self, command: str) -> bool:
        """Start a command. False if one is already running."""
        if self.is_running():
            return False
        self._buf = {'out': '', 'err': ''}
        self._code = None
        p = QProcess(self)
        p.setProcessEnvironment(self._environment())
        p.setWorkingDirectory(self.cwd if os.path.isdir(self.cwd) else os.path.expanduser('~'))
        if IS_WINDOWS:
            # Qt starts it without a console window, since this GUI app has no console
            prog, args = self._windows_command(command)
        else:
            prog, args = self._unix_shell(), ['-l', '-c', self._unix_script(command)]
        p.readyReadStandardOutput.connect(lambda: self._read('out'))
        p.readyReadStandardError.connect(lambda: self._read('err'))
        p.finished.connect(self._done)
        p.errorOccurred.connect(self._error)
        self._proc = p
        p.start(prog, args)
        return True

    def _unix_script(self, command: str) -> str:
        # sudo asks for the password through SUDO_ASKPASS (a dialog), not the missing TTY
        cmd = re.sub(r'(^|[;&|(]\s*)sudo(?=\s)(?!\s+-A)', r'\1sudo -A', command) if IS_MAC else command
        return (f'{cmd}\n__st_rc=$?\n'
                f'printf "{_MARK}%s:%s\\n" "$__st_rc" "$PWD"\n')

    @staticmethod
    def _windows_command(command: str):
        script = (
            '[Console]::OutputEncoding = [Text.Encoding]::UTF8\n'
            '$ProgressPreference = "SilentlyContinue"\n'
            f'{command}\n'
            '$__st_rc = if ($?) { if ($LASTEXITCODE) { $LASTEXITCODE } else { 0 } } '
            'else { if ($LASTEXITCODE) { $LASTEXITCODE } else { 1 } }\n'
            f'Write-Output ([char]30 + "ST_DONE:" + $__st_rc + ":" + (Get-Location).ProviderPath)\n')
        import base64
        encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
        return 'powershell.exe', ['-NoLogo', '-NoProfile', '-NonInteractive',
                                  '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded]

    def send_input(self, text: str) -> None:
        if self.is_running():
            self._proc.write((text + '\n').encode('utf-8'))

    def stop(self) -> None:
        """Ctrl+C: interrupt the command and everything it started."""
        if not self.is_running():
            return
        pid = int(self._proc.processId())
        try:
            import psutil
            shell = psutil.Process(pid)
            procs = shell.children(recursive=True) + [shell]
        except Exception:
            self._proc.kill()
            return
        for proc in procs:                       # the command first, then its shell
            try:
                if IS_WINDOWS:
                    proc.kill()
                else:
                    proc.send_signal(signal.SIGINT)
            except Exception:
                pass

    # ── output ────────────────────────────────────────────────────────────────

    def _read(self, which: str) -> None:
        p = self._proc
        if p is None:
            return
        raw = bytes(p.readAllStandardOutput() if which == 'out' else p.readAllStandardError())
        text = raw.decode('utf-8', errors='replace').replace('\r\n', '\n')
        self._buf[which] += text
        lines = self._buf[which].split('\n')
        self._buf[which] = lines.pop()          # keep an unfinished line for later
        self._emit(lines, which == 'err')

    def _emit(self, lines, is_err: bool) -> None:
        shown = []
        for line in lines:
            if _MARK in line:                     # may follow output without a newline
                line, mark = line.split(_MARK, 1)
                self._parse_mark(_MARK + mark)
                if not line:
                    continue
            line = _ANSI.sub('', line.rsplit('\r', 1)[-1])     # progress bars: last state
            shown.append(line)
        if shown:
            self.output.emit('\n'.join(shown), is_err)

    def _parse_mark(self, line: str) -> None:
        body = line[line.index(_MARK) + len(_MARK):].strip()
        code, _, cwd = body.partition(':')
        try:
            self._code = int(code)
        except ValueError:
            self._code = None
        if cwd and os.path.isdir(cwd):
            self.cwd = cwd

    def _done(self, exit_code: int, _status=None) -> None:
        for which in ('out', 'err'):
            rest = self._buf[which]
            self._buf[which] = ''
            if rest:
                self._emit([rest], which == 'err')
        code = self._code if self._code is not None else int(exit_code)
        self._proc = None
        self.finished.emit(code)

    def _error(self, err) -> None:
        if err == QProcess.FailedToStart:
            self.output.emit(f'Could not start {self.shell_name()}.', True)
            self._proc = None
            self.finished.emit(-1)

    # ── full-screen programs ──────────────────────────────────────────────────

    def open_in_terminal(self, command: str) -> str:
        """Run a command in a real terminal window. Returns a message for the user."""
        import subprocess
        cwd = self.cwd
        if IS_MAC:
            line = f'cd {shlex.quote(cwd)} && {command}'
            line = line.replace('\\', '\\\\').replace('"', '\\"')
            subprocess.Popen(['/usr/bin/osascript', '-e',
                              f'tell application "Terminal"\n activate\n do script "{line}"\nend tell'])
            return 'Opened in Terminal.'
        if IS_WINDOWS:
            subprocess.Popen(['powershell.exe', '-NoExit', '-Command', command], cwd=cwd,
                             creationflags=getattr(subprocess, 'CREATE_NEW_CONSOLE', 0))
            return 'Opened in a new PowerShell window.'
        for term in ('x-terminal-emulator', 'gnome-terminal', 'konsole', 'xterm'):
            import shutil
            if shutil.which(term):
                subprocess.Popen([term, '-e', 'sh', '-c', f'{command}; exec $SHELL'], cwd=cwd)
                return f'Opened in {term}.'
        return 'No terminal app found to open it in.'
