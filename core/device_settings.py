"""Device Settings backend — display, mouse, audio, NVIDIA, power, privacy."""
from __future__ import annotations
import ctypes, ctypes.wintypes as wt, os, re, subprocess, winreg
from typing import List, Tuple, Optional, Dict

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# ── Display / DEVMODEW ────────────────────────────────────────────────────────

DM_PELSWIDTH         = 0x00080000
DM_PELSHEIGHT        = 0x00100000
DM_DISPLAYFREQUENCY  = 0x00400000
DM_DISPLAYORIENTATION= 0x00000080
ENUM_CURRENT_SETTINGS= 0xFFFFFFFF
CDS_UPDATEREGISTRY   = 0x00000001
CDS_TEST             = 0x00000002
DISP_CHANGE_SUCCESSFUL = 0

class _DEVMODEW(ctypes.Structure):
    _fields_ = [
        ('dmDeviceName',          ctypes.c_wchar * 32),
        ('dmSpecVersion',         ctypes.c_uint16),
        ('dmDriverVersion',       ctypes.c_uint16),
        ('dmSize',                ctypes.c_uint16),
        ('dmDriverExtra',         ctypes.c_uint16),
        ('dmFields',              ctypes.c_uint32),
        ('dmPositionX',           ctypes.c_int32),
        ('dmPositionY',           ctypes.c_int32),
        ('dmDisplayOrientation',  ctypes.c_uint32),
        ('dmDisplayFixedOutput',  ctypes.c_uint32),
        ('dmColor',               ctypes.c_int16),
        ('dmDuplex',              ctypes.c_int16),
        ('dmYResolution',         ctypes.c_int16),
        ('dmTTOption',            ctypes.c_int16),
        ('dmCollate',             ctypes.c_int16),
        ('dmFormName',            ctypes.c_wchar * 32),
        ('dmLogPixels',           ctypes.c_uint16),
        ('dmBitsPerPel',          ctypes.c_uint32),
        ('dmPelsWidth',           ctypes.c_uint32),
        ('dmPelsHeight',          ctypes.c_uint32),
        ('dmDisplayFlags',        ctypes.c_uint32),
        ('dmDisplayFrequency',    ctypes.c_uint32),
        ('dmICMMethod',           ctypes.c_uint32),
        ('dmICMIntent',           ctypes.c_uint32),
        ('dmMediaType',           ctypes.c_uint32),
        ('dmDitherType',          ctypes.c_uint32),
        ('dmReserved1',           ctypes.c_uint32),
        ('dmReserved2',           ctypes.c_uint32),
        ('dmPanningWidth',        ctypes.c_uint32),
        ('dmPanningHeight',       ctypes.c_uint32),
    ]

ORIENT_LABELS = {0: 'Landscape', 1: 'Portrait', 2: 'Landscape (Flipped)', 3: 'Portrait (Flipped)'}

def _current_devmode() -> Optional[_DEVMODEW]:
    dm = _DEVMODEW()
    dm.dmSize = ctypes.sizeof(_DEVMODEW)
    if user32.EnumDisplaySettingsW(None, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return dm
    return None

def get_display_info() -> Dict:
    dm = _current_devmode()
    if not dm:
        return {}
    return {
        'width': dm.dmPelsWidth, 'height': dm.dmPelsHeight,
        'hz': dm.dmDisplayFrequency, 'orientation': dm.dmDisplayOrientation,
    }

def enum_resolutions() -> List[Tuple[int, int, int]]:
    """Return sorted unique (width, height, hz) triples supported by the primary display."""
    seen = set()
    results = []
    dm = _DEVMODEW()
    dm.dmSize = ctypes.sizeof(_DEVMODEW)
    i = 0
    while user32.EnumDisplaySettingsW(None, i, ctypes.byref(dm)):
        key = (dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency)
        if key not in seen and dm.dmBitsPerPel >= 24:
            seen.add(key)
            results.append(key)
        i += 1
    results.sort(key=lambda t: (t[0] * t[1], t[2]))
    return results

def set_resolution(width: int, height: int, hz: int) -> Tuple[bool, str]:
    dm = _DEVMODEW()
    dm.dmSize   = ctypes.sizeof(_DEVMODEW)
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT | DM_DISPLAYFREQUENCY
    dm.dmPelsWidth  = width
    dm.dmPelsHeight = height
    dm.dmDisplayFrequency = hz
    if user32.ChangeDisplaySettingsW(ctypes.byref(dm), CDS_TEST) != DISP_CHANGE_SUCCESSFUL:
        return False, 'Mode not supported by display.'
    r = user32.ChangeDisplaySettingsW(ctypes.byref(dm), CDS_UPDATEREGISTRY)
    return (True, 'Resolution changed.') if r == DISP_CHANGE_SUCCESSFUL else (False, f'ChangeDisplaySettings error {r}')

def set_orientation(orient: int) -> Tuple[bool, str]:
    dm = _current_devmode()
    if not dm:
        return False, 'Cannot read current display mode.'
    dm.dmFields = DM_DISPLAYORIENTATION
    dm.dmDisplayOrientation = orient
    r = user32.ChangeDisplaySettingsW(ctypes.byref(dm), CDS_UPDATEREGISTRY)
    return (True, 'Orientation changed.') if r == DISP_CHANGE_SUCCESSFUL else (False, f'Error {r}')

# ── Mouse / Cursor ────────────────────────────────────────────────────────────

SPI_GETMOUSESPEED   = 0x0070
SPI_SETMOUSESPEED   = 0x0071
SPI_GETMOUSETRAILS  = 0x005E
SPI_SETMOUSETRAILS  = 0x005F
SPI_GETMOUSEVANISH  = 0x1020
SPI_SETMOUSEVANISH  = 0x1021
SPI_GETMOUSESONAR   = 0x101C
SPI_SETMOUSESONAR   = 0x101D
SPI_GETMOUSE        = 0x0003
SPI_SETMOUSE        = 0x0004
SPI_SETCURSORS      = 0x0057
SPIF_UPDATEINIFILE  = 0x01
SPIF_SENDCHANGE     = 0x02
SPIF_BOTH           = SPIF_UPDATEINIFILE | SPIF_SENDCHANGE

def get_mouse_speed() -> int:
    val = ctypes.c_int(0)
    user32.SystemParametersInfoW(SPI_GETMOUSESPEED, 0, ctypes.byref(val), 0)
    return val.value  # 1-20

def set_mouse_speed(speed: int) -> bool:
    speed = max(1, min(20, speed))
    return bool(user32.SystemParametersInfoW(SPI_SETMOUSESPEED, 0, ctypes.c_void_p(speed), SPIF_BOTH))

def get_enhance_precision() -> bool:
    arr = (ctypes.c_int * 3)(0, 0, 0)
    user32.SystemParametersInfoW(SPI_GETMOUSE, 0, arr, 0)
    return arr[2] != 0

def set_enhance_precision(enabled: bool) -> bool:
    arr = (ctypes.c_int * 3)(6, 10, 1 if enabled else 0)
    return bool(user32.SystemParametersInfoW(SPI_SETMOUSE, 0, arr, SPIF_BOTH))

def get_mouse_trails() -> int:
    val = ctypes.c_int(0)
    user32.SystemParametersInfoW(SPI_GETMOUSETRAILS, 0, ctypes.byref(val), 0)
    return val.value  # 0=off, 2-7=on with length

def set_mouse_trails(length: int) -> bool:
    return bool(user32.SystemParametersInfoW(SPI_SETMOUSETRAILS, length, None, SPIF_BOTH))

def get_mouse_vanish() -> bool:
    val = ctypes.c_bool(False)
    user32.SystemParametersInfoW(SPI_GETMOUSEVANISH, 0, ctypes.byref(val), 0)
    return val.value

def set_mouse_vanish(enabled: bool) -> bool:
    return bool(user32.SystemParametersInfoW(SPI_SETMOUSEVANISH, 0, ctypes.c_void_p(int(enabled)), SPIF_BOTH))

def get_mouse_sonar() -> bool:
    val = ctypes.c_bool(False)
    user32.SystemParametersInfoW(SPI_GETMOUSESONAR, 0, ctypes.byref(val), 0)
    return val.value

def set_mouse_sonar(enabled: bool) -> bool:
    return bool(user32.SystemParametersInfoW(SPI_SETMOUSESONAR, 0, ctypes.c_void_p(int(enabled)), SPIF_BOTH))

def get_cursor_size() -> int:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'SOFTWARE\Microsoft\Accessibility') as k:
            v, _ = winreg.QueryValueEx(k, 'CursorSize')
            return int(v)
    except Exception:
        return 1

def set_cursor_size(size: int) -> bool:
    size = max(1, min(15, size))
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                r'SOFTWARE\Microsoft\Accessibility',
                                access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, 'CursorSize', 0, winreg.REG_DWORD, size)
        # Accessibility scaling only works when cursor paths are empty and
        # Scheme Source = 1.  With file paths present Windows loads the
        # fixed-size .cur bitmaps and CursorBaseSize is ignored.
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                r'Control Panel\Cursors',
                                access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, 'CursorBaseSize', 0, winreg.REG_DWORD, size * 32)
            winreg.SetValueEx(k, 'Scheme Source',  0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(k, '',               0, winreg.REG_SZ,    '')
            for cursor_key in _CURSOR_KEYS:
                winreg.SetValueEx(k, cursor_key, 0, winreg.REG_SZ, '')
        user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        user32.SendNotifyMessageW(HWND_BROADCAST, WM_SETTINGCHANGE, 0, 'Accessibility')
        return True
    except Exception:
        return False

_CURSOR_SCHEMES = [
    'Windows Default', 'Windows Default (large)', 'Windows Default (extra large)',
    'Windows Black', 'Windows Black (large)', 'Windows Black (extra large)',
    'Windows Inverted', 'Windows Inverted (large)', 'Windows Inverted (extra large)',
]

# Cursor file CSVs for built-in aero schemes (in _CURSOR_KEYS order).
# None = scheme doesn't exist on this install.  Empty string per position = use system default.
_AERO_BASE = r'%SystemRoot%\Cursors'
def _builtin_scheme_csv(name: str) -> Optional[str]:
    """Return comma-separated cursor file list for a built-in scheme, or None if not available."""
    # (Arrow, Help, AppStarting, Wait, Crosshair, IBeam, NWPen, No,
    #  SizeNS, SizeWE, SizeNWSE, SizeNESW, SizeAll, UpArrow, Hand)
    _B = _AERO_BASE
    variants = {
        'Windows Default': (
            f'{_B}\\aero_arrow.cur,{_B}\\aero_helpsel.cur,{_B}\\aero_working.ani,'
            f'{_B}\\aero_busy.ani,,,{_B}\\aero_pen.cur,{_B}\\aero_unavail.cur,'
            f'{_B}\\aero_ns.cur,{_B}\\aero_ew.cur,{_B}\\aero_nwse.cur,'
            f'{_B}\\aero_nesw.cur,{_B}\\aero_move.cur,{_B}\\aero_up.cur,{_B}\\aero_link.cur'
        ),
        'Windows Default (large)': (
            f'{_B}\\aero_arrow_l.cur,{_B}\\aero_helpsel_l.cur,{_B}\\aero_working_l.ani,'
            f'{_B}\\aero_busy_l.ani,,,{_B}\\aero_pen_l.cur,{_B}\\aero_unavail_l.cur,'
            f'{_B}\\aero_ns_l.cur,{_B}\\aero_ew_l.cur,{_B}\\aero_nwse_l.cur,'
            f'{_B}\\aero_nesw_l.cur,{_B}\\aero_move_l.cur,{_B}\\aero_up_l.cur,{_B}\\aero_link_l.cur'
        ),
        'Windows Default (extra large)': (
            f'{_B}\\aero_arrow_xl.cur,{_B}\\aero_helpsel_xl.cur,{_B}\\aero_working_xl.ani,'
            f'{_B}\\aero_busy_xl.ani,,,{_B}\\aero_pen_xl.cur,{_B}\\aero_unavail_xl.cur,'
            f'{_B}\\aero_ns_xl.cur,{_B}\\aero_ew_xl.cur,{_B}\\aero_nwse_xl.cur,'
            f'{_B}\\aero_nesw_xl.cur,{_B}\\aero_move_xl.cur,{_B}\\aero_up_xl.cur,{_B}\\aero_link_xl.cur'
        ),
    }
    csv = variants.get(name)
    if csv is None:
        return None
    # Verify at least the Arrow file exists
    arrow = os.path.expandvars(csv.split(',')[0])
    return csv if os.path.exists(arrow) else None

def get_cursor_scheme() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\Cursors') as k:
            v, _ = winreg.QueryValueEx(k, '')
            return str(v)
    except Exception:
        return ''

def list_cursor_schemes() -> List[str]:
    """Return all available cursor scheme names.  Built-in aero variants first,
    then user-installed schemes from the registry."""
    # Built-in aero variants that always exist on Windows 10/11
    builtins = [s for s in _CURSOR_SCHEMES
                if _builtin_scheme_csv(s) is not None]
    schemes: List[str] = list(builtins)
    # Schemes registered by cursor pack installers
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Control Panel\Cursors\Schemes') as k:
            i = 0
            while True:
                try:
                    name, _, _ = winreg.EnumValue(k, i)
                    if name and name not in schemes:
                        schemes.append(name)
                    i += 1
                except OSError:
                    break
    except Exception:
        pass
    return schemes

# Ordered exactly as Windows stores cursors in the Cursor Schemes CSV:
# Arrow,Help,AppStarting,Wait,Crosshair,IBeam,NWPen,No,SizeNS,SizeWE,
# SizeNWSE,SizeNESW,SizeAll,UpArrow,Hand
_CURSOR_KEYS = [
    'Arrow', 'Help', 'AppStarting', 'Wait', 'Crosshair', 'IBeam',
    'NWPen', 'No', 'SizeNS', 'SizeWE',
    'SizeNWSE', 'SizeNESW', 'SizeAll', 'UpArrow', 'Hand',
]

def set_cursor_scheme(name: str) -> bool:
    """Apply a named cursor scheme.  Priority: built-in aero → registry Cursors\\Schemes."""
    raw: Optional[str] = None

    # 1. Try built-in aero scheme definition (always available for Default/Large/XL)
    raw = _builtin_scheme_csv(name)

    # 2. Try user-installed scheme from the correct registry path
    if raw is None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Control Panel\Cursors\Schemes') as k:
                raw, _ = winreg.QueryValueEx(k, name)
        except Exception:
            pass

    # 3. Apply: write individual cursor values and call SPI_SETCURSORS
    try:
        if raw is not None:
            files = [os.path.expandvars(f.strip()) for f in raw.split(',')]
        else:
            files = []  # unknown scheme — clear to system default
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, r'Control Panel\Cursors',
                                access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, '', 0, winreg.REG_SZ, name)
            for i, cursor_key in enumerate(_CURSOR_KEYS):
                val = files[i] if i < len(files) else ''
                winreg.SetValueEx(k, cursor_key, 0, winreg.REG_SZ, val)
        user32.SystemParametersInfoW(SPI_SETCURSORS, 0, None, SPIF_SENDCHANGE)
        return True
    except Exception:
        return False

# ── Audio ─────────────────────────────────────────────────────────────────────
#
# Uses CoCreateInstance + Marshal.GetObjectForIUnknown so the COM object can
# be properly cast to the interface.  Single-quoted PS here-string (@'...'@)
# avoids all escaping concerns for the C# GUID string literals.
#
_PS_AUDIO_TYPE = """\
Add-Type -Language CSharp -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IAudioEndpointVolume {
    void RegisterControlChangeNotify(IntPtr n);
    void UnregisterControlChangeNotify(IntPtr n);
    void GetChannelCount(out uint c);
    void SetMasterVolumeLevel(float f, Guid g);
    void SetMasterVolumeLevelScalar(float f, Guid g);
    void GetMasterVolumeLevel(out float f);
    void GetMasterVolumeLevelScalar(out float f);
    void SetChannelVolumeLevel(uint n, float f, Guid g);
    void SetChannelVolumeLevelScalar(uint n, float f, Guid g);
    void GetChannelVolumeLevel(uint n, out float f);
    void GetChannelVolumeLevelScalar(uint n, out float f);
    void SetMute([MarshalAs(UnmanagedType.Bool)] bool b, Guid g);
    void GetMute([MarshalAs(UnmanagedType.Bool)] out bool b);
}
[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
    void Activate(ref Guid iid, uint ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o);
    void OpenPropertyStore(uint a, out IntPtr p);
    void GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    void GetState(out uint s);
}
[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator {
    void EnumAudioEndpoints(uint f, uint m, out IntPtr d);
    void GetDefaultAudioEndpoint(uint f, uint r, out IMMDevice d);
}
public static class VolumeControl {
    [DllImport("ole32.dll")]
    private static extern int CoCreateInstance(ref Guid rclsid, IntPtr pUnkOuter,
        uint dwClsCtx, ref Guid riid, out IntPtr ppv);

    private static IAudioEndpointVolume GetEp() {
        Guid clsid = new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E");
        Guid iid   = new Guid("A95664D2-9614-4F35-A746-DE8DB63617E6");
        IntPtr ptr; int hr = CoCreateInstance(ref clsid, IntPtr.Zero, 1, ref iid, out ptr);
        if (hr != 0) throw new Exception("CoCreateInstance 0x" + hr.ToString("X8"));
        var e = (IMMDeviceEnumerator)Marshal.GetObjectForIUnknown(ptr);
        Marshal.Release(ptr);
        IMMDevice dev; e.GetDefaultAudioEndpoint(0, 0, out dev);
        Guid ep = new Guid("5CDF2C82-841E-4546-9722-0CF74078229A");
        object raw; dev.Activate(ref ep, 23, IntPtr.Zero, out raw);
        return (IAudioEndpointVolume)raw;
    }
    public static float GetVolume() { float f = 0; GetEp().GetMasterVolumeLevelScalar(out f); return f; }
    public static void  SetVolume(float v) { GetEp().SetMasterVolumeLevelScalar(v, Guid.Empty); }
    public static bool  GetMute()   { bool m = false; GetEp().GetMute(out m); return m; }
    public static void  SetMute(bool muted) { GetEp().SetMute(muted, Guid.Empty); }
}
'@
"""

def _audio_ps(body: str, timeout: int = 15) -> str:
    """Run a PS script with VolumeControl type pre-loaded."""
    script = "$ErrorActionPreference = 'Stop'\n" + _PS_AUDIO_TYPE + body
    return _ps(script, timeout)

def _ps(script: str, timeout: int = 10) -> str:
    # Write to a temp .ps1 file so heredoc @"..."@ terminators are
    # always at column 0 — passing multiline scripts via -Command
    # breaks heredoc parsing on Windows PowerShell 5.1.
    import tempfile, os as _os
    fd, path = tempfile.mkstemp(suffix='.ps1')
    try:
        _os.write(fd, script.encode('utf-8-sig'))
        _os.close(fd)
        r = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-File', path],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return r.stdout.strip()
    finally:
        try:
            _os.unlink(path)
        except Exception:
            pass

def get_volume_state() -> dict:
    """Read volume % and mute flag in one PS process. Returns {'volume': int, 'muted': bool}."""
    try:
        out = _audio_ps(
            '[int]([VolumeControl]::GetVolume()*100+0.5)\n'
            'if([VolumeControl]::GetMute()){"1"}else{"0"}'
        )
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        vol  = int(lines[0]) if lines else 50
        mute = lines[1] == '1' if len(lines) > 1 else False
        return {'volume': vol, 'muted': mute}
    except Exception:
        return {'volume': 50, 'muted': False}

def get_master_volume() -> int:
    return get_volume_state()['volume']

def set_master_volume(level: int) -> bool:
    level = max(0, min(100, level))
    try:
        _audio_ps(f'[VolumeControl]::SetVolume([float]{level / 100.0})')
        return True
    except Exception:
        return False

def get_mute() -> bool:
    try:
        out = _audio_ps('if([VolumeControl]::GetMute()){"1"}else{"0"}')
        return out.strip() == '1'
    except Exception:
        return False

def set_mute(muted: bool) -> bool:
    v = '$true' if muted else '$false'
    try:
        _audio_ps(f'[VolumeControl]::SetMute({v})')
        return True
    except Exception:
        return False

def get_audio_devices() -> List[Dict]:
    ps = r"""
$ErrorActionPreference='SilentlyContinue'
Get-CimInstance Win32_SoundDevice | Select-Object Name,Manufacturer,Status,DeviceID |
    ConvertTo-Json -Compress
"""
    try:
        import json
        out = _ps(ps, 15)
        data = json.loads(out)
        if isinstance(data, dict): data = [data]
        return [{'name': d.get('Name',''), 'manufacturer': d.get('Manufacturer',''),
                 'status': d.get('Status','')} for d in data]
    except Exception:
        return []

# ── NVIDIA ────────────────────────────────────────────────────────────────────

def find_nvidia_smi() -> Optional[str]:
    for p in [
        r'C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe',
        r'C:\Windows\System32\nvidia-smi.exe',
    ]:
        if os.path.exists(p):
            return p
    import shutil
    return shutil.which('nvidia-smi')

def get_nvidia_info() -> Optional[Dict]:
    smi = find_nvidia_smi()
    if not smi:
        return None
    try:
        r = subprocess.run(
            [smi, '--query-gpu=name,temperature.gpu,utilization.gpu,utilization.memory,'
                  'memory.total,memory.used,power.draw,power.limit,clocks.current.graphics,'
                  'clocks.current.memory,driver_version,fan.speed',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0:
            return None
        parts = [p.strip() for p in r.stdout.strip().split(',')]
        if len(parts) < 11:
            return None
        fan_raw = parts[11].strip() if len(parts) > 11 else 'N/A'
        fan_str = f'{fan_raw} %' if fan_raw not in ('N/A', '[N/A]', '') else 'N/A'
        return {
            'name': parts[0], 'temp': parts[1], 'gpu_util': parts[2],
            'mem_util': parts[3], 'mem_total': parts[4], 'mem_used': parts[5],
            'power_draw': parts[6], 'power_limit': parts[7],
            'clock_gpu': parts[8], 'clock_mem': parts[9], 'driver': parts[10],
            'fan': fan_str,
        }
    except Exception:
        return None

def get_nvidia_power_mode() -> str:
    smi = find_nvidia_smi()
    if not smi:
        return 'N/A'
    try:
        r = subprocess.run([smi, '--query-gpu=power.management', '--format=csv,noheader'],
                           capture_output=True, text=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.stdout.strip()
    except Exception:
        return 'N/A'

def _reg_get(hive, path: str, name: str, default=None):
    try:
        with winreg.OpenKey(hive, path) as k:
            v, _ = winreg.QueryValueEx(k, name)
            return v
    except Exception:
        return default

def _reg_set(hive, path: str, name: str, val, vtype=winreg.REG_DWORD) -> bool:
    try:
        with winreg.CreateKeyEx(hive, path, access=winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, name, 0, vtype, val)
        return True
    except Exception:
        return False

def get_nvidia_shader_cache() -> bool:
    v = _reg_get(winreg.HKEY_CURRENT_USER,
                 r'SOFTWARE\NVIDIA Corporation\Global\NVTweak', 'ShaderDiskCache', 1)
    return bool(v)

def set_nvidia_shader_cache(enabled: bool) -> bool:
    return _reg_set(winreg.HKEY_CURRENT_USER,
                    r'SOFTWARE\NVIDIA Corporation\Global\NVTweak',
                    'ShaderDiskCache', 1 if enabled else 0)

# ── Power ─────────────────────────────────────────────────────────────────────

def get_power_plans() -> List[Tuple[str, str]]:
    """Returns [(guid, name), ...] — name is clean, no * or () markers."""
    try:
        r = subprocess.run(['powercfg', '/list'], capture_output=True, text=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        plans = []
        for line in r.stdout.splitlines():
            m = re.search(
                r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'
                r'\s+\(([^)]*)\)',
                line, re.IGNORECASE,
            )
            if m:
                plans.append((m.group(1), m.group(2).strip()))
        return plans
    except Exception:
        return []

def get_active_power_plan() -> str:
    try:
        r = subprocess.run(['powercfg', '/getactivescheme'], capture_output=True, text=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        for part in r.stdout.split():
            if '-' in part and len(part) == 36:
                return part
        return ''
    except Exception:
        return ''

def set_power_plan(guid: str) -> bool:
    try:
        r = subprocess.run(['powercfg', '/setactive', guid], capture_output=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False

def get_hibernate() -> bool:
    try:
        r = subprocess.run(['powercfg', '/availablesleepstates'], capture_output=True, text=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return 'Hibernate' in r.stdout
    except Exception:
        return False

def set_hibernate(enabled: bool) -> bool:
    cmd = ['powercfg', '/hibernate', 'on' if enabled else 'off']
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False

def get_fast_startup() -> bool:
    v = _reg_get(winreg.HKEY_LOCAL_MACHINE,
                 r'SYSTEM\CurrentControlSet\Control\Session Manager\Power',
                 'HiberbootEnabled', 1)
    return bool(v)

def set_fast_startup(enabled: bool) -> bool:
    return _reg_set(winreg.HKEY_LOCAL_MACHINE,
                    r'SYSTEM\CurrentControlSet\Control\Session Manager\Power',
                    'HiberbootEnabled', 1 if enabled else 0)

def get_usb_selective_suspend() -> bool:
    v = _reg_get(winreg.HKEY_LOCAL_MACHINE,
                 r'SYSTEM\CurrentControlSet\Services\USB', 'DisableSelectiveSuspend', 0)
    return not bool(v)

def set_usb_selective_suspend(enabled: bool) -> bool:
    return _reg_set(winreg.HKEY_LOCAL_MACHINE,
                    r'SYSTEM\CurrentControlSet\Services\USB',
                    'DisableSelectiveSuspend', 0 if enabled else 1)

# ── Privacy & Hidden Windows Settings ─────────────────────────────────────────

HKLM = winreg.HKEY_LOCAL_MACHINE
HKCU = winreg.HKEY_CURRENT_USER

_PRIVACY_SETTINGS: Dict[str, Tuple] = {
    # key: (hive, regpath, value_name, enabled_val, disabled_val)
    'advertising_id':       (HKCU,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo',                    'Enabled',                   1, 0),
    'game_dvr':             (HKCU,  r'System\GameConfigStore',                                                        'GameDVR_Enabled',            1, 0),
    'game_bar':             (HKCU,  r'SOFTWARE\Microsoft\GameBar',                                                    'AutoGameModeEnabled',        1, 0),
    'background_apps':      (HKCU,  r'Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications',        'GlobalUserDisabled',          0, 1),
    'web_search_start':     (HKCU,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\Search',                             'BingSearchEnabled',           1, 0),
    'cortana':              (HKLM,  r'SOFTWARE\Policies\Microsoft\Windows\Windows Search',                           'AllowCortana',               1, 0),
    'activity_history':     (HKLM,  r'SOFTWARE\Policies\Microsoft\Windows\System',                                   'EnableActivityFeed',          1, 0),
    'delivery_optimization':(HKLM,  r'SOFTWARE\Policies\Microsoft\Windows\DeliveryOptimization',                    'DODownloadMode',              3, 0),
    'error_reporting':      (HKCU,  r'SOFTWARE\Microsoft\Windows\Windows Error Reporting',                           'Disabled',                   0, 1),
    'telemetry':            (HKLM,  r'SOFTWARE\Policies\Microsoft\Windows\DataCollection',                           'AllowTelemetry',             3, 0),
    'windows_tips':         (HKCU,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',             'SoftLandingEnabled',         1, 0),
    'lock_screen_ads':      (HKCU,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',             'RotatingLockScreenOverlayEnabled', 1, 0),
    'autoplay':             (HKLM,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer',                  'NoDriveTypeAutoRun',         0, 255),
    'numlock_startup':      (winreg.HKEY_USERS, r'.DEFAULT\Control Panel\Keyboard',                                  'InitialKeyboardIndicators',  '2', '0'),
    'fast_user_switching':  (HKLM,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System',                   'HideFastUserSwitching',      0, 1),
    'uac_prompt':           (HKLM,  r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System',                   'ConsentPromptBehaviorAdmin', 5, 0),
}

def get_privacy_setting(key: str) -> bool:
    if key not in _PRIVACY_SETTINGS:
        return False
    hive, path, name, on_val, off_val = _PRIVACY_SETTINGS[key]
    v = _reg_get(hive, path, name, on_val)
    return v == on_val

def set_privacy_setting(key: str, enabled: bool) -> bool:
    if key not in _PRIVACY_SETTINGS:
        return False
    hive, path, name, on_val, off_val = _PRIVACY_SETTINGS[key]
    val = on_val if enabled else off_val
    vtype = winreg.REG_SZ if isinstance(val, str) else winreg.REG_DWORD
    return _reg_set(hive, path, name, val, vtype)

def get_memory_compression() -> bool:
    try:
        r = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command',
             '(Get-MMAgent).MemoryCompression'],
            capture_output=True, text=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return r.stdout.strip().lower() == 'true'
    except Exception:
        return True

def set_memory_compression(enabled: bool) -> bool:
    cmd = 'Enable-MMAgent -MemoryCompression' if enabled else 'Disable-MMAgent -MemoryCompression'
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', cmd],
                           capture_output=True, text=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False

def get_superfetch() -> bool:
    v = _reg_get(HKLM, r'SYSTEM\CurrentControlSet\Services\SysMain', 'Start', 2)
    return v in (2, 3)

def set_superfetch(enabled: bool) -> bool:
    start_type = 2 if enabled else 4
    ok = _reg_set(HKLM, r'SYSTEM\CurrentControlSet\Services\SysMain', 'Start', start_type)
    if ok:
        sc = 'start' if enabled else 'stop'
        subprocess.run(['sc', sc, 'SysMain'], capture_output=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return ok

def open_nvidia_control_panel() -> bool:
    for p in [
        r'C:\Program Files\NVIDIA Corporation\Control Panel Client\nvcplui.exe',
        r'C:\Windows\System32\nvcplui.exe',
    ]:
        if os.path.exists(p):
            subprocess.Popen([p])
            return True
    try:
        os.startfile('nvcplui.exe')
        return True
    except Exception:
        return False

# ── Fan detection ─────────────────────────────────────────────────────────────

def get_fan_info() -> dict:
    """Detect fan speeds.
    Returns {'gpu_fan_pct': int|None, 'gpu_fan_na': bool, 'cpu_fans': [...], 'ohm_fans': [...]}
    """
    result: dict = {'gpu_fan_pct': None, 'gpu_fan_na': False, 'cpu_fans': [], 'ohm_fans': []}

    # GPU fan via nvidia-smi
    smi = find_nvidia_smi()
    if smi:
        try:
            r = subprocess.run(
                [smi, '--query-gpu=fan.speed', '--format=csv,noheader,nounits'],
                capture_output=True, text=True, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW)
            val = r.stdout.strip()
            if r.returncode == 0 and val:
                stripped = val.split()[0].lower().strip('[]')
                if stripped in ('n/a', 'na', ''):
                    result['gpu_fan_na'] = True
                else:
                    try:
                        result['gpu_fan_pct'] = int(float(stripped))
                    except ValueError:
                        result['gpu_fan_na'] = True
        except Exception:
            pass

    # System fans via OpenHardwareMonitor WMI namespace (if running)
    try:
        import json as _json
        ps_ohm = (
            '$s=Get-CimInstance -Namespace root/OpenHardwareMonitor -Class Sensor'
            ' -ErrorAction SilentlyContinue -Filter "SensorType=\'Fan\'";'
            'if($s){$s|Select-Object Name,Value|ConvertTo-Json -Compress}else{"[]"}'
        )
        r = subprocess.run(
            ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps_ohm],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0 and r.stdout.strip():
            data = _json.loads(r.stdout.strip())
            if isinstance(data, dict):
                data = [data]
            result['ohm_fans'] = [
                {'name': d.get('Name', 'Fan'), 'rpm': int(d.get('Value') or 0)}
                for d in (data or [])
            ]
    except Exception:
        pass

    # Fallback: WMI Win32_Fan (rarely populated on modern systems)
    if not result['ohm_fans']:
        try:
            import json as _json
            ps = (
                '$fans=Get-CimInstance -ClassName Win32_Fan -ErrorAction SilentlyContinue;'
                'if($fans){$fans|Select-Object Name,DesiredSpeed|ConvertTo-Json -Compress}'
                'else{"[]"}'
            )
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                capture_output=True, text=True, timeout=6,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0 and r.stdout.strip():
                data = _json.loads(r.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                result['cpu_fans'] = [
                    {'name': d.get('Name', 'Fan'), 'rpm': d.get('DesiredSpeed') or 0}
                    for d in (data or [])
                ]
        except Exception:
            pass

    return result

# ── Admin / elevation ─────────────────────────────────────────────────────────

def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def restart_as_admin() -> bool:
    """Re-launch this process elevated via UAC. Returns True if ShellExecute succeeded."""
    import sys
    if is_admin():
        return False  # already elevated
    try:
        script = os.path.abspath(sys.argv[0])
        cwd = os.path.dirname(script)

        # Write pythonw.exe path so ST.exe launcher can find the interpreter.
        exe_raw = sys.executable
        pythonw = exe_raw.replace('python.exe', 'pythonw.exe').replace('Python.exe', 'pythonw.exe')
        if not os.path.exists(pythonw):
            pythonw = exe_raw
        try:
            with open(os.path.join(cwd, 'python_path.txt'), 'w') as f:
                f.write(pythonw)
        except Exception:
            pass

        # Prefer ST.exe (has our icon so UAC dialog shows ST logo).
        # Fall back to pythonw.exe if the launcher hasn't been compiled yet.
        launcher = os.path.join(cwd, 'ST.exe')
        if os.path.exists(launcher):
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas', launcher, None, cwd, 1
            )
        else:
            parts = [f'"{script}"']
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas', pythonw, ' '.join(parts), cwd, 1
            )
        return int(ret) > 32
    except Exception:
        return False
