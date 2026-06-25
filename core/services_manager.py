"""Windows Services Manager backend."""
from __future__ import annotations

import json
import subprocess
from typing import List, Tuple

_PS_QUERY = (
    "Get-CimInstance Win32_Service | "
    "Select-Object Name,DisplayName,State,StartMode,Description,ProcessId,PathName,StartName | "
    "ConvertTo-Json -Compress -Depth 1"
)


class WindowsService:
    __slots__ = ('name', 'display_name', 'state', 'start_mode',
                 'description', 'pid', 'path', 'account')

    def __init__(self, name: str, display_name: str, state: str,
                 start_mode: str, description: str, pid: int,
                 path: str, account: str):
        self.name         = name
        self.display_name = display_name or name
        self.state        = state or 'Unknown'
        self.start_mode   = start_mode or 'Unknown'
        self.description  = description or ''
        self.pid          = pid or 0
        self.path         = path or ''
        self.account      = account or ''

    @property
    def is_running(self) -> bool:
        return self.state == 'Running'

    def detail_text(self) -> str:
        pid_s = str(self.pid) if self.pid else '(not running)'
        return (
            f"  Display Name  {self.display_name}\n"
            f"  Service Name  {self.name}\n"
            f"  State         {self.state}\n"
            f"  Start Mode    {self.start_mode}\n"
            f"  PID           {pid_s}\n"
            f"  Account       {self.account or '(unknown)'}\n"
            f"  Path          {self.path or '(unknown)'}\n"
            f"\n"
            f"  Description:\n"
            f"  {self.description or '(none)'}"
        )


class ServicesManager:
    def get_all(self) -> List[WindowsService]:
        try:
            proc = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', _PS_QUERY],
                capture_output=True, text=True, timeout=60,
            )
            raw = proc.stdout.strip()
            if not raw:
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
            out = []
            for s in data:
                n = (s.get('Name') or '').strip()
                if not n:
                    continue
                out.append(WindowsService(
                    name=n,
                    display_name=(s.get('DisplayName') or n).strip(),
                    state=(s.get('State') or 'Unknown').strip(),
                    start_mode=(s.get('StartMode') or 'Unknown').strip(),
                    description=(s.get('Description') or '').strip(),
                    pid=int(s.get('ProcessId') or 0),
                    path=(s.get('PathName') or '').strip().strip('"'),
                    account=(s.get('StartName') or '').strip(),
                ))
            return sorted(out, key=lambda s: s.display_name.lower())
        except Exception:
            return []

    # ── control ───────────────────────────────────────────────────────────────

    @staticmethod
    def _sc(*args: str) -> Tuple[bool, str]:
        try:
            p = subprocess.run(
                ['sc'] + list(args),
                capture_output=True, text=True, timeout=30,
            )
            msg = (p.stderr or p.stdout).strip()
            return p.returncode == 0, msg
        except Exception as e:
            return False, str(e)

    def start(self, name: str) -> Tuple[bool, str]:
        return self._sc('start', name)

    def stop(self, name: str) -> Tuple[bool, str]:
        return self._sc('stop', name)

    def restart(self, name: str) -> Tuple[bool, str]:
        ok, msg = self.stop(name)
        if not ok:
            return False, f"Stop failed: {msg}"
        import time
        time.sleep(1)
        return self.start(name)

    def set_startup(self, name: str, mode: str) -> Tuple[bool, str]:
        return self._sc('config', name, f'start= {mode}')
