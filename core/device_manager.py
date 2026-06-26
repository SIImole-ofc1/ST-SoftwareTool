import subprocess
import json
from collections import defaultdict
from typing import List, Tuple

# Device classes that can never be disabled
_PROTECTED_CLASSES = frozenset({
    'Processor', 'System', 'Computer', 'SCSIAdapter',
    'Volume', 'DiskDrive',
})

# Name keywords that lock a device as protected
_PROTECTED_KEYS = (
    # CPUs
    'processor', 'ryzen', 'core i', 'core(tm)', 'xeon', 'athlon', 'threadripper',
    # GPUs
    'nvidia', 'geforce', 'rtx ', 'gtx ', 'radeon', ' rx ', 'arc graphics',
    'intel(r) iris', 'intel(r) uhd', 'amd radeon', 'quadro',
    # Display
    'generic pnp monitor', 'generic monitor', 'pnp monitor',
    # Core bus / system
    'pci express root', 'pci bus', 'acpi x64', 'acpi-compliant',
    'high definition audio controller', 'system board',
    'numeric data processor', 'direct memory access',
    'microsoft acpi-compliant', 'motherboard',
)

# Human-readable labels for PnP class names
CLASS_LABELS = {
    'AudioEndpoint':   'Audio inputs and outputs',
    'MEDIA':           'Sound, video and game controllers',
    'Bluetooth':       'Bluetooth',
    'DiskDrive':       'Disk drives',
    'Display':         'Display adapters',
    'HDC':             'IDE ATA/ATAPI controllers',
    'HIDClass':        'Human Interface Devices',
    'Keyboard':        'Keyboards',
    'Monitor':         'Monitors',
    'Mouse':           'Mice and other pointing devices',
    'Net':             'Network adapters',
    'Ports':           'Ports (COM & LPT)',
    'PrintQueue':      'Print queues',
    'Printer':         'Printers',
    'Processor':       'Processors',
    'SCSIAdapter':     'Storage controllers',
    'System':          'System devices',
    'USB':             'Universal Serial Bus controllers',
    'USBDevice':       'USB devices',
    'Sensor':          'Sensors',
    'Camera':          'Cameras',
    'Image':           'Imaging devices',
    'Biometric':       'Biometric devices',
    'Battery':         'Batteries',
    'Volume':          'Volumes',
    'WPD':             'Portable devices',
    'SoftwareDevice':  'Software devices',
    'Extension':       'Extension devices',
    'FDC':             'Floppy disk controllers',
}


class Device:
    def __init__(self, instance_id: str, name: str, class_name: str,
                 status: str, present: bool, manufacturer: str = ""):
        self.instance_id  = instance_id
        self.name         = name
        self.class_name   = class_name
        self.status       = status        # "OK", "Unknown", "Error", "Degraded"
        self.present      = present
        self.manufacturer = manufacturer
        self.protected    = self._check_protected()

    def _check_protected(self) -> bool:
        if self.class_name in _PROTECTED_CLASSES:
            return True
        n = self.name.lower()
        return any(k in n for k in _PROTECTED_KEYS)

    @property
    def class_label(self) -> str:
        return CLASS_LABELS.get(self.class_name, self.class_name or "Other devices")

    @property
    def is_working(self) -> bool:
        return self.status == "OK"

    @property
    def status_display(self) -> str:
        if self.status == "OK":
            return "Working"
        if self.status == "Unknown":
            return "Disabled / Unknown"
        if self.status == "Error":
            return "Error"
        if self.status == "Degraded":
            return "Degraded"
        return self.status or "Unknown"

    def detail_text(self) -> str:
        return (
            f"Name:\n  {self.name}\n\n"
            f"Class:\n  {self.class_label}\n\n"
            f"Status:       {self.status_display}\n"
            f"Present:      {'Yes' if self.present else 'No (disconnected)'}\n"
            f"Manufacturer: {self.manufacturer or '(unknown)'}\n"
            f"Protected:    {'Yes — cannot be disabled' if self.protected else 'No'}\n\n"
            f"Instance ID:\n  {self.instance_id}"
        )


class DeviceManager:
    def get_devices(self) -> List[Device]:
        """Query all PnP devices via PowerShell."""
        ps = r"""
$ErrorActionPreference = 'SilentlyContinue'
Get-PnpDevice -ErrorAction SilentlyContinue |
    Select-Object InstanceId, FriendlyName, Class, Status, Present, Manufacturer |
    ConvertTo-Json -Compress -Depth 2
"""
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                capture_output=True, text=True, timeout=40,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            raw = r.stdout.strip()
            if not raw:
                return []
            data = json.loads(raw)
            if isinstance(data, dict):
                data = [data]
        except Exception:
            return []

        devices: List[Device] = []
        seen: set = set()
        for item in data:
            name = (item.get('FriendlyName') or '').strip()
            if not name or name in seen:
                continue
            seen.add(name)
            devices.append(Device(
                instance_id  = (item.get('InstanceId')    or '').strip(),
                name         = name,
                class_name   = (item.get('Class')         or '').strip(),
                status       = (item.get('Status')        or 'Unknown').strip(),
                present      = bool(item.get('Present', True)),
                manufacturer = (item.get('Manufacturer')  or '').strip(),
            ))

        return sorted(devices, key=lambda d: (d.class_label.lower(), d.name.lower()))

    def grouped(self, devices: List[Device]):
        """Return {class_label: [Device, ...]} dict."""
        groups: dict = defaultdict(list)
        for d in devices:
            groups[d.class_label].append(d)
        return dict(sorted(groups.items()))

    def disable_device(self, instance_id: str) -> Tuple[bool, str]:
        ps = f'Disable-PnpDevice -InstanceId "{instance_id}" -Confirm:$false -ErrorAction Stop'
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                return True, "Device disabled."
            return False, (r.stderr or r.stdout or "Unknown error").strip()
        except Exception as e:
            return False, str(e)

    def enable_device(self, instance_id: str) -> Tuple[bool, str]:
        ps = f'Enable-PnpDevice -InstanceId "{instance_id}" -Confirm:$false -ErrorAction Stop'
        try:
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                capture_output=True, text=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                return True, "Device enabled."
            return False, (r.stderr or r.stdout or "Unknown error").strip()
        except Exception as e:
            return False, str(e)
