"""Shared data types for ST-AntiVirus."""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field


@dataclass
class ThreatEntry:
    severity:    str         # 'red' | 'orange' | 'green'
    category:    str
    path:        str         # display path
    reason:      str
    detail:      str  = ''
    threat_type: str  = 'file'    # file | process | registry | task | service | info
    # per-type blocking info
    file_path:   str  = ''        # actual FS path for file/process threats
    pid:         int  = 0
    reg_hive:    int  = 0         # winreg HKEY constant
    reg_key:     str  = ''
    reg_value:   str  = ''
    task_name:   str  = ''
    svc_name:    str  = ''
    blocked:     bool = False
    confirmed:   bool = False     # True = identified by a detection engine, not a guess

    @property
    def severity_label(self) -> str:
        if self.blocked:
            return 'BLOCKED'
        return {'red': 'CRITICAL', 'orange': 'WARNING', 'green': 'CLEAN'}.get(
            self.severity, self.severity.upper()
        )

    @property
    def blockable(self) -> bool:
        return not self.blocked and self.threat_type not in ('info', 'green') \
            and self.severity != 'green'

    def block_label(self) -> str:
        labels = {
            'file':     'Quarantine File',
            'process':  'Kill Process',
            'registry': 'Remove Registry Entry',
            'task':     'Disable Task',
            'service':  'Stop & Disable Service',
        }
        return labels.get(self.threat_type, 'Block Threat')


def file_threat(sev: str, cat: str, path: str, reason: str, detail: str = '',
                confirmed: bool = False) -> ThreatEntry:
    return ThreatEntry(severity=sev, category=cat, path=path, file_path=path,
                       threat_type='file', reason=reason, detail=detail, confirmed=confirmed)


@dataclass
class ScanStats:
    """Live counters for the UI (read from the GUI thread, written by the scan thread)."""
    mode:          str   = ''
    files_seen:    int   = 0     # every file found on disk
    files_checked: int   = 0     # files whose contents were analysed this scan
    files_cached:  int   = 0     # unchanged + clean since last scan, skipped
    cloud_lookups: int   = 0
    threats:       int   = 0
    current:       str   = ''
    started:       float = 0.0
    finished:      float = 0.0
    stopped:       bool  = False
    cloud_ok:      bool  = True
    amsi_ok:       bool  = False
    av_products:   list  = field(default_factory=list)

    @property
    def elapsed(self) -> float:
        return (self.finished or time.time()) - self.started if self.started else 0.0


def norm(p: str) -> str:
    """Case/separator-normalised absolute path for comparisons."""
    return os.path.normcase(os.path.normpath(p)) if p else ''
