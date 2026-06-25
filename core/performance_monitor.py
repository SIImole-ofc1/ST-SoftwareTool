"""
Accurate hardware monitoring using psutil (CPU/RAM/Disk/Net) and
Windows WMI via PowerShell for GPU utilization.
"""
import time, os, shutil
import subprocess
from typing import List, Optional, Tuple, Dict


def _find_nvidia_smi() -> Optional[str]:
    for p in [
        r'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe',
        r'C:\Windows\System32\nvidia-smi.exe',
    ]:
        if os.path.exists(p):
            return p
    return shutil.which('nvidia-smi')

try:
    import psutil as _ps
    _PSUTIL = True
    _ps.cpu_percent(interval=None)          # warm-up so first call is non-zero
    _ps.cpu_percent(interval=None, percpu=True)
except ImportError:
    _PSUTIL = False


class PerformanceMonitor:
    @property
    def available(self) -> bool:
        return _PSUTIL

    # ── CPU ───────────────────────────────────────────────────────────────────

    def cpu_percent(self) -> float:
        """Overall CPU utilization (same source as Task Manager)."""
        return _ps.cpu_percent(interval=None) if _PSUTIL else 0.0

    def cpu_per_core(self) -> List[float]:
        return _ps.cpu_percent(interval=None, percpu=True) if _PSUTIL else []

    def cpu_freq(self) -> Tuple[float, float]:
        """(current_GHz, max_GHz)."""
        if not _PSUTIL:
            return 0.0, 0.0
        try:
            f = _ps.cpu_freq()
            if f:
                return round(f.current / 1000, 2), round(f.max / 1000, 2)
        except Exception:
            pass
        return 0.0, 0.0

    def cpu_info(self) -> Dict:
        name = "Unknown CPU"
        cores_p = _ps.cpu_count(logical=False) or 0 if _PSUTIL else 0
        cores_l = _ps.cpu_count(logical=True)  or 0 if _PSUTIL else 0
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
            name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except Exception:
            pass
        return {"name": name, "physical": cores_p, "logical": cores_l}

    # ── RAM ───────────────────────────────────────────────────────────────────

    def ram(self) -> Tuple[float, float, float]:
        """(used_GB, total_GB, percent)."""
        if not _PSUTIL:
            return 0.0, 0.0, 0.0
        m = _ps.virtual_memory()
        return m.used / 1e9, m.total / 1e9, m.percent

    # ── Disk ──────────────────────────────────────────────────────────────────

    def disks(self) -> List[Dict]:
        if not _PSUTIL:
            return []
        result = []
        for part in _ps.disk_partitions():
            try:
                u = _ps.disk_usage(part.mountpoint)
                result.append({
                    "device":    part.device,
                    "mount":     part.mountpoint,
                    "used_gb":   u.used  / 1e9,
                    "total_gb":  u.total / 1e9,
                    "percent":   u.percent,
                })
            except Exception:
                pass
        return result

    def disk_io(self) -> Tuple[float, float]:
        """(read_MB/s, write_MB/s) since last call."""
        if not _PSUTIL:
            return 0.0, 0.0
        if not hasattr(self, "_disk_t"):
            c = _ps.disk_io_counters()
            self._disk_t = time.time()
            self._disk_r = c.read_bytes if c else 0
            self._disk_w = c.write_bytes if c else 0
            return 0.0, 0.0
        try:
            c = _ps.disk_io_counters()
            if not c:
                return 0.0, 0.0
            now = time.time()
            dt = now - self._disk_t or 1
            rb = (c.read_bytes  - self._disk_r) / dt / 1e6
            wb = (c.write_bytes - self._disk_w) / dt / 1e6
            self._disk_t = now
            self._disk_r = c.read_bytes
            self._disk_w = c.write_bytes
            return max(0, rb), max(0, wb)
        except Exception:
            return 0.0, 0.0

    # ── Network ───────────────────────────────────────────────────────────────

    def network(self) -> Tuple[float, float]:
        """(KB/s sent, KB/s received) since last call."""
        if not _PSUTIL:
            return 0.0, 0.0
        if not hasattr(self, "_net_t"):
            c = _ps.net_io_counters()
            self._net_t = time.time()
            self._net_s = c.bytes_sent
            self._net_r = c.bytes_recv
            return 0.0, 0.0
        try:
            c = _ps.net_io_counters()
            now = time.time()
            dt = now - self._net_t or 1
            ks = (c.bytes_sent - self._net_s) / dt / 1024
            kr = (c.bytes_recv - self._net_r) / dt / 1024
            self._net_t = now
            self._net_s = c.bytes_sent
            self._net_r = c.bytes_recv
            return max(0, ks), max(0, kr)
        except Exception:
            return 0.0, 0.0

    # ── GPU ───────────────────────────────────────────────────────────────────
    # Probed once: _nvidia_smi = True/False/None(=not yet checked)
    _nvidia_smi: Optional[bool] = None

    def gpu_percent(self) -> Optional[float]:
        """GPU utilisation %.  Tries nvidia-smi first, then WMI. None = unavailable."""
        # ── nvidia-smi path (NVIDIA GPUs) ──
        if self._nvidia_smi is not False:
            smi = _find_nvidia_smi()
            if smi:
                try:
                    r = subprocess.run(
                        [smi, "--query-gpu=utilization.gpu",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=4,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if r.returncode == 0:
                        PerformanceMonitor._nvidia_smi = True
                        return min(float(r.stdout.strip().split()[0]), 100.0)
                except Exception:
                    pass
            PerformanceMonitor._nvidia_smi = False

        # ── WMI fallback (AMD / Intel integrated) ──
        ps = (
            "$v=(Get-CimInstance -Namespace 'root/CIMV2' "
            "-Class Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine "
            "-ErrorAction SilentlyContinue | "
            "Where-Object{$_.Name -like '*engtype_3D*'} | "
            "Measure-Object UtilizationPercentage -Sum).Sum; "
            "if($v -eq $null){'none'}else{$v}"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=4,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            out = r.stdout.strip()
            if out and out != "none":
                return min(float(out), 100.0)
        except Exception:
            pass
        return None

    def gpu_details(self) -> Dict:
        """
        Returns dict with name, vram_total_gb, vram_used_gb, temp_c
        (best-effort; values default to 0 if unavailable).
        """
        result = {"name": "Unknown GPU", "vram_total_gb": 0.0,
                  "vram_used_gb": 0.0, "temp_c": 0}
        if self._nvidia_smi is not False:
            smi = _find_nvidia_smi()
            if smi:
                try:
                    r = subprocess.run(
                        [smi,
                         "--query-gpu=name,memory.total,memory.used,temperature.gpu",
                         "--format=csv,noheader,nounits"],
                        capture_output=True, text=True, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if r.returncode == 0:
                        parts = [p.strip() for p in r.stdout.strip().split(",")]
                        if len(parts) >= 4:
                            result["name"]          = parts[0]
                            result["vram_total_gb"] = round(float(parts[1]) / 1024, 1)
                            result["vram_used_gb"]  = round(float(parts[2]) / 1024, 1)
                            result["temp_c"]        = int(parts[3])
                        return result
                except Exception:
                    pass
        # WMI fallback for name + VRAM
        ps = (
            "$c=Get-CimInstance -Class Win32_VideoController -ErrorAction SilentlyContinue "
            "| Select-Object -First 1; "
            "if($c){''+$c.Caption+'|'+[math]::Round($c.AdapterRAM/1GB,1)}else{'Unknown|0'}"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            parts = r.stdout.strip().split("|")
            result["name"]          = parts[0].strip()
            result["vram_total_gb"] = float(parts[1]) if len(parts) > 1 else 0.0
        except Exception:
            pass
        return result

    def gpu_info(self) -> Dict:
        d = self.gpu_details()
        return {"name": d["name"], "vram_gb": d["vram_total_gb"]}

    # ── CPU Temperature ───────────────────────────────────────────────────────

    def cpu_temp(self) -> Optional[float]:
        """CPU temperature in °C via WMI ACPI thermal zones (best-effort)."""
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                 '$t=(Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature'
                 ' -ErrorAction SilentlyContinue | Select-Object -First 1).CurrentTemperature;'
                 'if($t -ne $null){[math]::Round($t/10-273.15,1)}else{""}'],
                capture_output=True, text=True, timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            val = r.stdout.strip()
            if val:
                return float(val)
        except Exception:
            pass
        return None
