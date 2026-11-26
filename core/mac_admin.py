"""Run a command with administrator rights on macOS.

Uses the standard macOS password dialog (AppleScript "do shell script ... with administrator
privileges").  If passwordless sudo is already set up on the Mac, that is used instead and no
dialog appears.  Callers must build `command` only from validated values (numbers, labels
checked against a strict pattern, fixed paths): it is run by /bin/sh as root.
"""
from __future__ import annotations

import os
import subprocess

CANCELLED = 'cancelled'


def applescript_quote(text: str) -> str:
    """Text as an AppleScript string literal."""
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _passwordless_sudo() -> bool:
    try:
        return subprocess.run(['sudo', '-n', 'true'], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_as_admin(command: str, prompt: str, timeout: float = 180) -> tuple[bool, str]:
    """Run `command` (a /bin/sh command line) as root.

    Returns (True, '') on success, (False, CANCELLED) if the user cancelled the password
    dialog, otherwise (False, error message).
    """
    try:
        if os.geteuid() == 0:
            r = subprocess.run(['/bin/sh', '-c', command], capture_output=True, text=True,
                               timeout=timeout)
        elif _passwordless_sudo():
            r = subprocess.run(['sudo', '-n', '/bin/sh', '-c', command], capture_output=True,
                               text=True, timeout=timeout)
        else:
            script = (f'do shell script {applescript_quote(command)} '
                      f'with prompt {applescript_quote(prompt)} with administrator privileges')
            r = subprocess.run(['osascript', '-e', script], capture_output=True, text=True,
                               timeout=timeout)
            if r.returncode != 0 and '-128' in (r.stderr or ''):   # "User canceled."
                return False, CANCELLED
    except subprocess.TimeoutExpired:
        return False, 'Timed out waiting for the administrator password.'
    except OSError as exc:
        return False, str(exc)
    if r.returncode == 0:
        return True, ''
    return False, (r.stderr or r.stdout or f'exit code {r.returncode}').strip()
